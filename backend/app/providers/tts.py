"""
tts.py — Text-to-Speech provider adapter.

Swap contract: changing provider requires ONLY swapping this file.
No consuming module changes.

Required interface:
  synthesize(text: str) -> bytes           # PCM or MP3 audio bytes
  stream_synthesize(text: str) -> AsyncIterator[bytes]  # streamed audio chunks

Providers:
  InworldTTS  — production, uses Inworld REST API
  DeepgramTTS — alternative, Deepgram Aura REST API
  OpenAITTS   — alternative, OpenAI TTS REST API
  SilenceTTS  — test/fallback, generates silence

Audio format:
  InworldTTS / DeepgramTTS / OpenAITTS → MP3 (audio/mpeg)
  SilenceTTS → PCM 16kHz/16-bit/mono   (audio/pcm;rate=16000)

Connection pooling:
  InworldTTS and DeepgramTTS share a module-level persistent httpx.AsyncClient.
  Eliminates TCP+TLS handshake overhead (~80-150ms) per call.
  Clients are created lazily on first use and reused for the process lifetime.
  If a client is closed unexpectedly, it is recreated automatically.

Timeout: 3000ms per sentence. On timeout: silence substituted.
"""
from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import AsyncIterator
from typing import Protocol

import httpx
import structlog

from app.config import get_settings
from app.observability.metrics import record_event
from app.observability.call_tracker import track_tts_call

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Provider protocol ─────────────────────────────────────────────────────────

class TTSProvider(Protocol):
    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        ...

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        ...

    @property
    def audio_format(self) -> str:
        ...


# ── Shared persistent HTTP clients ────────────────────────────────────────────
#
# One client per TTS provider, reused across all sessions and turns.
# httpx.AsyncClient with keepalive maintains the TCP connection to the provider.
# Saves ~80-150ms per call vs creating a new client each time.
#
# Limits: 4 concurrent connections per provider (interview concurrency is low).

_inworld_client:  httpx.AsyncClient | None = None
_deepgram_client: httpx.AsyncClient | None = None


def _get_inworld_client() -> httpx.AsyncClient:
    global _inworld_client
    if _inworld_client is None or _inworld_client.is_closed:
        _inworld_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=4,
                keepalive_expiry=30.0,
            ),
            http2=False,  # Inworld API compatibility
        )
        log.info("tts.inworld_client_created")
    return _inworld_client


def _get_deepgram_client() -> httpx.AsyncClient:
    global _deepgram_client
    if _deepgram_client is None or _deepgram_client.is_closed:
        _deepgram_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=10.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=4,
                keepalive_expiry=30.0,
            ),
        )
        log.info("tts.deepgram_client_created")
    return _deepgram_client


async def close_tts_clients() -> None:
    """
    Call from application shutdown to cleanly close all TTS HTTP connections.
    Wire into FastAPI lifespan shutdown if desired.
    """
    global _inworld_client, _deepgram_client
    for client in [_inworld_client, _deepgram_client]:
        if client and not client.is_closed:
            try:
                await client.aclose()
            except Exception:
                pass
    _inworld_client  = None
    _deepgram_client = None


# ── Inworld TTS ───────────────────────────────────────────────────────────────

class InworldTTS:
    """
    Inworld AI TTS via REST API.
    Uses module-level persistent httpx client (no TCP/TLS overhead per call).
    """

    def __init__(self, voice: str = "Sarah", model: str = "inworld-tts-1.5-mini") -> None:
        self._api_key     = getattr(settings, 'INWORLD_API_KEY', '') or ''
        self._voice       = voice
        self._model       = model
        self.audio_format = "audio/mpeg"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        if not text.strip() or not self._api_key:
            return _silence_pcm(500)

        t_start = time.monotonic()
        log.debug("tts.inworld_start",
                  voice=self._voice, chars=len(text), session_id=session_id)

        try:
            client = _get_inworld_client()
            resp   = await client.post(
                "https://api.inworld.ai/tts/v1/voice",
                headers={
                    "Authorization": f"Basic {self._api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "text":    text[:2000],
                    "voiceId": self._voice,
                    "modelId": self._model,
                },
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                import base64
                data        = resp.json()
                audio_bytes = base64.b64decode(data.get("audioContent", ""))
            else:
                audio_bytes = resp.content

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            record_event("tts.synthesized", session_id=session_id,
                         provider="inworld", voice=self._voice,
                         chars=len(text), bytes=len(audio_bytes), latency_ms=elapsed_ms)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="inworld", status="success")
            log.info("tts.inworld", voice=self._voice, latency_ms=elapsed_ms,
                     chars=len(text))
            return audio_bytes

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.error("tts.inworld_error",
                      error=str(exc), session_id=session_id, latency_ms=elapsed_ms)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="inworld",
                           status="failure", error=str(exc))
            return _silence_pcm(300)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        audio = await self.synthesize(text, session_id)
        for i in range(0, len(audio), 4096):
            yield audio[i:i + 4096]
            await asyncio.sleep(0)


# ── Deepgram Aura TTS ─────────────────────────────────────────────────────────

