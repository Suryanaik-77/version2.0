"""
websocket.py — WebSocket hub. The critical real-time relay.

Design invariants:
1. NO business logic here. Pure message routing.
2. Every receive operation dispatches work as a non-awaited task.
   The WS loop NEVER awaits domain work directly.
3. One Redis subscriber task per connection.
4. Active generation task tracked per session for barge-in interruption.
5. Heartbeat runs as a background task — not in the receive loop.
6. Session state lives in Redis — not in this class.

Concurrency model:
- receive_loop task: reads from WebSocket, dispatches audio tasks
- redis_sub_task:   reads from Redis pub/sub, writes to WebSocket
- heartbeat_task:   refreshes connection TTL every N seconds
These three tasks run concurrently per connection. No shared mutable state.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Awaitable
from uuid import uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio.client import PubSub

from app.api.auth import validate_ws_token
from app.config import get_settings
from app.core import redis as r
from app.core.session import (
    session_exists,
    end_session,
    SessionNotFoundError,
    SessionEndedError,
)
from app.models.events import (
    WSEvent,
    WSEventType,
    DisconnectReason,
    heartbeat_ack_event,
    barge_in_event,
    error_event,
    session_end_event,
)
from app.models.session import EndReason
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter()


# ── WebSocket Hub ─────────────────────────────────────────────────────────────

class WebSocketHub:
    """
    Manages all active WebSocket connections.
    
    State held here:
    - Active WebSocket objects (can't go in Redis — not serializable)
    - Active generation tasks (for barge-in cancellation)
    
    Everything else lives in Redis.
    """

    def __init__(self) -> None:
        # connection_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # session_id → set of connection_ids (local instance only)
        self._local_session_conns: dict[str, set[str]] = {}
        # session_id → active generation asyncio.Task (for barge-in)
        self._active_streams: dict[str, asyncio.Task] = {}
        # session_id → set of connection_ids that are admin connections
        self._admin_connections: dict[str, set[str]] = {}

    # ── Connection management ─────────────────────────────────────────────────

    async def connect(
        self,
        session_id: str,
        connection_id: str,
        ws: WebSocket,
        is_admin: bool = False,
    ) -> None:
        self._connections[connection_id] = ws
        self._local_session_conns.setdefault(session_id, set()).add(connection_id)
        if is_admin:
            self._admin_connections.setdefault(session_id, set()).add(connection_id)
        await r.register_connection(session_id, connection_id)
        log.info("ws.connected", session_id=session_id, connection_id=connection_id)

    async def disconnect(
        self,
        session_id: str,
        connection_id: str,
        reason: DisconnectReason = DisconnectReason.CLEAN,
    ) -> None:
        self._connections.pop(connection_id, None)
        self._local_session_conns.get(session_id, set()).discard(connection_id)
        self._admin_connections.get(session_id, set()).discard(connection_id)
        await r.unregister_connection(session_id, connection_id)
        # Clean up audio accumulator when all connections for session are gone
        if not self._local_session_conns.get(session_id):
            _audio_accumulators.pop(session_id, None)
            _turn_counters.pop(session_id, None)
        record_event("ws.disconnected", session_id=session_id, reason=reason.value)
        log.info("ws.disconnected", session_id=session_id, reason=reason.value)

    # ── Message relay ─────────────────────────────────────────────────────────

    async def send_to_connection(self, connection_id: str, event_json: str) -> bool:
        """Send event to a specific connection. Returns False if connection is gone."""
        ws = self._connections.get(connection_id)
        if ws is None:
            return False
        try:
            await ws.send_text(event_json)
            return True
        except Exception as exc:
            log.warning("ws.send_failed", connection_id=connection_id, error=str(exc))
            return False

    async def send_bytes_to_session(self, session_id: str, data: bytes) -> None:
        """Send binary audio data to all candidate connections for a session."""
        conns = list(self._local_session_conns.get(session_id, set()))
        if not conns:
            log.warning("ws.no_connections_for_audio", session_id=session_id, bytes=len(data))
            return
        for conn_id in conns:
            if conn_id in self._admin_connections.get(session_id, set()):
                continue
            ws = self._connections.get(conn_id)
            if ws:
                try:
                    await ws.send_bytes(data)
                except Exception as exc:
                    log.warning("ws.audio_send_failed", session_id=session_id, error=str(exc), bytes=len(data))

    async def publish_to_session(self, session_id: str, event_json: str) -> None:
        """
        Publish event to session via Redis pub/sub.
        Used by pipeline — routes through Redis so other hub instances receive it too.
        """
        await r.publish_event(session_id, event_json)

    async def relay_to_session(self, session_id: str, event_json: str, admin_only: bool = False) -> None:
        """
        Relay event to all local connections for a session.
        admin_only=True: send only to admin connections (e.g. STATE_CHANGE).
        """
        target_conns = (
            self._admin_connections.get(session_id, set())
            if admin_only
            else self._local_session_conns.get(session_id, set())
        )
        dead = set()
        for conn_id in list(target_conns):
            ok = await self.send_to_connection(conn_id, event_json)
            if not ok:
                dead.add(conn_id)
        
        # Clean up dead connections without blocking
        for conn_id in dead:
            asyncio.create_task(
                self.disconnect(session_id, conn_id, DisconnectReason.ERROR)
            )

    # ── Barge-in ──────────────────────────────────────────────────────────────

    def register_stream(self, session_id: str, task: asyncio.Task) -> None:
        """
        Track the active generation task.
        Called by turn handler when question generation starts.
        Required for barge-in interruption.
        """
        existing = self._active_streams.get(session_id)
        if existing and not existing.done():
            # Shouldn't happen under normal operation — log if it does
            log.warning("ws.stream_replaced", session_id=session_id)
            existing.cancel()
        self._active_streams[session_id] = task

    async def interrupt_stream(self, session_id: str) -> None:
        """
        Cancel the active generation task for barge-in.
        Must complete within 50ms (no I/O in cancellation path).
        """
        task = self._active_streams.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        record_event("ws.barge_in_interrupted", session_id=session_id)

    def clear_stream(self, session_id: str) -> None:
        self._active_streams.pop(session_id, None)


# Singleton hub instance — shared across all WebSocket connections
hub = WebSocketHub()


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    Main WebSocket endpoint.
    
    Execution model:
    - Authenticate before accepting connection
    - Launch three concurrent tasks: receive_loop, redis_sub_loop, heartbeat_loop
    - Never await domain work inside this function
    """
    # ── Auth before accept ────────────────────────────────────────────────────
    try:
        user = await validate_ws_token(websocket)
    except Exception:
        return  # validate_ws_token already closed the socket

    # ── Validate session ──────────────────────────────────────────────────────
    if not await session_exists(session_id):
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    connection_id = str(uuid4())
    is_admin = user.role.value in ("admin", "reviewer")
    
    await hub.connect(session_id, connection_id, websocket, is_admin=is_admin)

    # ── Send opening question immediately ───────────────────────────────────────
    asyncio.create_task(
        _send_opening(session_id, connection_id),
        name=f"opening_{session_id}",
    )

    # ── Launch concurrent tasks ───────────────────────────────────────────────
    receive_task  = asyncio.create_task(_receive_loop(session_id, connection_id, websocket, is_admin))
    sub_task      = asyncio.create_task(_redis_sub_loop(session_id, connection_id))
    heartbeat_task = asyncio.create_task(_heartbeat_loop(session_id, connection_id))

    try:
        # Wait for ANY task to finish — normally receive_task ends on disconnect
        done, pending = await asyncio.wait(
            [receive_task, sub_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        
        # Cancel remaining tasks cleanly
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        
        # Re-raise exceptions from completed tasks (for logging)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                log.error("ws.task_error", session_id=session_id, error=str(exc))
    finally:
        await hub.disconnect(session_id, connection_id)
        log.info("ws.endpoint_exited", session_id=session_id, connection_id=connection_id)


# ── Receive loop ──────────────────────────────────────────────────────────────

async def _send_opening(session_id: str, connection_id: str) -> None:
    """Send the opening interviewer question + TTS audio right after connection."""
    try:
        from app.engines.interview import generate_opening
        from app.providers.tts import get_tts_provider
        from app.models.events import WSEvent, WSEventType

        # Generate opening text
        opening_text = ""
        async for token in generate_opening(session_id):
            opening_text += token

        if not opening_text:
            return

        # Send question text to client
        text_event = WSEvent(
            type=WSEventType.INTERVIEWER_CHUNK,
            session_id=session_id,
            payload={"text": opening_text, "sentence_index": 0, "is_final": True},
        )
        await hub.send_to_connection(connection_id, text_event.to_json())

        # Synthesize and send audio as binary frame
        tts = get_tts_provider()
        audio_bytes = await tts.synthesize(opening_text, session_id=session_id)
        if audio_bytes and len(audio_bytes) > 100:
            ws = hub._connections.get(connection_id)
            if ws:
                await ws.send_bytes(audio_bytes)

        # Send done event
        done_event = WSEvent(
            type=WSEventType.INTERVIEWER_DONE,
            session_id=session_id,
            payload={"sentence_count": 1},
        )
        await hub.send_to_connection(connection_id, done_event.to_json())

        log.info("ws.opening_sent", session_id=session_id, chars=len(opening_text))
    except Exception as exc:
        log.error("ws.opening_failed", session_id=session_id, error=str(exc), exc_info=exc)


async def _receive_loop(
    session_id: str,
    connection_id: str,
    websocket: WebSocket,
    is_admin: bool,
) -> None:
    """
    Reads messages from the WebSocket client.
    
    Binary frames → audio data → dispatch as non-blocking task
    Text frames → JSON event (heartbeat, barge-in signal) → dispatch
    
    CRITICAL: Nothing is awaited inline except the recv call itself.
    All processing is dispatched via create_task.
    """
    try:
        while True:
            # This is the ONLY await in the receive loop
            message = await websocket.receive()
            
            if message["type"] == "websocket.disconnect":
                break
            
            if "bytes" in message and message["bytes"]:
                # Audio chunk — dispatch to STT pipeline (Phase 3)
                # Non-blocking: create_task returns immediately
                asyncio.create_task(
                    _handle_audio_chunk(session_id, message["bytes"]),
                    name=f"audio_{session_id}",
                )
                
            elif "text" in message and message["text"]:
                asyncio.create_task(
                    _handle_text_message(session_id, connection_id, message["text"], is_admin),
                    name=f"text_{session_id}",
                )
                
    except WebSocketDisconnect:
        log.info("ws.client_disconnected", session_id=session_id)
    except Exception as exc:
        log.error("ws.receive_error", session_id=session_id, error=str(exc))


# ── Redis subscriber loop ─────────────────────────────────────────────────────

async def _redis_sub_loop(session_id: str, connection_id: str) -> None:
    """
    Subscribes to Redis session events channel.
    Forwards every event to local WebSocket connections.
    
    This is how cross-instance events reach connected clients.
    One task per connection — task ends when connection ends.
    """
    pubsub: PubSub = await r.subscribe_session_events(session_id)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            
            event_json: str = message["data"]
            
            # Parse to determine if admin-only
            try:
                event = json.loads(event_json)
                event_type = event.get("type")
                admin_only = event_type == WSEventType.STATE_CHANGE
            except (json.JSONDecodeError, AttributeError):
                admin_only = False
            
            await hub.relay_to_session(session_id, event_json, admin_only=admin_only)
            
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.error("ws.sub_error", session_id=session_id, error=str(exc))
    finally:
        await pubsub.aclose()


# ── Heartbeat loop ────────────────────────────────────────────────────────────

async def _heartbeat_loop(session_id: str, connection_id: str) -> None:
    """
    Refreshes connection TTL in Redis at regular intervals.
    Also detects stale connections (client stopped sending heartbeats).
    
    Client is expected to send HEARTBEAT every 15s.
    We track last-seen and close after 45s without heartbeat.
    """
    try:
        while True:
            await asyncio.sleep(settings.HEARTBEAT_INTERVAL)
            
            alive = await r.is_connection_alive(connection_id)
            if not alive:
                log.warning("ws.connection_stale", connection_id=connection_id)
                break
            
            await r.heartbeat_connection(connection_id)
            
    except asyncio.CancelledError:
        pass


from app.voice.vad import AudioAccumulator, VADResult
from app.voice import pipeline as voice_pipeline

# Per-session audio accumulators — created on connect, cleaned on disconnect
# Session-isolated: one dict entry per active session
_audio_accumulators: dict[str, AudioAccumulator] = {}
_turn_counters: dict[str, int] = {}  # session_id → current turn number


# ── Audio chunk handler (Phase 3 — VAD + STT + pipeline) ─────────────────────

async def _handle_audio_chunk(session_id: str, audio_bytes: bytes) -> None:
    """
    Receives PCM audio bytes from candidate microphone.
    Runs VAD, accumulates chunks, triggers STT + pipeline when utterance ends.

    Execution: always runs as a detached task — never blocks the receive loop.
    Session-isolated: uses per-session AudioAccumulator, no shared state.
    """
    # Get or create per-session accumulator
    if session_id not in _audio_accumulators:
        _audio_accumulators[session_id] = AudioAccumulator(session_id=session_id)
        _turn_counters[session_id] = 0

    accumulator = _audio_accumulators[session_id]
    result = accumulator.push_chunk(audio_bytes)

    if result == VADResult.UTTERANCE_COMPLETE:
        audio_data = accumulator.get_audio()
        _turn_counters[session_id] = _turn_counters.get(session_id, 0) + 1
        turn_number = _turn_counters[session_id]

        # Launch STT → pipeline as non-blocking task
        asyncio.create_task(
            voice_pipeline.handle_utterance(
                session_id=session_id,
                audio_bytes=audio_data,
                turn_number=turn_number,
                ws_hub=hub,
            ),
            name=f"utterance_{session_id}_{turn_number}",
        )


# ── Text message handler ──────────────────────────────────────────────────────

async def _handle_text_message(
    session_id: str,
    connection_id: str,
    text: str,
    is_admin: bool,
) -> None:
    """
    Handles JSON control messages from client.
    
    Supported: HEARTBEAT, BARGE_IN.
    Unsupported event types are logged and dropped.
    """
    try:
        data = json.loads(text)
        event_type = data.get("type")
    except (json.JSONDecodeError, AttributeError):
        log.warning("ws.invalid_message", session_id=session_id)
        return

    if event_type == WSEventType.HEARTBEAT:
        await r.heartbeat_connection(connection_id)
        ack = heartbeat_ack_event(session_id)
        await hub.send_to_connection(connection_id, ack.to_json())
        
    elif event_type == WSEventType.BARGE_IN:
        # Candidate is speaking — interrupt current generation
        log.info("ws.barge_in", session_id=session_id)
        await hub.interrupt_stream(session_id)
        # Relay barge-in event to confirm interruption
        await r.publish_event(session_id, barge_in_event(session_id).to_json())

    elif event_type == WSEventType.SESSION_END and is_admin:
        asyncio.create_task(
            _handle_admin_end_session(session_id),
            name=f"end_{session_id}",
        )

    else:
        log.debug("ws.unknown_event_type", session_id=session_id, event_type=event_type)


async def _handle_admin_end_session(session_id: str) -> None:
    try:
        summary = await end_session(session_id, EndReason.ADMIN_TERMINATE)
        event = session_end_event(session_id, EndReason.ADMIN_TERMINATE.value)
        await r.publish_event(session_id, event.to_json())
    except (SessionNotFoundError, SessionEndedError) as exc:
        log.warning("ws.end_session_failed", session_id=session_id, error=str(exc))
