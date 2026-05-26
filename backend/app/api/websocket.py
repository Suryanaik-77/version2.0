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

STT routing (v2):
  Streaming STT (Deepgram WebSocket) is enabled when:
    - DEEPGRAM_API_KEY is set in environment
    - runtime_config stt_provider == "deepgram"
  Frontend sends raw PCM frames (AudioWorklet) as binary WebSocket frames.
  These are routed directly to the Deepgram WebSocket without buffering.

  Blob/batch STT fallback:
    Used when streaming STT is disabled or when the frontend sends WebM blobs
    (MediaRecorder fallback for browsers without AudioWorklet support).
    Detected via AUDIO_META + binary frame sequence.
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
    get_session,
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
        self._connections:         dict[str, WebSocket]      = {}
        self._local_session_conns: dict[str, set[str]]       = {}
        self._active_streams:      dict[str, asyncio.Task]   = {}
        self._admin_connections:   dict[str, set[str]]       = {}

    async def connect(
        self,
        session_id:    str,
        connection_id: str,
        ws:            WebSocket,
        is_admin:      bool = False,
    ) -> None:
        self._connections[connection_id] = ws
        self._local_session_conns.setdefault(session_id, set()).add(connection_id)
        if is_admin:
            self._admin_connections.setdefault(session_id, set()).add(connection_id)
        await r.register_connection(session_id, connection_id)
        log.info("ws.connected", session_id=session_id, connection_id=connection_id)

    async def disconnect(
        self,
        session_id:    str,
        connection_id: str,
        reason:        DisconnectReason = DisconnectReason.CLEAN,
    ) -> None:
        self._connections.pop(connection_id, None)
        self._local_session_conns.get(session_id, set()).discard(connection_id)
        self._admin_connections.get(session_id, set()).discard(connection_id)
        await r.unregister_connection(session_id, connection_id)
        if not self._local_session_conns.get(session_id):
            _audio_accumulators.pop(session_id, None)
            _turn_counters.pop(session_id, None)
        record_event("ws.disconnected", session_id=session_id, reason=reason.value)
        log.info("ws.disconnected", session_id=session_id, reason=reason.value)

    async def send_to_connection(self, connection_id: str, event_json: str) -> bool:
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
        conns = list(self._local_session_conns.get(session_id, set()))
        if not conns:
            log.warning("ws.no_connections_for_audio",
                        session_id=session_id, bytes=len(data))
            return
        for conn_id in conns:
            ws = self._connections.get(conn_id)
            if ws:
                try:
                    await ws.send_bytes(data)
                except Exception as exc:
                    log.warning("ws.audio_send_failed",
                                session_id=session_id, error=str(exc), bytes=len(data))

    async def publish_to_session(self, session_id: str, event_json: str) -> None:
        await r.publish_event(session_id, event_json)

    async def relay_to_session(
        self,
        session_id:  str,
        event_json:  str,
        admin_only:  bool = False,
    ) -> None:
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
        for conn_id in dead:
            asyncio.create_task(
                self.disconnect(session_id, conn_id, DisconnectReason.ERROR)
            )

    def register_stream(self, session_id: str, task: asyncio.Task) -> None:
        existing = self._active_streams.get(session_id)
        if existing and not existing.done():
            log.warning("ws.stream_replaced", session_id=session_id)
            existing.cancel()
        self._active_streams[session_id] = task

    async def interrupt_stream(self, session_id: str) -> None:
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


