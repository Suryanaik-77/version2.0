"""
strategy_engine.py — Interviewer mode transition logic.

This is NOT an orchestrator. It is a pure state machine.
No I/O, no LLM calls, no external dependencies.

Contract:
  decide_mode(state, signals, eval_scores) -> InterviewerMode

Called:
  1. Immediately (with inline_signals only) for optional pre-eval mode hint
  2. After async eval completes (with full eval_scores) for confirmed mode update

The confirmed mode takes effect on TURN N+1, not the current turn.
"""
from __future__ import annotations

from app.models.session import (
    InlineSignals,
    InterviewerMode,
    SessionState,
    Correctness,
    SignalLevel,
)


# ── Mode transition table ──────────────────────────────────────────────────────
# Each function takes current state + signals and returns the next mode.
# Simple, readable, no hidden logic.

def decide_mode_from_eval(
    current_mode: InterviewerMode,
    eval_scores: dict[str, int],
    inline_signals: InlineSignals | None,
    consecutive_weak: int = 0,
    consecutive_strong: int = 0,
) -> InterviewerMode:
    """
    Primary mode decision — called after async eval completes.
    Uses formal scores + inline signals for a confirmed mode decision.

    consecutive_weak/strong: how many turns in a row the candidate
    scored weak/strong. Used to prevent mode thrashing.
    """
    avg = _avg_score(eval_scores)
    correctness = eval_scores.get("correctness", 5)
    depth = eval_scores.get("depth", 5)

    # Catastrophic wrong answer — always recover regardless of mode
    if correctness <= 3:
        return InterviewerMode.RECOVERING

    # Strong performance
    if avg >= 7.5 and depth >= 7:
        if current_mode == InterviewerMode.DEEPENING and consecutive_strong >= 2:
            return InterviewerMode.PRESSURE
        if current_mode in (InterviewerMode.PROBING, InterviewerMode.ESCALATING):
            return InterviewerMode.DEEPENING
        if current_mode == InterviewerMode.PRESSURE:
            return InterviewerMode.TRANSITIONING  # mastered — move on
        return current_mode  # already in a strong-answer mode

    # Weak/shallow performance
    if avg < 5.0 or depth <= 3:
        if current_mode == InterviewerMode.RECOVERING:
            # Still struggling after recovery — stay in recovery
            if consecutive_weak >= 2:
                return InterviewerMode.RECOVERING
        if current_mode == InterviewerMode.PRESSURE:
            return InterviewerMode.RECOVERING
        return InterviewerMode.ESCALATING

    # Partial performance (5.0–7.4) — hold current mode or minor adjustment
    if current_mode == InterviewerMode.PRESSURE and avg >= 6.5:
        return InterviewerMode.PRESSURE  # stay under pressure

    if current_mode == InterviewerMode.RECOVERING and avg >= 5.5:
        return InterviewerMode.PROBING  # candidate recovered

    if current_mode == InterviewerMode.ESCALATING and avg >= 6.0:
        return InterviewerMode.PROBING  # escalation worked

    return current_mode


def decide_mode_from_inline(
    current_mode: InterviewerMode,
    signals: InlineSignals,
) -> InterviewerMode | None:
    """
    Lightweight mode hint from inline signals only.
    Called during/immediately after question generation — before eval arrives.

    Returns None if no immediate mode change is warranted.
    This is advisory — the full eval decision supersedes this.
    """
    # Immediate recovery trigger: clearly wrong answer detected
    if (signals.correctness == Correctness.WRONG
            and signals.vagueness == SignalLevel.HIGH):
        if current_mode not in (InterviewerMode.RECOVERING,):
            return InterviewerMode.RECOVERING

    # Memorization detected in non-recovery mode
    if signals.memorization_suspected and current_mode == InterviewerMode.PROBING:
        return InterviewerMode.ESCALATING

    return None


def should_transition_topic(
    current_mode: InterviewerMode,
    consecutive_strong: int,
    turn_count: int,
    topic_turn_count: int,
) -> bool:
    """
    Returns True ONLY when the topic is genuinely exhausted.

    Rules:
    - Never transition based on fixed turn count alone.
    - Only after: PRESSURE mode + 2+ consecutive strong answers on same topic.
    - Or: topic has been covered for 5+ turns with avg score >= 7.
    """
    if current_mode == InterviewerMode.PRESSURE and consecutive_strong >= 2:
        return True

    if topic_turn_count >= 5 and consecutive_strong >= 3:
        return True

    return False


def compute_pressure_level(
    current_mode: InterviewerMode,
    consecutive_strong: int,
) -> int:
    """
    Returns pressure intensity 0–3.
    Used by question_engine to calibrate question difficulty.
    """
    if current_mode == InterviewerMode.PRESSURE:
        return min(3, 1 + consecutive_strong)
    if current_mode == InterviewerMode.DEEPENING:
        return 1
    if current_mode == InterviewerMode.ESCALATING:
        return 0
    return 0


# ── Internal helpers ───────────────────────────────────────────────────────────

def _avg_score(scores: dict[str, int]) -> float:
    if not scores:
        return 5.0
    return sum(scores.values()) / len(scores)
