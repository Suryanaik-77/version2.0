"""
block1_context_reducer.py — Context Reducer Rules (Block 1 of 5)

Deterministic rules for reducing each input type before token budgeting.
Pure functions. No I/O. No LLM calls. All operations are O(n).

Integration point:
    Called by block3_strategies.py before budget allocation.
    Input: raw CandidateMemory, dict eval_scores, InlineSignals, str transcript
    Output: ReducedContext (typed, bounded data)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from app.models.session import (
    CandidateMemory, InlineSignals, Correctness, SignalLevel,
    Contradiction, TopicSummary, BuzzwordRecord,
)


# ── Output type ────────────────────────────────────────────────────────────────

@dataclass
class ReducedContext:
    """
    All inputs after reduction. Passed to budget allocator and formatter.
    Every field is bounded by reduction rules — no unbounded strings.
    """
    # Memory (all bounded by rules below)
    active_contradiction: str | None       # max 120 chars, first unresolved only
    weak_topics: list[str]                 # max 2 items, format: "topic (score)"
    strong_topics: list[str]               # max 2 items, name only
    recent_claims: list[str]               # max 3 items, each max 60 chars
    repeated_buzzwords: list[str]          # max 2 items, name only

    # Eval (aggregated, not per-dimension)
    eval_avg: float | None                 # single number, None if no eval
    eval_correctness: int | None           # 0-10, preserved fully
    eval_depth: int | None                 # 0-10, preserved fully
    eval_flags: list[str]                  # max 2, each max 40 chars

    # Signals (priority-ranked, deduplicated)
    signal_correctness: str | None         # "WRONG" | "PARTIAL" | "CORRECT" | None
    signal_vagueness: str | None           # "HIGH" | None (LOW suppressed)
    signal_missing_mechanism: str | None   # max 50 chars, or None
    signal_memorization: bool              # bool only
    signal_contradiction_ref: str | None   # max 80 chars, or None

    # Transcript
    transcript_truncated: str              # max 300 chars (preserves last sentence)
    transcript_word_count: int             # for budget sizing


# ── Rule A: Memory reduction ──────────────────────────────────────────────────

# Rule A1: Keep only the FIRST unresolved contradiction.
# Rationale: Multiple contradictions overwhelm the question — one at a time.
# Rule A2: Truncate each side to 60 chars max.
# Rule A3: If resolved, drop it entirely.

def reduce_contradictions(memory: CandidateMemory) -> str | None:
    unresolved = [c for c in memory.contradictions if not c.resolved]
    if not unresolved:
        return None
    c = unresolved[0]                            # Rule A1: first only
    side_a = _truncate(c.statement_a, 60)        # Rule A2
    side_b = _truncate(c.statement_b, 60)        # Rule A2
    return f'said "{side_a}" then "{side_b}"'


# Rule A4: Keep max 2 weak topics, sorted by lowest score.
# Rule A5: Format as "topic (score)" — drop all other fields.
# Rule A6: If avg_score > 6.5, do not include (topic recovered).

def reduce_weak_topics(memory: CandidateMemory) -> list[str]:
    eligible = [t for t in memory.weak_topics if t.avg_score <= 6.5]  # Rule A6
    top2 = sorted(eligible, key=lambda t: t.avg_score)[:2]            # Rule A4
    return [f"{t.topic} ({t.avg_score:.1f})" for t in top2]           # Rule A5


# Rule A7: Keep max 2 strong topics, name only.
# Rationale: Strong topics are used as pressure targets, not for context.

def reduce_strong_topics(memory: CandidateMemory) -> list[str]:
    top2 = sorted(memory.strong_topics, key=lambda t: t.avg_score, reverse=True)[:2]
    return [t.topic for t in top2]                                     # Rule A7: name only


# Rule A8: Keep last 3 claims only (most recent = most relevant).
# Rule A9: Truncate each to 60 chars.
# Rule A10: Skip generic claims (< 20 chars or all lowercase = likely noise).

def reduce_claims(memory: CandidateMemory) -> list[str]:
    candidates = [
        c for c in memory.claims
        if len(c) >= 20 and not c.islower()                            # Rule A10
    ]
    last3 = candidates[-3:]                                            # Rule A8
    return [_truncate(c, 60) for c in last3]                          # Rule A9


# Rule A11: Keep max 2 buzzwords with count >= 2 (repeated only).
# Rule A12: Name only — drop context and count.
# Rule A13: Drop if count == 1 (single use is not a pattern).

def reduce_buzzwords(memory: CandidateMemory) -> list[str]:
    repeated = [b for b in memory.buzzwords if b.count >= 2]          # Rule A13
    top2 = sorted(repeated, key=lambda b: b.count, reverse=True)[:2]  # Rule A11
    return [b.term for b in top2]                                      # Rule A12


# ── Rule B: Eval reduction ────────────────────────────────────────────────────

# Rule B1: Collapse 7 dimension scores to 3 values: avg, correctness, depth.
# Rationale: avg captures overall, correctness and depth drive mode decisions.
# Rule B2: Preserve correctness fully — wrong answers are critical signals.
# Rule B3: Preserve depth fully — depth drives DEEPENING vs ESCALATING.
# Rule B4: Drop: accuracy, completeness, clarity, maturity, ownership.
# Rule B5: Keep max 2 flags, each truncated to 40 chars.

def reduce_eval(eval_scores: dict | None) -> tuple[float | None, int | None, int | None, list[str]]:
    """Returns (avg, correctness, depth, flags)."""
    if not eval_scores:
        return None, None, None, []
    scores = {k: v for k, v in eval_scores.items() if k != 'flags'}
    avg = sum(scores.values()) / len(scores) if scores else None       # Rule B1
    correctness = eval_scores.get('correctness')                       # Rule B2
    depth = eval_scores.get('depth')                                   # Rule B3
    raw_flags = eval_scores.get('flags', []) or []
    flags = [_truncate(str(f), 40) for f in raw_flags[:2]]            # Rule B5
    return avg, correctness, depth, flags


# ── Rule C: Inline signal reduction ──────────────────────────────────────────

# Rule C1: Signal priority order: contradiction > wrong > vagueness > memorization.
#          Lower-priority signals are suppressed when higher ones are active.
# Rule C2: SignalLevel.LOW vagueness → drop (not actionable).
# Rule C3: Correctness.UNKNOWN → drop (no signal to surface).
# Rule C4: Correctness.CORRECT → drop (only wrong/partial are actionable).
# Rule C5: Truncate missing_mechanism to 50 chars.
# Rule C6: Truncate contradiction_with to 80 chars.

def reduce_signals(signals: InlineSignals | None) -> tuple[
    str | None,   # correctness
    str | None,   # vagueness
    str | None,   # missing_mechanism
    bool,         # memorization
    str | None,   # contradiction_ref
]:
    if signals is None:
        return None, None, None, False, None

    # Rule C1: contradiction takes all priority
    if signals.contradiction_with:
        return (
            None,                                                        # C1: suppress correctness
            None,                                                        # C1: suppress vagueness
            None,                                                        # C1: suppress mechanism
            False,                                                       # C1: suppress memorization
            _truncate(signals.contradiction_with, 80),                  # C6
        )

    correctness = None
    if signals.correctness == Correctness.WRONG:
        correctness = "WRONG"
    elif signals.correctness == Correctness.PARTIAL:
        correctness = "PARTIAL"
    # Rule C3/C4: UNKNOWN and CORRECT are dropped

    vagueness = None
    if signals.vagueness == SignalLevel.HIGH:                           # Rule C2
        vagueness = "HIGH"

    mechanism = None
    if signals.missing_mechanism:
        mechanism = _truncate(signals.missing_mechanism, 50)           # Rule C5

    # Rule C1: if wrong answer, suppress memorization (redundant)
    memorization = signals.memorization_suspected and correctness != "WRONG"

    return correctness, vagueness, mechanism, memorization, None


# ── Rule D: Transcript reduction ─────────────────────────────────────────────

# Rule D1: Max 300 chars. If transcript > 300 chars, keep LAST 300 chars.
# Rationale: last part of the answer is most recent and most relevant.
# Rule D2: Never cut mid-sentence — find the last sentence boundary before cut.
# Rule D3: Preserve any numbers, tool names, and ownership phrases in the kept portion.

def reduce_transcript(transcript: str) -> tuple[str, int]:
    """Returns (reduced_text, word_count)."""
    word_count = len(transcript.split())
    if len(transcript) <= 300:                                         # Rule D1
        return transcript.strip(), word_count

    # Rule D2: find last sentence boundary in first 300 chars
    candidate = transcript[-300:]
    # Try to find sentence start to avoid mid-sentence cut
    sentence_start = re.search(r'(?<=[.!?])\s+[A-Z]', candidate)
    if sentence_start:
        candidate = candidate[sentence_start.start():].strip()
    return candidate, word_count


# ── Main reducer ──────────────────────────────────────────────────────────────

def reduce(
    memory: CandidateMemory | None,
    eval_scores: dict | None,
    signals: InlineSignals | None,
    transcript: str,
) -> ReducedContext:
    """
    Apply all reduction rules to produce a ReducedContext.
    Always succeeds — missing inputs produce empty/None values.
    """
    mem = memory or CandidateMemory(session_id="")

    eval_avg, eval_corr, eval_depth, eval_flags = reduce_eval(eval_scores)
    sig_corr, sig_vague, sig_mech, sig_mem, sig_contra = reduce_signals(signals)
    transcript_reduced, word_count = reduce_transcript(transcript)

    return ReducedContext(
        active_contradiction=reduce_contradictions(mem),
        weak_topics=reduce_weak_topics(mem),
        strong_topics=reduce_strong_topics(mem),
        recent_claims=reduce_claims(mem),
        repeated_buzzwords=reduce_buzzwords(mem),
        eval_avg=eval_avg,
        eval_correctness=eval_corr,
        eval_depth=eval_depth,
        eval_flags=eval_flags,
        signal_correctness=sig_corr,
        signal_vagueness=sig_vague,
        signal_missing_mechanism=sig_mech,
        signal_memorization=sig_mem,
        signal_contradiction_ref=sig_contra,
        transcript_truncated=transcript_reduced,
        transcript_word_count=word_count,
    )


# ── Utility ────────────────────────────────────────────────────────────────────

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"