class DeepgramTTS:
    """
    Deepgram Aura TTS via REST API.
    Uses module-level persistent httpx client.
    """

    def __init__(self, voice: str = "aura-asteria-en") -> None:
        self._api_key     = getattr(settings, 'DEEPGRAM_API_KEY', '') or ''
        self._voice       = voice
        self.audio_format = "audio/mpeg"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        if not text.strip() or not self._api_key:
            return _silence_pcm(500)

        t_start = time.monotonic()
        try:
            client = _get_deepgram_client()
            resp   = await client.post(
                f"https://api.deepgram.com/v1/speak?model={self._voice}",
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Content-Type":  "application/json",
                },
                json={"text": text[:2000]},
            )
            resp.raise_for_status()
            audio_bytes = resp.content
            elapsed_ms  = int((time.monotonic() - t_start) * 1000)
            record_event("tts.synthesized", session_id=session_id,
                         provider="deepgram", voice=self._voice,
                         chars=len(text), bytes=len(audio_bytes), latency_ms=elapsed_ms)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="deepgram", status="success")
            log.info("tts.deepgram", voice=self._voice, latency_ms=elapsed_ms,
                     chars=len(text))
            return audio_bytes

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.error("tts.deepgram_error", error=str(exc), session_id=session_id)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="deepgram",
                           status="failure", error=str(exc))
            return _silence_pcm(300)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        audio = await self.synthesize(text, session_id)
        for i in range(0, len(audio), 4096):
            yield audio[i:i + 4096]
            await asyncio.sleep(0)


# ── OpenAI TTS ────────────────────────────────────────────────────────────────

class OpenAITTS:
    """OpenAI TTS — uses openai SDK (manages its own connection pool)."""

    def __init__(self) -> None:
        from openai import AsyncOpenAI
        self._client      = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._voice       = "nova"
        self._model       = "tts-1"
        self.audio_format = "audio/mpeg"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        if not text.strip():
            return _silence_pcm(500)
        t_start = time.monotonic()
        try:
            response   = await asyncio.wait_for(
                self._client.audio.speech.create(
                    model=self._model, voice=self._voice,
                    input=text, response_format="mp3", speed=1.0,
                ),
                timeout=3.0,
            )
            audio_bytes = response.content
            elapsed_ms  = int((time.monotonic() - t_start) * 1000)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="openai", status="success")
            return audio_bytes
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="openai",
                           status="failure", error="timeout")
            return _silence_pcm(300)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            track_tts_call(session_id=session_id, latency_ms=elapsed_ms,
                           char_count=len(text), provider="openai",
                           status="failure", error=str(exc))
            return _silence_pcm(300)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        try:
            async with await self._client.audio.speech.with_streaming_response.create(
                model=self._model, voice=self._voice,
                input=text, response_format="mp3",
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=4096):
                    yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("tts.openai_stream_error", session_id=session_id, error=str(exc))
            yield _silence_pcm(300)


# ── Silence TTS (test / fallback) ─────────────────────────────────────────────

class SilenceTTS:
    """Generates silence — used for testing without API keys."""

    def __init__(self) -> None:
        self.audio_format = "audio/pcm;rate=16000"

    async def synthesize(self, text: str, session_id: str = "") -> bytes:
        words        = len(text.split())
        duration_ms  = max(300, int(words / 2.5 * 1000))
        await asyncio.sleep(min(duration_ms / 1000 * 0.1, 0.15))
        return _silence_pcm(duration_ms)

    async def stream_synthesize(self, text: str, session_id: str = "") -> AsyncIterator[bytes]:
        audio      = await self.synthesize(text, session_id)
        chunk_bytes = int(16000 * 0.02) * 2  # 20ms at 16kHz 16-bit
        for i in range(0, len(audio), chunk_bytes):
            yield audio[i:i + chunk_bytes]
            await asyncio.sleep(0.02)


# ── Provider factory ──────────────────────────────────────────────────────────

def get_tts_provider() -> TTSProvider:
    """
    Returns the configured TTS provider. Reads from Redis runtime config.
    Changes take effect immediately (no restart needed).
    """
    from app.core.runtime_config import get

    if not get("tts_enabled", True):
        return SilenceTTS()

    provider = get("tts_provider", "openai")
    voice    = get("tts_voice", "")

    if provider == "inworld":
        iw_key = getattr(settings, 'INWORLD_API_KEY', '')
        if iw_key:
            return InworldTTS(voice=voice or "Sarah")
        log.warning("tts.inworld_no_key — falling back")

    if provider == "deepgram":
        dg_key = getattr(settings, 'DEEPGRAM_API_KEY', '')
        if dg_key:
            return DeepgramTTS(voice=voice or "aura-asteria-en")
        log.warning("tts.deepgram_no_key — falling back")

    if settings.OPENAI_API_KEY:
        tts = OpenAITTS()
        if voice:
            tts._voice = voice
        return tts

    return SilenceTTS()


# ── Audio utilities ───────────────────────────────────────────────────────────

def _silence_pcm(duration_ms: int, sample_rate: int = 16000) -> bytes:
    """Generate silence as 16-bit PCM bytes."""
    num_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))
