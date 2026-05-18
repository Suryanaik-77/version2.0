"""
redis.py — Redis connection pool and all typed operations.

Rules enforced here:
- All operations are async.
- No raw Redis keys outside this module — all key patterns defined as constants.
- Pipeline used for multi-key atomic operations.
- Separate connection pool for pub/sub (blocking) vs. regular ops.
- Every operation has an explicit timeout to prevent pool starvation.
"""
from __future__ import annotations

import json
import asyncio
from datetime import datetime
from typing import AsyncIterator

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.config import get_settings
from app.models.session import SessionState, SessionContext, CandidateMemory, InlineSignals

settings = get_settings()

# ── Key patterns (single source of truth) ─────────────────────────────────────

def _key_session_state(session_id: str) -> str:
    return f"session:{session_id}:state"

def _key_session_context(session_id: str) -> str:
    return f"session:{session_id}:context"

def _key_session_memory(session_id: str) -> str:
    return f"session:{session_id}:memory"

def _key_session_turns(session_id: str) -> str:
    return f"session:{session_id}:turns"

def _key_session_connections(session_id: str) -> str:
    return f"session:{session_id}:connections"

def _key_ws_connection(connection_id: str) -> str:
    return f"ws:{connection_id}:session"

def _key_inline_signals(session_id: str, turn_number: int) -> str:
    return f"session:{session_id}:inline:{turn_number}"

def _key_circuit_breaker(provider_name: str) -> str:
    return f"provider:{provider_name}:circuit"

def _channel_session_events(session_id: str) -> str:
    return f"session:{session_id}:events"

def _channel_anticheat_events(session_id: str) -> str:
    return f"anticheat:{session_id}:events"


# ── Connection pool ────────────────────────────────────────────────────────────

_pool: Redis | None = None
_pubsub_pool: Redis | None = None


async def init_redis() -> None:
    """Called at app startup. Creates connection pools."""
    global _pool, _pubsub_pool
    _pool = aioredis.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_POOL_MAX_SIZE,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        health_check_interval=30,
    )
    # Separate pool for pub/sub — these block their connection
    _pubsub_pool = aioredis.from_url(
        settings.REDIS_PUBSUB_URL,
        max_connections=50,
        decode_responses=True,
        socket_timeout=None,   # pub/sub connections are long-lived
        socket_connect_timeout=2.0,
    )
    # Verify connectivity
    await _pool.ping()
    await _pubsub_pool.ping()


async def close_redis() -> None:
    """Called at app shutdown."""
    if _pool:
        await _pool.aclose()
    if _pubsub_pool:
        await _pubsub_pool.aclose()


def _get_pool() -> Redis:
    if _pool is None:
        raise RuntimeError("Redis pool not initialized. Call init_redis() first.")
    return _pool


def _get_pubsub_pool() -> Redis:
    if _pubsub_pool is None:
        raise RuntimeError("Redis pubsub pool not initialized.")
    return _pubsub_pool


# ── Session state operations ───────────────────────────────────────────────────

async def set_session_state(state: SessionState) -> None:
    """Atomically write session state. Refreshes TTL."""
    r = _get_pool()
    key = _key_session_state(state.session_id)
    await r.setex(
        key,
        settings.SESSION_TTL,
        state.model_dump_json(),
    )


async def get_session_state(session_id: str) -> SessionState | None:
    r = _get_pool()
    raw = await r.get(_key_session_state(session_id))
    if raw is None:
        return None
    return SessionState.model_validate_json(raw)


