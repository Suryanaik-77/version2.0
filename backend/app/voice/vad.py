"""
vad.py — Voice Activity Detection.

Determines when the candidate has finished speaking so we can
send accumulated audio to STT.

Algorithm: energy-based VAD.
  - Compute RMS energy per audio chunk.
  - If energy drops below threshold for SILENCE_FRAMES consecutive frames → utterance end.
  - Also enforces MAX_UTTERANCE_DURATION to prevent unbounded buffering.

Format assumption: PCM 16-bit signed, little-endian, mono, 16kHz.
This is the standard format for browser MediaRecorder/WebAudio output.

Why not WebRTC VAD or Silero VAD?
  - WebRTC VAD requires C bindings (py-webrtcvad).
  - Silero VAD requires PyTorch (heavy).
  - Energy VAD is 50 lines, no dependencies, <0.1ms per chunk.
  - Phase 5 can upgrade if silence detection quality is insufficient.
"""
from __future__ import annotations

import audioop
import struct
import time
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# PCM 16kHz, 16-bit, mono
SAMPLE_RATE      = 16000
BYTES_PER_SAMPLE = 2
CHUNK_MS         = 20       # 20ms audio chunks from browser
CHUNK_BYTES      = int(SAMPLE_RATE * CHUNK_MS / 1000 * BYTES_PER_SAMPLE)

# VAD tuning
ENERGY_THRESHOLD  = 500     # RMS threshold for speech detection (0-32767)
SILENCE_FRAMES    = 40      # 40 frames × 20ms = 800ms silence → end of utterance
MIN_SPEECH_FRAMES = 5       # Minimum frames of speech (100ms) to consider valid
MAX_UTTERANCE_S   = 30      # Abort if candidate speaks for > 30s without pause
SPEECH_HANGOVER   = 8       # Continue recording for 8 frames after speech ends


@dataclass
class AudioAccumulator:
    """
    Per-session audio state. Created when a session's WS connects,
    lives until session ends.

    Thread/coroutine safety: single asyncio task per session owns this object.
    No locking needed.
    """
    session_id: str

    # Internal state
    _chunks: list[bytes] = field(default_factory=list)
    _silence_count: int = 0
    _speech_frame_count: int = 0
    _hangover_count: int = 0
    _utterance_start_time: float | None = None
    _is_speaking: bool = False
    _total_bytes: int = 0

    def push_chunk(self, audio_bytes: bytes) -> "VADResult":
        """
        Process one audio chunk (typically 20ms).
        Returns VADResult indicating speech state.

        Returns VADResult.UTTERANCE_COMPLETE when an utterance has ended.
        Caller should then call get_audio() to retrieve accumulated audio.
        """
        if not audio_bytes:
            return VADResult.SILENCE

        # Compute RMS energy
        energy = _rms_energy(audio_bytes)

        if energy > ENERGY_THRESHOLD:
            # Speech detected
            if not self._is_speaking:
                self._is_speaking = True
                self._utterance_start_time = time.monotonic()
                log.debug("vad.speech_start", session_id=self.session_id, energy=energy)

            self._speech_frame_count += 1
            self._silence_count = 0
            self._hangover_count = SPEECH_HANGOVER
            self._chunks.append(audio_bytes)
            self._total_bytes += len(audio_bytes)

            # Enforce max utterance duration
            if self._utterance_start_time:
                duration = time.monotonic() - self._utterance_start_time
                if duration > MAX_UTTERANCE_S:
                    log.warning("vad.max_duration_exceeded", session_id=self.session_id)
                    return VADResult.UTTERANCE_COMPLETE

            return VADResult.SPEECH

        else:
            # Silence
            if self._is_speaking or self._hangover_count > 0:
                # In hangover — still buffering for a bit after speech ends
                self._chunks.append(audio_bytes)
                self._total_bytes += len(audio_bytes)
                self._hangover_count = max(0, self._hangover_count - 1)

            self._silence_count += 1

            # Utterance complete: was speaking, now silent for long enough
            if (self._is_speaking
                    and self._speech_frame_count >= MIN_SPEECH_FRAMES
                    and self._silence_count >= SILENCE_FRAMES
                    and self._hangover_count == 0):
                log.debug(
                    "vad.utterance_complete",
                    session_id=self.session_id,
                    speech_frames=self._speech_frame_count,
                    total_bytes=self._total_bytes,
                )
                return VADResult.UTTERANCE_COMPLETE

            return VADResult.SILENCE

    def get_audio(self) -> bytes:
        """Return accumulated audio bytes and reset state."""
        audio = b"".join(self._chunks)
        self._reset()
        return audio

    def is_speaking(self) -> bool:
        return self._is_speaking

    def clear(self) -> None:
        """Called on barge-in to discard current accumulation."""
        self._reset()

    def _reset(self) -> None:
        self._chunks = []
        self._silence_count = 0
        self._speech_frame_count = 0
        self._hangover_count = 0
        self._utterance_start_time = None
        self._is_speaking = False
        self._total_bytes = 0


class VADResult:
    SILENCE            = "SILENCE"
    SPEECH             = "SPEECH"
    UTTERANCE_COMPLETE = "UTTERANCE_COMPLETE"


def _rms_energy(audio_bytes: bytes) -> float:
    """
    Compute RMS energy of 16-bit PCM audio chunk.
    Returns value in range 0–32767.
    Fast: uses audioop from stdlib.
    """
    if len(audio_bytes) < 2:
        return 0.0
    try:
        return audioop.rms(audio_bytes, 2)
    except audioop.error:
        return 0.0


def estimate_duration_ms(audio_bytes: bytes) -> int:
    """Returns duration of PCM 16-bit 16kHz mono audio in milliseconds."""
    samples = len(audio_bytes) // BYTES_PER_SAMPLE
    return int(samples / SAMPLE_RATE * 1000)