hub = WebSocketHub()


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    try:
        user = await validate_ws_token(websocket)
    except Exception:
        return

    if not await session_exists(session_id):
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    connection_id = str(uuid4())
    is_admin      = user.role.value in ("admin", "reviewer")

    await hub.connect(session_id, connection_id, websocket, is_admin=is_admin)

    state = await get_session(session_id)
    if state and state.turn_count == 0:
        asyncio.create_task(
            _send_opening(session_id, connection_id),
            name=f"opening_{session_id}",
        )
    else:
        reconnect_event = WSEvent(
            type=WSEventType.RECONNECTED,
            session_id=session_id,
            payload={
                "turn_count": state.turn_count if state else 0,
                "mode":       state.mode.value if state else "PROBING",
                "domain":     state.active_domain.value if state else "",
                "message":    "Session restored. Continuing from where you left off.",
            },
        )
        await hub.send_to_connection(connection_id, reconnect_event.to_json())
        log.info("ws.reconnect_restored", session_id=session_id,
                 turn_count=state.turn_count if state else 0)
        record_event("ws.reconnected", session_id=session_id)

    # ── Streaming STT setup ───────────────────────────────────────────────────
    # Enable when: DEEPGRAM_API_KEY is set AND stt_provider == "deepgram"
    # Frontend sends raw PCM (AudioWorklet, linear16, 16kHz) as binary frames.
    # Falls back gracefully: if streaming STT init fails, blob path handles audio.
    from app.core.runtime_config import get as rc_get
    _use_streaming_stt = (
        bool(settings.DEEPGRAM_API_KEY) and
        rc_get("stt_provider", "openai") == "deepgram"
    )
    _streaming_stt_instance = None

    if _use_streaming_stt:
        from app.providers.streaming_stt import get_or_create_streaming_stt

        async def _on_utterance_complete(transcript: str, stt_ms: int):
            """Called by Deepgram when candidate finishes speaking."""
            corrected = _correct_transcript(transcript)
            if corrected != transcript:
                log.info("stt.corrected", session_id=session_id,
                         raw=transcript[:80], corrected=corrected[:80])
                transcript = corrected

            turn_num = _turn_counters.get(session_id, 0) + 1
            _turn_counters[session_id] = turn_num

            stt_event = stt_final_event(session_id, transcript, stt_ms, turn_num)
            await hub.publish_to_session(session_id, stt_event.to_json())

            from app.voice.pipeline import run_turn_pipeline
            pipeline_task = asyncio.create_task(
                run_turn_pipeline(
                    session_id=session_id,
                    transcript=transcript,
                    turn_number=turn_num,
                    ws_hub=hub,
                ),
                name=f"pipeline_{session_id}_{turn_num}",
            )
            hub.register_stream(session_id, pipeline_task)

        async def _on_partial(partial: str):
            from app.models.events import stt_partial_event
            event = stt_partial_event(session_id, partial)
            await hub.relay_to_session(session_id, event.to_json())

        _streaming_stt_instance = await get_or_create_streaming_stt(
            session_id=session_id,
            on_utterance_complete=_on_utterance_complete,
            on_partial=_on_partial,
        )
        if _streaming_stt_instance:
            log.info("ws.streaming_stt_enabled", session_id=session_id)
        else:
            log.warning("ws.streaming_stt_fallback_to_batch", session_id=session_id)
            _use_streaming_stt = False

    # ── Launch concurrent tasks ───────────────────────────────────────────────
    receive_task   = asyncio.create_task(
        _receive_loop(session_id, connection_id, websocket, is_admin)
    )
    sub_task       = asyncio.create_task(
        _redis_sub_loop(session_id, connection_id)
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(session_id, connection_id)
    )

    try:
        done, pending = await asyncio.wait(
            [receive_task, sub_task, heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                log.error("ws.task_error", session_id=session_id, error=str(exc))
    finally:
        if _streaming_stt_instance:
            from app.providers.streaming_stt import close_streaming_stt
            await close_streaming_stt(session_id)
        await hub.disconnect(session_id, connection_id)
        log.info("ws.endpoint_exited",
                 session_id=session_id, connection_id=connection_id)


# ── Receive loop ──────────────────────────────────────────────────────────────

async def _send_opening(session_id: str, connection_id: str) -> None:
    try:
        from app.engines.interview import generate_opening
        from app.providers.tts import get_tts_provider
        from app.models.events import WSEvent, WSEventType

        opening_text = ""
        async for token in generate_opening(session_id):
            opening_text += token

        if not opening_text:
            return

        text_event = WSEvent(
            type=WSEventType.INTERVIEWER_CHUNK,
            session_id=session_id,
            payload={"text": opening_text, "sentence_index": 0, "is_final": True},
        )
        await hub.send_to_connection(connection_id, text_event.to_json())

        tts         = get_tts_provider()
        audio_bytes = await tts.synthesize(opening_text, session_id=session_id)
        if audio_bytes and len(audio_bytes) > 100:
            ws = hub._connections.get(connection_id)
            if ws:
                await ws.send_bytes(audio_bytes)

        done_event = WSEvent(
            type=WSEventType.INTERVIEWER_DONE,
            session_id=session_id,
            payload={"sentence_count": 1},
        )
        await hub.send_to_connection(connection_id, done_event.to_json())
        log.info("ws.opening_sent", session_id=session_id, chars=len(opening_text))

    except Exception as exc:
        log.error("ws.opening_failed", session_id=session_id,
                  error=str(exc), exc_info=exc)


async def _receive_loop(
    session_id:    str,
    connection_id: str,
    websocket:     WebSocket,
    is_admin:      bool,
) -> None:
    """
    Reads messages from the WebSocket client.

    Binary frames:
      1. If streaming STT active → forward PCM to Deepgram (zero-copy, non-blocking)
      2. If AUDIO_META pending → blob transport (MediaRecorder fallback)
      3. Otherwise → server-side VAD path

    Text frames: JSON control messages (heartbeat, barge-in, AUDIO_META metadata).

    CRITICAL: Nothing is awaited inline except the recv call itself.
    All processing is dispatched via create_task.
    """
    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]

                # Route 1: Streaming STT (PCM from AudioWorklet)
                from app.providers.streaming_stt import _active_stt
                streaming_stt = _active_stt.get(session_id)
                if streaming_stt and streaming_stt.is_connected:
                    asyncio.create_task(streaming_stt.send_audio(audio_bytes))
                    continue

                # Route 2: Blob transport (MediaRecorder fallback — AUDIO_META sent first)
                meta = _pending_audio_meta.pop(session_id, None)
                if meta:
                    asyncio.create_task(
                        _handle_audio_blob(session_id, {
                            "audio_bytes": audio_bytes,
                            "format":      meta.get("format", "webm"),
                            "duration_ms": meta.get("duration_ms", 0),
                            "_binary":     True,
                        }),
                        name=f"audio_blob_{session_id}",
                    )
                else:
                    # Route 3: PCM from server-side VAD
                    asyncio.create_task(
                        _handle_audio_chunk(session_id, audio_bytes),
                        name=f"audio_{session_id}",
                    )

            elif "text" in message and message["text"]:
                asyncio.create_task(
                    _handle_text_message(
                        session_id, connection_id, message["text"], is_admin
                    ),
                    name=f"text_{session_id}",
                )

    except WebSocketDisconnect:
        log.info("ws.client_disconnected", session_id=session_id)
    except Exception as exc:
        log.error("ws.receive_error", session_id=session_id, error=str(exc))


# ── Redis subscriber loop ─────────────────────────────────────────────────────

async def _redis_sub_loop(session_id: str, connection_id: str) -> None:
    pubsub: PubSub = await r.subscribe_session_events(session_id)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            event_json: str = message["data"]
            try:
                event      = json.loads(event_json)
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

_audio_accumulators: dict[str, AudioAccumulator] = {}
_turn_counters:      dict[str, int]               = {}
_pending_audio_meta: dict[str, dict]              = {}


# ── Audio chunk handler (server-side VAD) ─────────────────────────────────────

async def _handle_audio_chunk(session_id: str, audio_bytes: bytes) -> None:
    if session_id not in _audio_accumulators:
        _audio_accumulators[session_id] = AudioAccumulator(session_id=session_id)
        _turn_counters[session_id]      = 0

    accumulator = _audio_accumulators[session_id]
    result      = accumulator.push_chunk(audio_bytes)

    if result == VADResult.UTTERANCE_COMPLETE:
        audio_data   = accumulator.get_audio()
        _turn_counters[session_id] = _turn_counters.get(session_id, 0) + 1
        turn_number  = _turn_counters[session_id]

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
    session_id:    str,
    connection_id: str,
    text:          str,
    is_admin:      bool,
) -> None:
    try:
        data       = json.loads(text)
        event_type = data.get("type")
    except (json.JSONDecodeError, AttributeError):
        log.warning("ws.invalid_message", session_id=session_id)
        return

    if event_type == WSEventType.HEARTBEAT:
        await r.heartbeat_connection(connection_id)
        ack = heartbeat_ack_event(session_id)
        await hub.send_to_connection(connection_id, ack.to_json())

    elif event_type == WSEventType.BARGE_IN:
        log.info("ws.barge_in", session_id=session_id)
        await hub.interrupt_stream(session_id)
        await r.publish_event(session_id, barge_in_event(session_id).to_json())

    elif event_type == "AUDIO_META":
        _pending_audio_meta[session_id] = {
            "format":      data.get("format", "webm"),
            "duration_ms": data.get("duration_ms", 0),
        }

    elif event_type == "AUDIO_BLOB":
        asyncio.create_task(
            _handle_audio_blob(session_id, data),
            name=f"audio_blob_{session_id}",
        )

    elif event_type == WSEventType.SESSION_END and is_admin:
        asyncio.create_task(
            _handle_admin_end_session(session_id),
            name=f"end_{session_id}",
        )

    else:
        log.debug("ws.unknown_event_type",
                  session_id=session_id, event_type=event_type)


