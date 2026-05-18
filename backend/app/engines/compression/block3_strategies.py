"""
block3_strategies.py — Compression Strategy Rules (Block 3 of 5)

Deterministic strategies that serialize ReducedContext → section strings.
Each strategy enforces its section's token budget via block2_budget.enforce().

Strategies in priority order:
  S1: Contradiction collapse     → 1 line, both sides, max 35 tokens
  S2: Signal merge               → 1 line, priority-ranked, max 45 tokens
  S3: Memory bullet collapse     → N lines, each max 1 line, total max 55 tokens
  S4: Eval collapse              → 1 line: avg + key dimensions only, max 25 tokens
  S5: Example passthrough        → utterance string, max 20 tokens
  S6: Trend passthrough          → 1 line, max 12 tokens
  S7: Avoid passthrough          → list of last 2 questions, max 18 tokens
  S8: Transcript tail-preserve   → last N chars to fill transcript budget

Priority rules:
  - Contradiction always serialized fully when present (rule A2 already limits size)
  - Signals serialized as single compact line — no multi-line expansion
  - Memory: max 4 lines total (1 contradiction + 1 weak + 1 strong + 1 claims)
  - If memory > budget: drop claims first, then strong, then weak
  - Eval: never more than 1 line
"""
from __future__ import annotations

from app.engines.compression.block1_context_reducer import ReducedContext
from app.engines.compression.block2_budget import AllocatedBudgets, enforce, tokens


# ── S1: Contradiction collapse ────────────────────────────────────────────────
# Rule: 1 line. Both sides in quotes. No editorializing.
# Format: 'said "[A]" then "[B]"'

def serialize_contradiction(ctx: ReducedContext, budget: int) -> str:
    if not ctx.active_contradiction:
        return ""
    line = f"CONTRADICTION: {ctx.active_contradiction}"
    return enforce(line, budget)


# ── S2: Signal merge ──────────────────────────────────────────────────────────
# Rule: Merge all active signals into ONE compact line.
# Priority: contradiction_ref > wrong > partial > vagueness > mechanism > memorization
# Drop: CORRECT, UNKNOWN, LOW vagueness (already filtered by block1)
# Format: "SIGNALS: [type]: [detail]; [type]: [detail]"

def serialize_signals(ctx: ReducedContext, budget: int) -> str:
    parts = []

    if ctx.signal_contradiction_ref:
        parts.append(f"prior-claim: \"{ctx.signal_contradiction_ref}\"")

    elif ctx.signal_correctness == "WRONG":
        if ctx.signal_missing_mechanism:
            parts.append(f"wrong-answer; missing: {ctx.signal_missing_mechanism}")
        else:
            parts.append("wrong-answer")

    elif ctx.signal_correctness == "PARTIAL":
        if ctx.signal_missing_mechanism:
            parts.append(f"partial; missing: {ctx.signal_missing_mechanism}")
        else:
            parts.append("partial-answer")

    elif ctx.signal_vagueness == "HIGH":
        if ctx.signal_missing_mechanism:
            parts.append(f"vague; missing: {ctx.signal_missing_mechanism}")
        else:
            parts.append("vague-answer")

    if ctx.signal_memorization:
        parts.append("memorized-sounding")

    if not parts:
        return ""

    line = "SIGNALS: " + "; ".join(parts)
    return enforce(line, budget)


# ── S3: Memory bullet collapse ────────────────────────────────────────────────
# Strategy: 4 possible lines. Drop in reverse priority if over budget.
# Priority: contradiction_line > weak > strong > claims > buzzwords
# Rule: Merge all weak topics into 1 line (not per-item).
# Rule: Merge all strong topics into 1 line.
# Rule: Collapse repeated buzzwords into 1 line.
# Rule: Claims are last — dropped first if over budget.

