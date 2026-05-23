"""
streaming_stt.py — Deepgram streaming STT via WebSocket.

Architecture:
  - One Deepgram WebSocket per interview session
  - Browser sends audio chunks → backend → Deepgram WS
  - Deepgram returns partial/final transcripts in real-time
  - On speech endpoint (silence detected by Deepgram), triggers pipeline

Latency advantage:
  - Batch STT: wait for silence → send blob → wait 900-1400ms → transcript
  - Streaming STT: transcription happens DURING speech → only 200ms after silence

Integration:
  - websocket.py calls stream_audio_chunk() for each binary audio frame
  - On utterance complete, callback triggers the pipeline
  - Falls back to batch STT on connection failure

Feature flag: runtime_config "stt_provider" = "deepgram" enables this.
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
      1. connect() — called when interview WebSocket opens
      2. send_audio() — called for each audio chunk from browser
      3. Deepgram sends back partials and finals via _receive_loop
      4. On speech_final → calls on_utterance_complete callback
      5. close() — called when interview WebSocket closes
    """

    def __init__(
        self,
        session_id: str,
        on_utterance_complete: Callable[[str, int], Awaitable[None]],
        on_partial: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.session_id = session_id
        self.on_utterance_complete = on_utterance_complete
        self.on_partial = on_partial
        self._ws = None
        self._receive_task = None
        self._connected = False
        self._final_transcript = ""
        self._partial_transcript = ""
        self._utterance_start = None
        self._connect_time = None

    async def connect(self) -> bool:
        """Connect to Deepgram streaming API. Returns True on success."""
        api_key = settings.DEEPGRAM_API_KEY
        if not api_key:
            log.warning("streaming_stt.no_api_key", session_id=self.session_id)
            return False

        url = (
            "wss://api.deepgram.com/v1/listen"
            "?model=nova-2"
            "&language=en"
            "&smart_format=true"
            "&endpointing=400"      # 400ms silence = utterance end
            "&interim_results=true"  # get partial transcripts
            "&vad_events=true"       # get speech start/end events
            "&encoding=linear16"
            "&sample_rate=16000"
            "&channels=1"
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
            self._connected = True
            self._connect_time = time.monotonic()
            self._receive_task = asyncio.create_task(
                self._receive_loop(),
                name=f"dg_recv_{self.session_id}",
            )
            log.info("streaming_stt.connected", session_id=self.session_id)
            return True

        except Exception as exc:
            log.error("streaming_stt.connect_failed",
                      session_id=self.session_id, error=str(exc))
            self._connected = False
            return False

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio chunk to Deepgram. Called for each browser audio frame."""
        if not self._connected or not self._ws:
            return
        try:
            await self._ws.send(audio_bytes)
            if self._utterance_start is None:
                self._utterance_start = time.monotonic()
        except Exception as exc:
            log.warning("streaming_stt.send_failed",
                        session_id=self.session_id, error=str(exc))
            self._connected = False

    async def _receive_loop(self) -> None:
        """Receive partial and final transcripts from Deepgram."""
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
                    # Deepgram detected end of utterance
                    await self._finalize_utterance()
                elif msg_type == "Error":
                    log.error("streaming_stt.deepgram_error",
                              session_id=self.session_id, error=data)

        except websockets.exceptions.ConnectionClosed:
            log.info("streaming_stt.connection_closed",
                     session_id=self.session_id)
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
            channel = data.get("channel", {})
            alt = channel.get("alternatives", [{}])[0]
            transcript = alt.get("transcript", "").strip()
            is_final = data.get("is_final", False)
            speech_final = data.get("speech_final", False)

            if not transcript:
                return

            if is_final:
                # Confirmed words — append to accumulated transcript
                self._final_transcript += (" " + transcript) if self._final_transcript else transcript
                self._partial_transcript = ""

                log.debug("streaming_stt.final_chunk",
                          session_id=self.session_id,
                          chunk=transcript[:60],
                          accumulated=len(self._final_transcript))
            else:
                # Interim/partial — may change
                self._partial_transcript = transcript

                # Send partial to frontend for display
                if self.on_partial:
                    combined = self._final_transcript
                    if self._partial_transcript:
                        combined = (combined + " " + self._partial_transcript).strip()
                    await self.on_partial(combined)

            # speech_final = Deepgram's endpoint detection (utterance complete)
            if speech_final:
                await self._finalize_utterance()

        except Exception as exc:
            log.warning("streaming_stt.result_error",
                        session_id=self.session_id, error=str(exc))

    async def _finalize_utterance(self) -> None:
        """Utterance complete — trigger the interview pipeline."""
        # Combine final + any remaining partial
        full = self._final_transcript.strip()
        if self._partial_transcript:
            full = (full + " " + self._partial_transcript).strip()

        if not full:
            return

        # Calculate STT duration
        stt_ms = 0
        if self._utterance_start:
            stt_ms = int((time.monotonic() - self._utterance_start) * 1000)

        log.info("streaming_stt.utterance_complete",
                 session_id=self.session_id,
                 chars=len(full),
                 stt_ms=stt_ms,
                 transcript_preview=full[:80])

        # Track metrics
        track_stt_call(
            session_id=self.session_id,
            latency_ms=stt_ms,
            audio_duration_sec=stt_ms / 1000,
            status="success",
        )

        # Reset for next utterance
        transcript = full
        self._final_transcript = ""
        self._partial_transcript = ""
        self._utterance_start = None

        # Trigger pipeline via callback
        await self.on_utterance_complete(transcript, stt_ms)

    async def close(self) -> None:
        """Clean shutdown — close Deepgram WebSocket."""
        self._connected = False
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
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


# ── Per-session STT instance registry ────────────────────────────────────────

_active_stt: dict[str, DeepgramStreamingSTT] = {}


async def get_or_create_streaming_stt(
    session_id: str,
    on_utterance_complete: Callable[[str, int], Awaitable[None]],
    on_partial: Callable[[str], Awaitable[None]] | None = None,
) -> DeepgramStreamingSTT | None:
    """Get existing or create new streaming STT for a session."""
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
    """Close streaming STT for a session."""
    stt = _active_stt.pop(session_id, None)
    if stt:
        await stt.close()