# ── Audio blob handler (MediaRecorder fallback) ───────────────────────────────

_transcript_accum: dict[str, list[str]] = {}
_stt_client = None

def _get_stt_client():
    global _stt_client
    if _stt_client is None:
        from openai import AsyncOpenAI
        from app.config import get_settings
        _stt_client = AsyncOpenAI(api_key=get_settings().OPENAI_API_KEY, timeout=10.0)
    return _stt_client


async def _handle_audio_blob(session_id: str, data: dict) -> None:
    """
    Handle complete audio blob from browser MediaRecorder (fallback path).
    Used when AudioWorklet is unavailable or stt_provider != deepgram.
    """
    audio_format = data.get("format", "webm")
    duration_ms  = data.get("duration_ms", 0)

    if data.get("_binary"):
        audio_bytes = data["audio_bytes"]
    else:
        import base64
        audio_b64   = data.get("audio", "")
        if not audio_b64:
            return
        audio_bytes = base64.b64decode(audio_b64)

    log.info("ws.audio_blob", session_id=session_id,
             bytes=len(audio_bytes), duration_ms=duration_ms, format=audio_format)

    from app.providers.stt import get_stt_provider
    stt        = get_stt_provider()
    transcript = await _transcribe_blob(stt, audio_bytes, audio_format, session_id)

    if not transcript:
        log.warning("ws.blob_empty_transcript", session_id=session_id)
        return

    raw_transcript = transcript
    transcript     = _correct_transcript(transcript)
    if transcript != raw_transcript:
        log.info("stt.corrected", session_id=session_id,
                 raw=raw_transcript[:80], corrected=transcript[:80])

    if session_id not in _transcript_accum:
        _transcript_accum[session_id] = []
    _transcript_accum[session_id].append(transcript)

    log.info("ws.blob_transcribed", session_id=session_id,
             chars=len(transcript),
             chunk_count=len(_transcript_accum[session_id]),
             preview=transcript[:80])

    from app.models.events import stt_final_event
    turn_num               = _turn_counters.get(session_id, 0) + 1
    _turn_counters[session_id] = turn_num
    combined               = " ".join(_transcript_accum[session_id])
    stt_event              = stt_final_event(session_id, combined, duration_ms, turn_num)
    await hub.publish_to_session(session_id, stt_event.to_json())

    full_transcript = " ".join(_transcript_accum.pop(session_id, []))
    if full_transcript:
        from app.voice.pipeline import run_turn_pipeline
        pipeline_task = asyncio.create_task(
            run_turn_pipeline(
                session_id=session_id,
                transcript=full_transcript,
                turn_number=turn_num,
                ws_hub=hub,
            ),
            name=f"pipeline_{session_id}_{turn_num}",
        )
        hub.register_stream(session_id, pipeline_task)