def serialize_memory(ctx: ReducedContext, budget: int) -> str:
    lines: list[str] = []

    # Line 1: weak topics (merged into 1 line)
    if ctx.weak_topics:
        lines.append("WEAK: " + ", ".join(ctx.weak_topics))

    # Line 2: strong topics (name only, merged)
    if ctx.strong_topics:
        lines.append("STRONG: " + ", ".join(ctx.strong_topics))

    # Line 3: recent claims (last 3, semicolon-separated)
    if ctx.recent_claims:
        claims_line = "CLAIMED: " + "; ".join(f'"{c}"' for c in ctx.recent_claims)
        lines.append(claims_line)

    # Line 4: buzzwords (name only, comma-separated)
    if ctx.repeated_buzzwords:
        lines.append("REPEATED-NO-MECHANISM: " + ", ".join(ctx.repeated_buzzwords))

    if not lines:
        return ""

    # Enforce budget by dropping lines from the end (lowest priority first)
    while lines:
        candidate = "\n".join(lines)
        if tokens(candidate) <= budget:
            return candidate
        lines.pop()  # drop lowest-priority line

    return ""


# ── S4: Eval collapse ─────────────────────────────────────────────────────────
# Rule: Never more than 1 line.
# Rule: avg + correctness + depth only. Drop 4 other dimensions.
# Rule: Flags appended if present and budget allows.
# Format: "EVAL: avg=N.N; correct=N; depth=N [flags: f1, f2]"

def serialize_eval(ctx: ReducedContext, budget: int) -> str:
    if ctx.eval_avg is None:
        return ""

    parts = [f"avg={ctx.eval_avg:.1f}"]
    if ctx.eval_correctness is not None:
        parts.append(f"correct={ctx.eval_correctness}")
    if ctx.eval_depth is not None:
        parts.append(f"depth={ctx.eval_depth}")

    line = "EVAL: " + "; ".join(parts)

    if ctx.eval_flags and tokens(line) < budget - 5:
        flags_text = ", ".join(ctx.eval_flags)
        extended = f"{line} [{flags_text}]"
        if tokens(extended) <= budget:
            line = extended

    return enforce(line, budget)


# ── S5: Example passthrough ───────────────────────────────────────────────────
# Rule: Single utterance string, surrounded by consistent format.
# Format: 'EXAMPLE: "[utterance]"'
# Budget: 20 tokens — already enforced by corpus.py utterance length.

def serialize_example(utterance: str, budget: int) -> str:
    if not utterance:
        return ""
    line = f'EXAMPLE: "{utterance}"'
    return enforce(line, budget)


# ── S6: Trend passthrough ─────────────────────────────────────────────────────
# Rule: 1 line only. Already short by design in prompts.py.
# No further compression needed — just enforce budget.

def serialize_trend(trend_text: str, budget: int) -> str:
    if not trend_text:
        return ""
    return enforce(trend_text, budget)


# ── S7: Avoid passthrough ─────────────────────────────────────────────────────
# Rule: Last 2 questions only. Each on its own line.
# Format: "AVOID:\n- [q1]\n- [q2]"

def serialize_avoid(recent_questions: list[str], budget: int) -> str:
    if not recent_questions:
        return ""
    last2 = recent_questions[-2:]
    lines = ["AVOID:"] + [f"- {q}" for q in last2]
    text = "\n".join(lines)
    return enforce(text, budget)


# ── S8: Transcript tail-preserve ─────────────────────────────────────────────
# Rule: Preserve the LAST N chars up to the budget, not the first N.
# Rationale: the most recent part of the answer is the most relevant anchor.
# Format: just the transcript text, no label.

def serialize_transcript(ctx: ReducedContext, budget: int) -> str:
    transcript = ctx.transcript_truncated
    max_chars = int(budget * 4.0)  # chars from token budget

    if len(transcript) <= max_chars:
        return transcript

    # Tail-preserve: take last max_chars, find sentence boundary
    tail = transcript[-max_chars:]
    # Find first sentence start in the tail to avoid mid-sentence cut
    match = __import__('re').search(r'(?<=[.!?])\s+[A-Z]', tail)
    if match:
        tail = tail[match.start():].strip()
    return tail
