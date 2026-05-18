"""
core/runtime_config.py — Mutable runtime configuration.

Stores settings that admin can change at runtime without restart.
Persisted in Redis so changes survive worker restarts but not Redis flush.
"""
from __future__ import annotations
from typing import Any
import json
import structlog
from app.core import redis as r_core

log = structlog.get_logger(__name__)

REDIS_KEY = "runtime:config"

# Defaults — used when Redis has no stored value
_DEFAULTS: dict[str, Any] = {
    # LLM Config
    "qgen_model": "gpt-4o-mini",
    "eval_model": "gpt-4o-mini",
    # Voice Config
    "tts_enabled": True,
    "tts_provider": "inworld",
    "tts_voice": "",
}

# In-memory cache (fast reads, synced from Redis)
_cache: dict[str, Any] = dict(_DEFAULTS)


async def load_from_redis():
    """Load runtime config from Redis at startup."""
    global _cache
    try:
        pool = r_core._pool
        if pool:
            raw = await pool.get(REDIS_KEY)
            if raw:
                stored = json.loads(raw)
                _cache = {**_DEFAULTS, **stored}
                log.info("runtime_config.loaded", keys=list(stored.keys()))
    except Exception as e:
        log.warning("runtime_config.load_failed", error=str(e))


async def save_to_redis():
    """Persist current config to Redis."""
    try:
        pool = r_core._pool
        if pool:
            await pool.set(REDIS_KEY, json.dumps(_cache))
    except Exception as e:
        log.warning("runtime_config.save_failed", error=str(e))


def get(key: str, default: Any = None) -> Any:
    return _cache.get(key, default)


async def set_key(key: str, value: Any):
    _cache[key] = value
    await save_to_redis()
    log.info("runtime_config.updated", key=key, value=value)


async def set_many(updates: dict[str, Any]):
    _cache.update(updates)
    await save_to_redis()
    log.info("runtime_config.bulk_update", keys=list(updates.keys()))


def get_all() -> dict[str, Any]:
    return dict(_cache)
