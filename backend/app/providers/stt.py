"""
stt.py — Speech-to-Text provider adapter.

Swap contract: changing provider requires ONLY swapping this file.

Strategy:
  - STT is NOT truly streaming for most providers (they require complete audio).
  - We use VAD (vad.py) to detect utterance end, THEN call STT.
  - Partial transcript feedback is simulated by showing "listening..."  on client.
  - True streaming STT (WebSocket-based) can replace this in Phase 5.

Providers:
  OpenAIWhisperSTT — real, uses openai.audio.transcriptions.create
  MistralSTT       — stub (Mistral STT API details not public standard)
  SilenceSTT       — test adapter, returns fixed transcript

Timeout: 700ms (our STT budget).
On timeout: return empty transcript (triggers fallback question).
"""
from __future__ import annotations

import asyncio
import time
from typing import Protocol

import structlog

from app.config import get_settings
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Provider protocol ─────────────────────────────────────────────────────────

class STTProvider(Protocol):
    async def transcribe(self, audio_bytes: bytes, session_id: str = "") -> str:
        """Transcribe complete audio to text. Returns empty string on failure."""
        ...


# ── OpenAI Whisper STT ────────────────────────────────────────────────────────

class OpenAIWhisperSTT:
    """
    OpenAI Whisper via openai.audio.transcriptions.create.
    Accepts PCM 16kHz/16-bit/mono as WAV container.

    Latency: typically 300-600ms for 5-10s utterance.
    Timeout: 700ms (STT budget). On timeout: returns empty string.
    """

    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def transcribe(self, audio_bytes: bytes, session_id: str = "") -> str:
        if not audio_bytes or len(audio_bytes) < 100:
            return ""

        t_start = time.monotonic()

        try:
            import io
            # Wrap PCM bytes in a WAV container (Whisper requires a container format)
            wav_bytes = _pcm_to_wav(audio_bytes)
            audio_file = io.BytesIO(wav_bytes)
            audio_file.name = "audio.wav"  # OpenAI needs a filename hint

            response = await asyncio.wait_for(
                self._client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=audio_file,
                    language="en",
                    response_format="text",
                ),
                timeout=3.0,  # gpt-4o-mini-transcribe needs more time than whisper
            )

            transcript = str(response).strip()
            elapsed_ms = int((time.monotonic() - t_start) * 1000)

            record_event(
                "stt.transcribed",
                session_id=session_id,
                chars=len(transcript),
                audio_bytes=len(audio_bytes),
                latency_ms=elapsed_ms,
            )
            log.debug(
                "stt.done",
                session_id=session_id,
                latency_ms=elapsed_ms,
                chars=len(transcript),
            )

            return transcript

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.warning("stt.timeout", session_id=session_id, elapsed_ms=elapsed_ms)
            record_event("stt.timeout", session_id=session_id, latency_ms=elapsed_ms)
            return ""

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            log.error("stt.error", session_id=session_id, error=str(exc))
            record_event("stt.error", session_id=session_id)
            return ""


# ── Mistral STT stub ──────────────────────────────────────────────────────────

class MistralSTT:
    """
    Mistral AI STT adapter stub.
    Interface correct; implementation pending Mistral STT API details.
    Falls back to OpenAI Whisper if available.
    """

    def __init__(self) -> None:
        self._api_key = settings.MISTRAL_API_KEY
        self._fallback = OpenAIWhisperSTT() if settings.OPENAI_API_KEY else EchoSTT()
        log.warning("stt.mistral_stub_active — using fallback")

    async def transcribe(self, audio_bytes: bytes, session_id: str = "") -> str:
        # TODO: implement Mistral STT call when API is available
        return await self._fallback.transcribe(audio_bytes, session_id)


# ── Echo STT (test adapter) ───────────────────────────────────────────────────

class EchoSTT:
    """
    Test adapter. Returns a fixed technical transcript for pipeline testing.
    Simulates realistic STT latency (50-200ms).
    """

    def __init__(self, fixed_response: str = "") -> None:
        self._response = fixed_response or (
            "I used common centroid layout for matching the differential pair in the OTA. "
            "The devices were interdigitated with dummy cells on both sides."
        )

    async def transcribe(self, audio_bytes: bytes, session_id: str = "") -> str:
        # Simulate STT latency proportional to audio length
        from app.voice.vad import estimate_duration_ms
        audio_duration_ms = estimate_duration_ms(audio_bytes)
        simulated_latency = min(audio_duration_ms * 0.05, 300)  # 5% of audio duration, max 300ms
        await asyncio.sleep(simulated_latency / 1000)
        return self._response


# ── Provider factory ──────────────────────────────────────────────────────────

def get_stt_provider() -> STTProvider:
    """
    Returns configured STT provider.
    Uses OpenAI Whisper if API key present, else EchoSTT for testing.
    """
    if settings.OPENAI_API_KEY:
        return OpenAIWhisperSTT()
    return EchoSTT()


# ── WAV container builder ─────────────────────────────────────────────────────

def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1, bit_depth: int = 16) -> bytes:
    """
    Wrap raw PCM bytes in a WAV container.
    Required because Whisper and most STT providers don't accept raw PCM.
    """
    import struct
    data_size    = len(pcm_bytes)
    header_size  = 44
    file_size    = header_size + data_size - 8
    byte_rate    = sample_rate * channels * (bit_depth // 8)
    block_align  = channels * (bit_depth // 8)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        file_size,
        b"WAVE",
        b"fmt ",
        16,            # chunk size
        1,             # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bit_depth,
        b"data",
        data_size,
    )
    return header + pcm_bytes
