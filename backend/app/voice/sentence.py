"""
sentence.py — Sentence boundary detection for streaming TTS chunking.

THE latency-critical component for first_audio_ms.

Design rules:
- Yield the first chunk ASAP, even if it's short.
- Never buffer the full response before yielding.
- Natural-sounding boundaries > grammatically perfect sentences.
- Every stage is cancellation-safe (CancelledError propagates cleanly).

Boundary detection hierarchy:
  1. Hard boundaries: [.!?] after MIN_HARD_CHARS — primary, high confidence
  2. Soft boundaries: [,;] after MIN_SOFT_CHARS — long clauses feel natural
  3. Force boundaries: after MAX_CHARS — prevents runaway buffering
  4. Final drain: yield remaining buffer regardless of boundary

Why not use NLTK/spaCy?
  Loading those libraries adds 100-300ms startup overhead and
  requires CPU work in the hot path. Regex is 10-100x faster here.
  The quality tradeoff is acceptable for TTS sentence units.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import structlog

log = structlog.get_logger(__name__)

# Tune these for perceived naturalness vs. chunk size tradeoff
MIN_HARD_CHARS  = 25    # Don't split on "Yes." — too short to TTS separately
MIN_SOFT_CHARS  = 70    # Minimum chars before comma can trigger a split
MAX_CHARS       = 220   # Force-split runaway sentences (e.g., LLM goes verbose)

# Hard sentence end: [.!?] followed by whitespace or end-of-string
_HARD_END = re.compile(r'[.!?](?:\s+|$)')

# Common abbreviations — prevent splitting mid-abbreviation
_ABBREVIATIONS = re.compile(
    r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e|Fig|Eq|Sec|Ref|al)\.'
    r'|(?<!\d)\.(?!\d)'   # not decimal numbers like 3.14
    r'|[A-Z]\.'           # single-letter abbreviations like U.S.A.
    , re.IGNORECASE
)

# Soft boundary: comma or semicolon
_SOFT_END = re.compile(r'[,;](?:\s+)')


async def stream(
    token_iter: AsyncIterator[str],
    min_hard_chars: int = MIN_HARD_CHARS,
    min_soft_chars: int = MIN_SOFT_CHARS,
    max_chars: int = MAX_CHARS,
) -> AsyncIterator[str]:
    """
    Async generator: token stream → sentence chunks.

    Designed for direct piping to TTS synthesis:
      async for sentence in sentence.stream(token_stream):
          audio = await tts.synthesize(sentence)

    Cancellation: CancelledError from token_iter propagates cleanly.
    The generator finalizes (yields remaining buffer) if the token stream
    ends normally; does NOT yield on CancelledError.
    """
    buf: list[str] = []

    try:
        async for token in token_iter:
            buf.append(token)
            text = "".join(buf)

            if len(text) < min_hard_chars:
                continue

            # ── Hard boundary check ───────────────────────────────────────────
            if _is_hard_end(text):
                chunk = text.strip()
                if chunk:
                    yield chunk
                buf = []
                continue

            # ── Soft boundary check ───────────────────────────────────────────
            if len(text) >= min_soft_chars and _SOFT_END.search(text):
                # Only split at the LAST soft boundary in the buffer
                match = list(_SOFT_END.finditer(text))[-1]
                split_at = match.end()
                chunk = text[:split_at].strip()
                remainder = text[split_at:]
                if chunk:
                    yield chunk
                buf = [remainder] if remainder else []
                continue

            # ── Force split ────────────────────────────────────────────────────
            if len(text) >= max_chars:
                # Find last word boundary to avoid mid-word splits
                split_pos = text.rfind(" ")
                if split_pos > min_hard_chars:
                    chunk = text[:split_pos].strip()
                    remainder = text[split_pos:].strip()
                    if chunk:
                        yield chunk
                    buf = [remainder] if remainder else []
                else:
                    yield text.strip()
                    buf = []

    except asyncio.CancelledError:
        # Barge-in: do NOT yield partial buffer — audio was interrupted
        buf.clear()
        raise
    else:
        # Normal stream end: yield remaining buffer (final fragment)
        if buf:
            remaining = "".join(buf).strip()
            if remaining:
                yield remaining


def _is_hard_end(text: str) -> bool:
    """
    Returns True if the text ends with a sentence-final punctuation mark,
    excluding abbreviations and decimal numbers.
    """
    matches = list(_HARD_END.finditer(text))
    if not matches:
        return False

    last_match = matches[-1]
    # Check if the character before the period is part of an abbreviation
    prefix = text[:last_match.start() + 1]
    return not _ABBREVIATIONS.search(prefix)


# ── Sentence metrics for observability ───────────────────────────────────────

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class SentenceStreamMetrics:
    """Records per-sentence timing for latency instrumentation."""
    session_id: str
    sentences: list[dict] = field(default_factory=list)
    _stream_start: float = field(default_factory=time.monotonic, init=False)

    def record(self, sentence_idx: int, text: str, elapsed_ms: float) -> None:
        self.sentences.append({
            "idx": sentence_idx,
            "chars": len(text),
            "words": len(text.split()),
            "elapsed_ms": int(elapsed_ms),
        })

    def first_sentence_ms(self) -> int | None:
        if self.sentences:
            return self.sentences[0]["elapsed_ms"]
        return None


async def stream_with_metrics(
    token_iter: AsyncIterator[str],
    metrics: SentenceStreamMetrics,
) -> AsyncIterator[tuple[int, str]]:
    """
    Instrumented sentence stream. Yields (sentence_index, text) tuples.
    Used by voice pipeline for checkpoint marking.
    """
    t_start = time.monotonic()
    idx = 0
    async for sentence in stream(token_iter):
        elapsed = (time.monotonic() - t_start) * 1000
        metrics.record(idx, sentence, elapsed)
        yield idx, sentence
        idx += 1
