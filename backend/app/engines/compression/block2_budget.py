"""
block2_budget.py — Token Budget Allocator (Block 2 of 5)

Defines the total token budget and allocates it across prompt sections.
Enforces at serialization time — sections that overflow their budget are cut.

Measured baselines (from profiling, not estimates):
    System prompt:  492 tokens (fixed — phrase filter included)
    Budget target:  700 tokens combined (GPT-4o-mini first-token optimum)
    Dynamic budget: 208 tokens available for all runtime-variable sections

Allocation approach:
    Priority order: signals > contradiction > memory > eval > example > trend > transcript
    Sections over budget are compressed further (see block3_strategies.py).
    Sections with nothing to say contribute 0 — budget rolls to transcript.
"""
from __future__ import annotations
from dataclasses import dataclass


# ── Fixed constants ────────────────────────────────────────────────────────────

SYSTEM_PROMPT_TOKENS: int = 492       # measured, not estimated. QUESTION_SYSTEM with filter.
TOTAL_TARGET_TOKENS:  int = 700       # combined system + user prompt target
DYNAMIC_BUDGET:       int = TOTAL_TARGET_TOKENS - SYSTEM_PROMPT_TOKENS  # = 208

CHARS_PER_TOKEN: float = 4.0          # conservative estimate for English technical text


# ── Section budgets ────────────────────────────────────────────────────────────
# All values are token counts.
# Sections are ordered by priority — higher priority sections are funded first.
# If a section has nothing to say, its allocation rolls to TRANSCRIPT_MIN.

@dataclass(frozen=True)
class SectionBudgets:
    # P1 — Signals block (correctness + vagueness + mechanism + memorization)
    # Cannot compress further without losing critical behavioral signal.
    signals:        int = 45

    # P2 — Active contradiction (single unresolved contradiction, both sides)
    # Preserved fully after rule A2 truncation — max 35 tokens post-reduction.
    contradiction:  int = 35

    # P3 — Memory (weak + strong + claims + buzzwords, post-reduction)
    # Already compressed by block1. This is the budget for the formatted output.
    memory:         int = 55

    # P4 — Eval summary (avg + correctness + depth + flags)
    # Only 3 numbers + optional flags after reduction. Hard to exceed 25 tokens.
    eval_summary:   int = 25

    # P5 — Corpus example (1 utterance, ~10 words)
    # Fixed format — always exactly 1 example, never more.
    example:        int = 20

    # P6 — Trend note (1 line, only when consecutive >= 2)
    # Short by design. Drops to 0 when no clear trend.
    trend:          int = 12

    # P7 — Avoid block (last 2 questions, ~8 words each)
    avoid:          int = 18

    # P8 — Transcript (remainder after P1-P7 funded)
    # Minimum guaranteed even when all higher sections are full.
    # Maximum is whatever budget is left after P1-P7.
    transcript_min: int = 30
    transcript_max: int = 100  # hard cap regardless of available budget

    @property
    def fixed_overhead(self) -> int:
        """Total tokens consumed by P1-P7 when all are fully used."""
        return (
            self.signals
            + self.contradiction
            + self.memory
            + self.eval_summary
            + self.example
            + self.trend
            + self.avoid
        )

    @property
    def transcript_budget(self) -> int:
        """Remaining budget for transcript after fixed overhead."""
        remaining = DYNAMIC_BUDGET - self.fixed_overhead
        return max(self.transcript_min, min(self.transcript_max, remaining))


BUDGETS = SectionBudgets()


# ── Budget check ──────────────────────────────────────────────────────────────

def tokens(text: str) -> int:
    """Estimate token count for a text string."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def fits(text: str, budget_tokens: int) -> bool:
    """Returns True if text fits within the token budget."""
    return tokens(text) <= budget_tokens


def enforce(text: str, budget_tokens: int) -> str:
    """
    Hard-enforce a token budget by truncating to max chars.
    Tries to truncate at a word boundary to avoid mid-word cuts.
    """
    max_chars = int(budget_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Find last word boundary
    last_space = truncated.rfind(' ')
    if last_space > max_chars * 0.8:   # don't truncate more than 20% to find word boundary
        truncated = truncated[:last_space]
    return truncated + "…"


# ── Dynamic allocation ─────────────────────────────────────────────────────────

@dataclass
class AllocatedBudgets:
    """
    Per-session runtime allocation.
    When a section is empty (None or empty string), its tokens roll to transcript.
    """
    signals:       int
    contradiction: int
    memory:        int
    eval_summary:  int
    example:       int
    trend:         int
    avoid:         int
    transcript:    int
    total:         int


def allocate(
    has_signals: bool,
    has_contradiction: bool,
    has_memory: bool,
    has_eval: bool,
    has_example: bool,
    has_trend: bool,
    has_avoid: bool,
) -> AllocatedBudgets:
    """
    Compute per-section allocations for this turn.
    Empty sections yield their budget to transcript (up to transcript_max).
    """
    freed = 0
    if not has_signals:       freed += BUDGETS.signals
    if not has_contradiction: freed += BUDGETS.contradiction
    if not has_memory:        freed += BUDGETS.memory
    if not has_eval:          freed += BUDGETS.eval_summary
    if not has_example:       freed += BUDGETS.example
    if not has_trend:         freed += BUDGETS.trend
    if not has_avoid:         freed += BUDGETS.avoid

    transcript = min(
        BUDGETS.transcript_max,
        BUDGETS.transcript_budget + freed,
    )

    total = (
        (BUDGETS.signals       if has_signals       else 0)
        + (BUDGETS.contradiction if has_contradiction else 0)
        + (BUDGETS.memory        if has_memory        else 0)
        + (BUDGETS.eval_summary  if has_eval          else 0)
        + (BUDGETS.example       if has_example       else 0)
        + (BUDGETS.trend         if has_trend         else 0)
        + (BUDGETS.avoid         if has_avoid         else 0)
        + transcript
    )

    return AllocatedBudgets(
        signals       = BUDGETS.signals       if has_signals       else 0,
        contradiction = BUDGETS.contradiction if has_contradiction else 0,
        memory        = BUDGETS.memory        if has_memory        else 0,
        eval_summary  = BUDGETS.eval_summary  if has_eval          else 0,
        example       = BUDGETS.example       if has_example       else 0,
        trend         = BUDGETS.trend         if has_trend         else 0,
        avoid         = BUDGETS.avoid         if has_avoid         else 0,
        transcript    = transcript,
        total         = total,
    )
