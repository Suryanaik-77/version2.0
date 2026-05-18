"""
tts.py — Text-to-Speech provider adapter.

Swap contract: changing provider requires ONLY swapping this file.
No consuming module changes.

Required interface:
  synthesize(text: str) -> bytes           # PCM or MP3 audio bytes
  stream_synthesize(text: str) -> AsyncIterator[bytes]  # streamed audio chunks

Providers:
  OpenAITTS   — real, uses openai.audio.speech.create
  InworldTTS  — stub (Inworld API is not public standard)
  SilenceTTS  — test adapter, generates silence at correct bitrate

Audio format contract (all providers must conform):
  Format: MP3 (openai) or PCM 16kHz/16-bit/mono (fallback/test)
  Clients handle both via content-type header in SESSION_START event.

Timeout: 2000ms per sentence synthesis.
  If exceeded: use silence (don't block pipeline).
"""
from __future__ import annotations

import asyncio
import struct
import math
import time
from collections.abc import AsyncIterator
from typing import Protocol

import structlog

from app.config import get_settings
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Provider protocol ─────────────────────────────────────────────────────────

class TTSProvider(Protocol):
    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        """Synthesize full text to audio bytes. Used for sentence-level synthesis."""
        ...

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        """Stream audio bytes. Used for sub-sentence streaming when available."""
        ...

    @property
    def audio_format(self) -> str:
        """Returns MIME type: 'audio/mpeg' or 'audio/pcm;rate=16000'"""
        ...


# ── OpenAI TTS (real implementation) ─────────────────────────────────────────

class OpenAITTS:
    """
    OpenAI TTS via openai.audio.speech.create.
    Returns MP3 bytes per sentence.

    Voice selection: 'nova' — clear, professional, not overly robotic.
    Alternatives: 'alloy', 'echo', 'fable', 'onyx', 'shimmer'.
    """

    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._voice = "nova"
        self._model = "tts-1"   # tts-1 is faster than tts-1-hd; use hd for quality
        self.audio_format = "audio/mpeg"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        """
        Synthesize text to MP3 bytes.
        Timeout: 2000ms. On timeout: return silence chunk.
        """
        if not text.strip():
            return _silence_pcm(500)

        t_start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.audio.speech.create(
                    model=self._model,
                    voice=self._voice,
                    input=text,
                    response_format="mp3",
                    speed=1.0,
                ),
                timeout=2.0,
            )
            audio_bytes = response.content
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            record_event(
                "tts.synthesized",
                session_id=session_id,
                chars=len(text),
                bytes=len(audio_bytes),
                latency_ms=elapsed_ms,
            )
            return audio_bytes

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.warning("tts.timeout", session_id=session_id, elapsed_ms=elapsed_ms)
            record_event("tts.timeout", session_id=session_id)
            # Return silence so playback doesn't hang
            return _silence_pcm(300)

        except asyncio.CancelledError:
            raise  # Barge-in — propagate

        except Exception as exc:
            log.error("tts.error", session_id=session_id, error=str(exc))
            record_event("tts.error", session_id=session_id, error=str(exc))
            return _silence_pcm(300)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        """
        Streams audio bytes using OpenAI's streaming TTS API.
        First bytes arrive faster than non-streaming for long sentences.
        """
        try:
            async with await self._client.audio.speech.with_streaming_response.create(
                model=self._model,
                voice=self._voice,
                input=text,
                response_format="mp3",
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=4096):
                    yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("tts.stream_error", session_id=session_id, error=str(exc))
            yield _silence_pcm(300)


# ── Inworld TTS stub ──────────────────────────────────────────────────────────

class InworldTTS:
    """
    Inworld AI TTS adapter stub.
    Interface correct; implementation pending Inworld API credentials.
    Falls back to SilenceTTS for testing.
    """

    def __init__(self) -> None:
        self.audio_format = "audio/mpeg"
        self._api_key = settings.INWORLD_API_KEY
        log.warning("tts.inworld_stub_active — using silence fallback")

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        # TODO: implement Inworld REST/gRPC call
        # API endpoint: TBD per Inworld credentials
        # Expected response: MP3 or OGG bytes
        return _silence_pcm(int(len(text.split()) * 0.45 * 1000))  # ~0.45s/word

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        audio = await self.synthesize(text, session_id)
        # Yield in 4KB chunks to simulate streaming
        chunk_size = 4096
        for i in range(0, len(audio), chunk_size):
            yield audio[i:i + chunk_size]
            await asyncio.sleep(0)  # yield control


# ── Silence TTS (test/fallback) ───────────────────────────────────────────────

class SilenceTTS:
    """
    Test adapter. Generates silence PCM at correct duration for text length.
    Estimates 150 words/min speaking rate.
    Enables full pipeline testing without API keys.
    """

    def __init__(self) -> None:
        self.audio_format = "audio/pcm;rate=16000"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        # Estimate: 150 words/min = 2.5 words/sec
        words = len(text.split())
        duration_ms = max(300, int(words / 2.5 * 1000))
        # Add small artificial delay to simulate API latency
        await asyncio.sleep(min(duration_ms / 1000 * 0.3, 0.3))
        return _silence_pcm(duration_ms)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        audio = await self.synthesize(text, session_id)
        # Stream in 20ms chunks (realistic for real TTS)
        chunk_samples = int(16000 * 0.02)    # 20ms
        chunk_bytes = chunk_samples * 2       # 16-bit
        for i in range(0, len(audio), chunk_bytes):
            yield audio[i:i + chunk_bytes]
            await asyncio.sleep(0.02)  # simulate real-time pacing


# ── Provider factory ──────────────────────────────────────────────────────────

def get_tts_provider() -> TTSProvider:
    """
    Returns configured TTS provider.
    To swap: change TTS_PROVIDER env var (Phase 5 adds config routing).
    Currently: OpenAI TTS if API key present, else SilenceTTS.
    """
    if settings.OPENAI_API_KEY:
        return OpenAITTS()
    return SilenceTTS()


# ── Audio utilities ───────────────────────────────────────────────────────────

def _silence_pcm(duration_ms: int, sample_rate: int = 16000) -> bytes:
    """Generate silence as 16-bit PCM bytes."""
    num_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))