async def update_session_mode(session_id: str, mode: str, turn_count: int | None = None) -> None:
    """
    Partial update — only writes mode (and optionally turn_count).
    Uses GET-modify-SET with optimistic lock to avoid full read-write cycles.
    """
    r = _get_pool()
    key = _key_session_state(session_id)
    # Lua script for atomic read-modify-write
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return nil end
    local state = cjson.decode(raw)
    state['mode'] = ARGV[1]
    if ARGV[2] ~= '' then
        state['turn_count'] = tonumber(ARGV[2])
    end
    state['last_turn_at'] = ARGV[3]
    redis.call('SETEX', KEYS[1], tonumber(ARGV[4]), cjson.encode(state))
    return 1
    """
    await r.eval(
        script, 1, key,
        mode,
        str(turn_count) if turn_count is not None else "",
        datetime.utcnow().isoformat(),
        settings.SESSION_TTL,
    )


async def touch_session(session_id: str) -> None:
    """Refresh TTL on active session — called each turn to keep session alive."""
    r = _get_pool()
    # Refresh all session keys atomically
    pipe = r.pipeline(transaction=False)
    for key_fn in [
        _key_session_state,
        _key_session_context,
        _key_session_memory,
        _key_session_turns,
    ]:
        pipe.expire(key_fn(session_id), settings.SESSION_TTL)
    await pipe.execute()


async def delete_session(session_id: str) -> None:
    """Remove all Redis keys for a session. Called after flush to Postgres."""
    r = _get_pool()
    keys = [
        _key_session_state(session_id),
        _key_session_context(session_id),
        _key_session_memory(session_id),
        _key_session_turns(session_id),
        _key_session_connections(session_id),
    ]
    if keys:
        await r.delete(*keys)


# ── Session context (hot-path — read every turn) ──────────────────────────────

async def set_session_context(ctx: SessionContext) -> None:
    r = _get_pool()
    await r.setex(
        _key_session_context(ctx.session_id),
        settings.SESSION_TTL,
        ctx.model_dump_json(),
    )


async def get_session_context(session_id: str) -> SessionContext | None:
    r = _get_pool()
    raw = await r.get(_key_session_context(session_id))
    if raw is None:
        return None
    return SessionContext.model_validate_json(raw)


# ── Candidate memory ──────────────────────────────────────────────────────────

async def set_memory(memory: CandidateMemory) -> None:
    r = _get_pool()
    await r.setex(
        _key_session_memory(memory.session_id),
        settings.SESSION_TTL,
        memory.model_dump_json(),
    )


async def get_memory(session_id: str) -> CandidateMemory | None:
    r = _get_pool()
    raw = await r.get(_key_session_memory(session_id))
    if raw is None:
        return None
    return CandidateMemory.model_validate_json(raw)


# ── Turn history (ring buffer, last 10) ───────────────────────────────────────

async def push_turn_summary(session_id: str, summary: dict) -> None:
    """Push to front of list. Trim to 10 most recent."""
    r = _get_pool()
    key = _key_session_turns(session_id)
    pipe = r.pipeline(transaction=False)
    pipe.lpush(key, json.dumps(summary))
    pipe.ltrim(key, 0, 9)   # keep last 10
    pipe.expire(key, settings.SESSION_TTL)
    await pipe.execute()


async def get_recent_turns(session_id: str, n: int = 3) -> list[dict]:
    """Return most recent n turn summaries."""
    r = _get_pool()
    raws = await r.lrange(_key_session_turns(session_id), 0, n - 1)
    return [json.loads(r) for r in raws]


# ── Inline signals (short-lived, consumed by strategy_engine) ────────────────

async def set_inline_signals(signals: InlineSignals) -> None:
    r = _get_pool()
    key = _key_inline_signals(signals.session_id, signals.turn_number)
    # TTL 60s — strategy_engine consumes this quickly after generation
    await r.setex(key, 60, signals.model_dump_json())


async def get_inline_signals(session_id: str, turn_number: int) -> InlineSignals | None:
    r = _get_pool()
    raw = await r.get(_key_inline_signals(session_id, turn_number))
    if raw is None:
        return None
    return InlineSignals.model_validate_json(raw)


# ── WebSocket connection registry ──────────────────────────────────────────────

async def register_connection(session_id: str, connection_id: str) -> None:
    r = _get_pool()
    pipe = r.pipeline(transaction=False)
    # Add to session's connection set
    pipe.sadd(_key_session_connections(session_id), connection_id)
    pipe.expire(_key_session_connections(session_id), settings.SESSION_TTL)
    # Map connection → session for reverse lookup
    pipe.setex(_key_ws_connection(connection_id), settings.SESSION_TTL, session_id)
    await pipe.execute()


async def unregister_connection(session_id: str, connection_id: str) -> None:
    r = _get_pool()
    pipe = r.pipeline(transaction=False)
    pipe.srem(_key_session_connections(session_id), connection_id)
    pipe.delete(_key_ws_connection(connection_id))
    await pipe.execute()


async def get_session_connections(session_id: str) -> set[str]:
    r = _get_pool()
    return await r.smembers(_key_session_connections(session_id))


async def heartbeat_connection(connection_id: str) -> None:
    """Refresh TTL on connection key — prevents stale detection."""
    r = _get_pool()
    await r.expire(_key_ws_connection(connection_id), settings.HEARTBEAT_STALE_AFTER + 10)


async def is_connection_alive(connection_id: str) -> bool:
    r = _get_pool()
    return await r.exists(_key_ws_connection(connection_id)) == 1


# ── Pub/Sub ────────────────────────────────────────────────────────────────────

async def publish_event(session_id: str, event_json: str) -> None:
    """Publish to session events channel. Used by all modules to relay WS events."""
    r = _get_pool()
    await r.publish(_channel_session_events(session_id), event_json)


async def publish_anticheat_event(session_id: str, event_json: str) -> None:
    """Anti-cheat uses its own isolated channel."""
    r = _get_pool()
    await r.publish(_channel_anticheat_events(session_id), event_json)


async def subscribe_session_events(session_id: str) -> PubSub:
    """
    Returns a PubSub object subscribed to session events.
    Caller is responsible for cleanup (await pubsub.aclose()).
    Uses the dedicated pubsub pool to avoid blocking regular operations.
    """
    r = _get_pubsub_pool()
    pubsub = r.pubsub()
    await pubsub.subscribe(_channel_session_events(session_id))
    return pubsub


# ── Circuit breaker ────────────────────────────────────────────────────────────

class CircuitState(str):
    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"


async def get_circuit_state(provider_name: str) -> str:
    r = _get_pool()
    state = await r.get(_key_circuit_breaker(provider_name))
    return state or CircuitState.CLOSED


async def open_circuit(provider_name: str, ttl_seconds: int = 60) -> None:
    r = _get_pool()
    await r.setex(_key_circuit_breaker(provider_name), ttl_seconds, CircuitState.OPEN)


async def close_circuit(provider_name: str) -> None:
    r = _get_pool()
    await r.delete(_key_circuit_breaker(provider_name))


# ── Active session discovery (admin monitoring) ───────────────────────────────

async def scan_sessions() -> list[str]:
    """
    Return all active session IDs currently in Redis.
    Used by admin dashboard to count and list live sessions.

    Scans session_state:* keys — these only exist for active/recent sessions.
    Uses SCAN (not KEYS) for safety under load.
    """
    r = _get_pool()
    session_ids: list[str] = []
    cursor = 0
    pattern = "session_state:*"

    while True:
        cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
        for key in keys:
            # Extract session_id from "session_state:{session_id}"
            key_str = key if isinstance(key, str) else key.decode()
            sid = key_str.replace("session_state:", "", 1)
            session_ids.append(sid)
        if cursor == 0:
            break

    return session_ids


async def get_all_active_sessions() -> list[dict]:
    """
    Return summary dicts for all active sessions.
    Used by /admin/sessions/active endpoint.

    Reads state for each discovered session_id.
    Returns lightweight dict for dashboard rendering — not full SessionState.
    """
    session_ids = await scan_sessions()
    if not session_ids:
        return []

    results = []
    for sid in session_ids:
        try:
            state = await get_session_state(sid)
            if state and state.is_active:
                results.append({
                    "session_id": state.session_id,
                    "candidate_id": state.candidate_id,
                    "domain": state.active_domain.value if state.active_domain else None,
                    "mode": state.mode.value if state.mode else None,
                    "turn_count": state.turn_count,
                    "phase": state.phase.value if state.phase else None,
                    "started_at": state.started_at.isoformat() if state.started_at else None,
                    "last_turn_at": state.last_turn_at.isoformat() if state.last_turn_at else None,
                    "is_active": state.is_active,
                })
        except Exception:
            continue  # Expired or malformed key — skip

    return results