# ── VLSI transcript correction (deterministic, <1ms) ─────────────────────────

import re as _re

_VLSI_CORRECTIONS: list[tuple[str, str]] = [
    (r'\bpolly\b', 'poly'),
    (r'\bpoly (?:silicon|silicone)\b', 'polysilicon'),
    (r'\benv well\b', 'n-well'),
    (r'\bn well\b', 'n-well'),
    (r'\bp well\b', 'p-well'),
    (r'\bp sub\b', 'p-sub'),
    (r'\bn sub\b', 'n-sub'),
    (r'\bmetal won\b', 'metal one'),
    (r'\bmetal to\b', 'metal two'),
    (r'\bsub straight\b', 'substrate'),
    (r'\bsubstrait\b', 'substrate'),
    (r'\bguard rain\b', 'guard ring'),
    (r'\bguard rang\b', 'guard ring'),
    (r'\bcommon centre\b', 'common centroid'),
    (r'\bcommon central\b', 'common centroid'),
    (r'\binter digitation\b', 'interdigitation'),
    (r'\bD R C\b', 'DRC'),
    (r'\bL V S\b', 'LVS'),
    (r'\bP E X\b', 'PEX'),
    (r'\bE S D\b', 'ESD'),
    (r'\bgm over id\b', 'gm/id'),
    (r'\bgm by id\b', 'gm/id'),
    (r'\bfolded cascade\b', 'folded cascode'),
    (r'\btele scopic\b', 'telescopic'),
    (r'\bvirtual so\b', 'Virtuoso'),
    (r'\bvirtuoso\b', 'Virtuoso'),
    (r'\bcalibre\b', 'Calibre'),
    (r'\bI C C 2\b', 'ICC2'),
    (r'\bprime time\b', 'PrimeTime'),
]

