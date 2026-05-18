"""
block4_fallback.py — Failure Handling Rules (Block 4 of 5)

Defines all failure modes and their deterministic fallback behavior.
The compressor NEVER raises an exception to the caller —
it returns a valid CompressedPrompt in all cases.

Failure hierarchy:
  FULL mode     → all sections available, normal compression applied
  MINIMAL mode  → transcript + last question only (memory/eval/signals all missing)
  TRANSCRIPT mode → transcript only (last question also unavailable)

Triggers for fallback:
  → memory read timeout / Redis unavailable       → MINIMAL
  → eval not yet available (async not complete)   → skip eval section (not MINIMAL)
  → signals empty (turn 1)                        → skip signals (not MINIMAL)
  → transcript empty after STT failure            → TRANSCRIPT with fallback text
  → compression total > hard cap (750 tokens)     → MINIMAL
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.session import VLSIDomain, InterviewerMode


class CompressionMode(str, Enum):
    FULL        = "FULL"        # all sections populated and within budget
    MINIMAL     = "MINIMAL"     # transcript + last question only
    TRANSCRIPT  = "TRANSCRIPT"  # transcript only (last question missing)


HARD_CAP_TOKENS: int = 750   # if compressed total exceeds this → downgrade to MINIMAL


# ── Failure handling rules ────────────────────────────────────────────────────

# Rule F1: Memory missing
# → Skip memory section entirely (return empty string for memory)
# → Do NOT downgrade to MINIMAL — interview continues without memory context
# → Log: memory_missing=True in CompressedPrompt

# Rule F2: Eval missing (async not yet complete)
# → Skip eval section
# → Do NOT downgrade to MINIMAL — common on turn 1
# → Log: eval_missing=True

# Rule F3: Signals empty (turn 1, no prior answer)
# → Skip signals section
# → Do NOT downgrade to MINIMAL — expected on turn 1
# → Log: signals_empty=True

# Rule F4: Transcript empty (STT failure)
# → Use domain-specific fallback transcript (see below)
# → Log: transcript_fallback=True

# Rule F5: Compressed total exceeds HARD_CAP_TOKENS
# → Downgrade to MINIMAL mode
# → Log: mode=MINIMAL, reason="budget_exceeded"

# Rule F6: Any unexpected exception in compression
# → Downgrade to MINIMAL mode
# → Log: mode=MINIMAL, reason="exception:[msg]"

# Rule F7: MINIMAL mode content
# → system_context: QUESTION_SYSTEM (unchanged)
# → memory:         "" (empty)
# → eval:           "" (empty)
# → signals:        "" (empty)
# → transcript:     last 200 chars of transcript (or fallback if empty)
# → last_question:  last question if available, else ""

# Rule F8: TRANSCRIPT mode content (last resort)
# → All sections empty except transcript
# → Transcript uses fallback text if original is empty


# ── Fallback transcripts (STT failure path) ───────────────────────────────────
# Domain-specific fallback ensures interviewer generates a valid opening question
# rather than an empty prompt. Used only when transcript is empty after STT.

FALLBACK_TRANSCRIPTS: dict[VLSIDomain, str] = {
    VLSIDomain.ANALOG_LAYOUT: (
        "I've worked on analog layout with matching and parasitic considerations."
    ),
    VLSIDomain.PHYSICAL_DESIGN: (
        "I've worked on physical design including timing closure and CTS."
    ),
    VLSIDomain.DESIGN_VERIFICATION: (
        "I've worked on verification including UVM environments and coverage."
    ),
}

FALLBACK_TRANSCRIPT_DEFAULT = "I have experience in VLSI design."


def get_fallback_transcript(domain: VLSIDomain | None) -> str:
    if domain and domain in FALLBACK_TRANSCRIPTS:
        return FALLBACK_TRANSCRIPTS[domain]
    return FALLBACK_TRANSCRIPT_DEFAULT


# ── Fallback builder ──────────────────────────────────────────────────────────

@dataclass
class FallbackResult:
    mode: CompressionMode
    transcript: str
    last_question: str
    reason: str
    memory_missing: bool = False
    eval_missing: bool = False
    signals_empty: bool = False
    transcript_fallback: bool = False


def build_fallback(
    transcript: str,
    domain: VLSIDomain | None,
    last_question: str,
    reason: str,
    mode: CompressionMode = CompressionMode.MINIMAL,
) -> FallbackResult:
    """
    Builds the fallback state for MINIMAL or TRANSCRIPT mode.
    Never raises. Always returns a valid FallbackResult.
    """
    transcript_fallback = False
    if not transcript or not transcript.strip():
        transcript = get_fallback_transcript(domain)   # Rule F4/F8
        transcript_fallback = True

    # Rule F7: MINIMAL mode — last 200 chars of transcript
    if mode == CompressionMode.MINIMAL:
        transcript = transcript[-200:].strip()

    return FallbackResult(
        mode=mode,
        transcript=transcript,
        last_question=last_question or "",
        reason=reason,
        transcript_fallback=transcript_fallback,
    )


def check_hard_cap(total_tokens: int) -> bool:
    """Returns True if total exceeds hard cap — caller should downgrade to MINIMAL."""
    return total_tokens > HARD_CAP_TOKENS    # Rule F5
