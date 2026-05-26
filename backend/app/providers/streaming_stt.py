"""
streaming_stt.py — Deepgram streaming STT via WebSocket.

Architecture:
  - One Deepgram WebSocket per interview session
  - Browser sends raw PCM frames (AudioWorklet) → backend → Deepgram WS
  - Deepgram returns partial/final transcripts in real-time
  - On speech endpoint (silence detected by Deepgram), triggers pipeline

Latency advantage vs batch STT:
  - Batch:     wait for silence (600-1200ms) → send blob → 350-1150ms STT = 950ms-2350ms
  - Streaming: transcript computed DURING speech → 200ms endpointing window only

Audio format expected from frontend:
  - Raw PCM linear16 (int16, little-endian)
  - 16000 Hz sample rate
  - 1 channel (mono)
  - 20ms frames (640 bytes = 320 samples)
  Produced by: audioWorkletProcessor.js

Deepgram WebSocket stays open for the entire session.
No reconnect between turns — PCM has no container header issue (unlike WebM).
reset_for_new_turn() only clears transcript buffers; WS remains connected.

Integration:
  - websocket.py creates instance on connect, calls send_audio() per binary frame
  - on_utterance_complete callback triggers run_turn_pipeline
  - Keepalive loop prevents Deepgram from closing during AI speech (no audio flowing)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Awaitable

import structlog
import websockets

from app.config import get_settings
from app.observability.call_tracker import track_stt_call

log = structlog.get_logger(__name__)
settings = get_settings()


class DeepgramStreamingSTT:
    """
    Per-session streaming STT connection to Deepgram.

    Lifecycle:
      1. connect()          — on WebSocket open
      2. send_audio()       — for each PCM frame from browser (continuous)
      3. Deepgram streams back partials/finals via _receive_loop
      4. speech_final       → on_utterance_complete callback → pipeline
      5. close()            — on WebSocket close
      6. reset_for_new_turn() — between turns (clears buffers, keeps WS open)
    """

    def __init__(
        self,
        session_id: str,
        on_utterance_complete: Callable[[str, int], Awaitable[None]],
        on_partial:            Callable[[str], Awaitable[None]] | None = None,
    ):
        self.session_id             = session_id
        self.on_utterance_complete  = on_utterance_complete
        self.on_partial             = on_partial
        self._ws                    = None
        self._receive_task          = None
        self._keepalive_task        = None
        self._connected             = False
        self._reconnecting          = False
        self._final_transcript      = ""
        self._partial_transcript    = ""
        self._utterance_start: float | None = None
        self._connect_time: float | None    = None
        self._last_audio_time               = time.monotonic()
        self._audio_chunk_count             = 0

    async def connect(self) -> bool:
        """Connect to Deepgram streaming API. Returns True on success."""
        api_key = settings.DEEPGRAM_API_KEY
        if not api_key:
            log.warning("streaming_stt.no_api_key", session_id=self.session_id)
            return False

        # Deepgram nova-2 model, English, 16kHz PCM linear16
        # endpointing=200: declare utterance end after 200ms silence (reduced from 400ms)
        # utterance_end_ms=1500: force finalization after 1.5s of silence as backup
        # interim_results: stream partials for UI feedback
        # vad_events: receive SpeechStarted / UtteranceEnd events
        url = (
            "wss://api.deepgram.com/v1/listen"
            "?model=nova-2"
            "&language=en"
            "&encoding=linear16"
            "&sample_rate=16000"
            "&channels=1"
            "&smart_format=true"
            "&endpointing=200"
            "&utterance_end_ms=1500"
            "&interim_results=true"
            "&vad_events=true"
        )

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    extra_headers={"Authorization": f"Token {api_key}"},
                    ping_interval=20,
                    ping_timeout=10,
                ),
                timeout=5.0,
            )
            self._connected   = True
            self._connect_time = time.monotonic()
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"dg_recv_{self.session_id}",
            )
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(),
                name=f"dg_keepalive_{self.session_id}",
            )
            log.info("streaming_stt.connected", session_id=self.session_id)
            return True

        except Exception as exc:
            log.error("streaming_stt.connect_failed",
                      session_id=self.session_id, error=str(exc))
            self._connected = False
            return False

    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Send PCM audio frame to Deepgram.
        Called for each 20ms PCM frame from the AudioWorklet.
        Should NOT be called while AI is speaking (frontend handles this).
        """
        if not self._connected or not self._ws:
            if not self._reconnecting:
                asyncio.create_task(self._auto_reconnect())
            return
        try:
            await self._ws.send(audio_bytes)
            self._last_audio_time = time.monotonic()
            self._audio_chunk_count += 1
            if self._utterance_start is None:
                self._utterance_start = time.monotonic()
        except Exception as exc:
            log.warning("streaming_stt.send_failed",
                        session_id=self.session_id, error=str(exc))
            self._connected = False
            if not self._reconnecting:
                asyncio.create_task(self._auto_reconnect())

    async def reset_for_new_turn(self) -> None:
        """
        Prepare for the next turn WITHOUT closing the Deepgram connection.

        With PCM streaming (AudioWorklet), there are no WebM container headers
        to reset — the same raw PCM stream continues uninterrupted.
        This is intentionally different from the WebM/MediaRecorder behaviour
        where resetting the Deepgram WS was required for each new recording.

        Only resets: transcript accumulation buffers + utterance timing.
        The Deepgram WebSocket stays alive and connected.
        """
        self._final_transcript   = ""
        self._partial_transcript = ""
        self._utterance_start    = None
        self._audio_chunk_count  = 0
        log.debug("streaming_stt.reset_for_new_turn",
                  session_id=self.session_id, ws_kept_alive=True)

    async def _auto_reconnect(self) -> None:
        """Reconnect Deepgram WebSocket on unexpected disconnect."""
        if self._reconnecting:
            return
        self._reconnecting = True
        log.info("streaming_stt.reconnecting", session_id=self.session_id)

        for task in [self._receive_task, self._keepalive_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        for attempt in range(1, 4):
            await asyncio.sleep(0.3 * attempt)
            try:
                success = await self.connect()
                if success:
                    log.info("streaming_stt.reconnected",
                             session_id=self.session_id, attempt=attempt)
                    self._reconnecting = False
                    return
            except Exception as exc:
                log.warning("streaming_stt.reconnect_attempt_failed",
                            session_id=self.session_id, attempt=attempt, error=str(exc))

        log.error("streaming_stt.reconnect_exhausted", session_id=self.session_id)
        self._reconnecting = False

    async def _keepalive_loop(self) -> None:
        """
        Send KeepAlive to Deepgram every 8s when no audio is flowing.
        Required during AI speech — frontend stops sending PCM but we want
        the Deepgram connection to remain open for the next candidate turn.
        """
        try:
            while self._connected and self._ws:
                await asyncio.sleep(8)
                if not self._connected or not self._ws:
                    break
                since_last = time.monotonic() - self._last_audio_time
                if since_last > 5:
                    try:
                        await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self) -> None:
        """Receive transcript events from Deepgram WebSocket."""
        try:
            async for msg in self._ws:
                try:
                    data = json.loads(msg)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = data.get("type", "")

                if msg_type == "Results":
                    await self._handle_result(data)
                elif msg_type == "SpeechStarted":
                    self._utterance_start = time.monotonic()
                elif msg_type == "UtteranceEnd":
                    await self._finalize_utterance()
                elif msg_type == "Error":
                    log.error("streaming_stt.deepgram_error",
                              session_id=self.session_id, error=data)

        except websockets.exceptions.ConnectionClosed:
            log.info("streaming_stt.connection_closed", session_id=self.session_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("streaming_stt.receive_error",
                      session_id=self.session_id, error=str(exc))
        finally:
            self._connected = False

    async def _handle_result(self, data: dict) -> None:
        """Process a transcript result from Deepgram."""
        try:
            channel      = data.get("channel", {})
            alt          = channel.get("alternatives", [{}])[0]
            transcript   = alt.get("transcript", "").strip()
            is_final     = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if not transcript:
                return

            if is_final:
                self._final_transcript += (" " + transcript) if self._final_transcript else transcript
                self._partial_transcript = ""
                log.debug("streaming_stt.final_chunk",
                          session_id=self.session_id,
                          chunk=transcript[:60],
                          accumulated=len(self._final_transcript))
            else:
                self._partial_transcript = transcript
                if self.on_partial:
                    combined = self._final_transcript
                    if self._partial_transcript:
                        combined = (combined + " " + self._partial_transcript).strip()
                    await self.on_partial(combined)

            if speech_final:
                await self._finalize_utterance()

        except Exception as exc:
            log.warning("streaming_stt.result_error",
                        session_id=self.session_id, error=str(exc))

    async def _finalize_utterance(self) -> None:
        """Utterance complete — trigger the interview pipeline."""
        full = self._final_transcript.strip()
        if self._partial_transcript:
            full = (full + " " + self._partial_transcript).strip()

        if not full:
            return

        stt_ms = 0
        if self._utterance_start:
            stt_ms = int((time.monotonic() - self._utterance_start) * 1000)

        log.info("streaming_stt.utterance_complete",
                 session_id=self.session_id,
                 chars=len(full),
                 stt_ms=stt_ms,
                 transcript_preview=full[:80])

        track_stt_call(
            session_id=self.session_id,
            latency_ms=stt_ms,
            audio_duration_sec=stt_ms / 1000,
            status="success",
        )

        transcript              = full
        self._final_transcript  = ""
        self._partial_transcript = ""
        self._utterance_start   = None

        await self.on_utterance_complete(transcript, stt_ms)

    async def close(self) -> None:
        """Clean shutdown."""
        self._connected = False
        for task in [self._receive_task, self._keepalive_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        log.info("streaming_stt.closed", session_id=self.session_id)

    @property
    def is_connected(self) -> bool:
        return self._connected


# ── Per-session STT registry ──────────────────────────────────────────────────

_active_stt: dict[str, DeepgramStreamingSTT] = {}


async def get_or_create_streaming_stt(
    session_id: str,
    on_utterance_complete: Callable[[str, int], Awaitable[None]],
    on_partial:            Callable[[str], Awaitable[None]] | None = None,
) -> DeepgramStreamingSTT | None:
    existing = _active_stt.get(session_id)
    if existing and existing.is_connected:
        return existing

    stt = DeepgramStreamingSTT(
        session_id=session_id,
        on_utterance_complete=on_utterance_complete,
        on_partial=on_partial,
    )
    success = await stt.connect()
    if success:
        _active_stt[session_id] = stt
        return stt
    return None


async def close_streaming_stt(session_id: str) -> None:
    stt = _active_stt.pop(session_id, None)
    if stt:
        await stt.close()