_SEMICONDUCTOR_CONTEXT = frozenset([
    'poly', 'metal', 'via', 'contact', 'diffusion', 'layout', 'drc', 'lvs',
    'transistor', 'nmos', 'pmos', 'well', 'substrate', 'guard ring', 'od',
    'active', 'layer', 'mask', 'routing', 'placement', 'extraction',
])


def _correct_transcript(text: str) -> str:
    text_lower    = text.lower()
    context_count = sum(1 for kw in _SEMICONDUCTOR_CONTEXT if kw in text_lower)

    for pattern, replacement in _VLSI_CORRECTIONS:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)

    return text


async def _transcribe_blob(stt, audio_bytes: bytes, fmt: str, session_id: str) -> str:
    import time
    import io

    if len(audio_bytes) < 1000:
        return ""

    from app.core.runtime_config import get as rc_get
    provider = rc_get("stt_provider", "openai")
    t0       = time.monotonic()

    if provider == "deepgram" and settings.DEEPGRAM_API_KEY:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen"
                    "?model=nova-2&language=en&smart_format=true",
                    headers={
                        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                        "Content-Type":  f"audio/{fmt}",
                    },
                    content=audio_bytes,
                )
                response.raise_for_status()
                data       = response.json()
                transcript = (
                    data.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                    .strip()
                )
            elapsed = int((time.monotonic() - t0) * 1000)
            log.info("stt.deepgram_done", session_id=session_id,
                     chars=len(transcript), latency_ms=elapsed)
            from app.observability.call_tracker import track_stt_call
            track_stt_call(session_id=session_id, latency_ms=elapsed,
                           status="success" if transcript else "empty")
            return transcript
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            log.warning("stt.deepgram_error", session_id=session_id,
                        error=str(exc), latency_ms=elapsed)

    stt_model = "gpt-4o-transcribe" if provider == "openai-4o" else "gpt-4o-mini-transcribe"
    try:
        client     = _get_stt_client()
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"audio.{fmt}"
        response   = await asyncio.wait_for(
            client.audio.transcriptions.create(
                model=stt_model, file=audio_file, language="en",
            ),
            timeout=10.0,
        )
        transcript = response.text.strip() if hasattr(response, "text") else str(response).strip()
        elapsed    = int((time.monotonic() - t0) * 1000)
        log.info("stt.openai_done", session_id=session_id,
                 model=stt_model, chars=len(transcript), latency_ms=elapsed)
        return transcript
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.error("stt.blob_error", session_id=session_id,
                  error=str(exc), latency_ms=elapsed)
        return ""


async def _handle_admin_end_session(session_id: str) -> None:
    try:
        await end_session(session_id, EndReason.ADMIN_TERMINATE)
        event = session_end_event(session_id, EndReason.ADMIN_TERMINATE.value)
        await r.publish_event(session_id, event.to_json())
    except (SessionNotFoundError, SessionEndedError) as exc:
        log.warning("ws.end_session_failed", session_id=session_id, error=str(exc))
