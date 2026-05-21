"""
suspicion.py — Suspicion scoring engine.

Ported from monolith. Aggregates all anti-cheat signals into a single
suspicion score (0-100) with integrity level classification.

Signal sources:
  - Browser anti-cheat events (tab switch, paste, devtools, AI extension)
  - Behavioral analysis (clean speech, pronoun vanishing, pause variance)
  - Smooth talker detection (confident + shallow knowledge)
  - Answer sophistication (expert terms above resume level)
  - Contradiction patterns
  - Camera/gaze events (head turned, eyes away, phone detected)

Called post-session or on-demand from admin dashboard.
"""
from __future__ import annotations

import structlog

from app.observability.behavioral import (
    analyze_pause_consistency,
    score_answer_sophistication,
)

log = structlog.get_logger(__name__)


def count_active_signals(
    anticheat_events: list[dict],
    behavioral_flags_all: list[str],
    smooth_talker_detected: bool = False,
    honest_admission_count: int = 0,
    total_scored_turns: int = 0,
    dangerous_fake_count: int = 0,
    contradiction_count: int = 0,
    above_level_count: int = 0,
) -> int:
    """
    Count distinct suspicion signal types active in the session.
    Each signal type counted at most once.
    """
    count = 0

    # Browser anti-cheat signals (each type counts once)
    event_types = {e.get("type", e.get("event_type", "")) for e in anticheat_events}
    for signal_type in [
        "tab_hidden", "tab_switch", "clipboard_paste", "paste_event",
        "dom_overlay", "screen_share", "canary_triggered",
        "head_turned", "eye_away", "split_screen",
        "ai_answer_overlay", "ai_extension_detected", "ai_extension",
        "phone_detected",
    ]:
        if signal_type in event_types:
            count += 1

    # Behavioral flags
    if "suspiciously_clean_speech" in behavioral_flags_all:
        count += 1
    if "personal_pronouns_vanished" in behavioral_flags_all:
        count += 1
    if "low_pause_variance" in behavioral_flags_all:
        count += 1
    if "self_corrections_vanished" in behavioral_flags_all:
        count += 1

    # Pattern signals
    if smooth_talker_detected:
        count += 1
    if total_scored_turns >= 8 and honest_admission_count == 0:
        count += 1  # never admitted not knowing anything
    if dangerous_fake_count >= 3:
        count += 1
    if contradiction_count > 0:
        count += 1
    if above_level_count > 0:
        count += 1

    return count


def compute_suspicion_score(
    anticheat_events: list[dict],
    turn_history: list[dict],
    pause_history: list[float] | None = None,
    smooth_talker_signals: list[str] | None = None,
    resume_level: str = "trained_fresher",
) -> dict:
    """
    Compute aggregated suspicion score from all signal sources.

    Args:
        anticheat_events: list of anti-cheat events from Redis
        turn_history: list of turn dicts with behavioral_flags, eval data
        pause_history: list of thinking pause durations
        smooth_talker_signals: accumulated smooth talker signals
        resume_level: candidate's resume level for sophistication check

    Returns:
        dict with suspicion_score, integrity_level, signal_count, flags
    """
    suspicion = 0.0
    flags: list[str] = []
    smooth_talker_signals = smooth_talker_signals or []

    # ── Browser anti-cheat events ────────────────────────────────────────
    for ev in anticheat_events:
        event_type = ev.get("type", ev.get("event_type", ""))
        turn = ev.get("turn", ev.get("ctx", {}).get("turn", "?"))

        if event_type in ("tab_hidden", "tab_switch"):
            suspicion += 8
            flags.append(f"Tab switch at turn {turn}")

        elif event_type in ("clipboard_paste", "paste_event"):
            suspicion += 15
            flags.append(f"Paste event at turn {turn}")

        elif event_type in ("dom_overlay", "canary_triggered"):
            suspicion += 20
            flags.append(f"AI browser extension detected at turn {turn}")

        elif event_type == "screen_share":
            suspicion += 12
            flags.append(f"Screen sharing at turn {turn}")

        elif event_type in ("ai_answer_overlay",):
            suspicion += 25
            flags.append(f"AI answer overlay detected on screen — cheating tool active")

        elif event_type in ("ai_extension_detected", "ai_extension"):
            suspicion += 20
            flags.append("AI browser extension detected")

        elif event_type == "head_turned":
            suspicion += 8
            flags.append(f"Candidate looked away from screen at turn {turn}")

        elif event_type == "eye_away":
            suspicion += 10
            flags.append(f"Eyes looking away while facing camera at turn {turn}")

        elif event_type == "split_screen":
            suspicion += 8
            flags.append(f"Split screen detected at turn {turn}")

        elif event_type == "phone_detected":
            suspicion += 12
            flags.append(f"Phone detected near candidate at turn {turn}")

        elif event_type == "devtools_opened":
            suspicion += 20
            flags.append(f"DevTools opened at turn {turn}")

    # ── Behavioral flags aggregation ─────────────────────────────────────
    all_flags: list[str] = []
    for turn in turn_history:
        all_flags.extend(turn.get("behavioral_flags", []))

    clean_count = sum(1 for f in all_flags if f == "suspiciously_clean_speech")
    if clean_count >= 3:
        suspicion += clean_count * 8
        flags.append(f"Filler words vanished in {clean_count} answers")

    pronoun_count = sum(1 for f in all_flags if f == "personal_pronouns_vanished")
    if pronoun_count >= 2:
        suspicion += pronoun_count * 7
        flags.append(f"Personal pronouns vanished in {pronoun_count} answers")

    correction_count = sum(1 for f in all_flags if f == "self_corrections_vanished")
    if correction_count >= 2:
        suspicion += correction_count * 5
        flags.append(f"Self-correction pattern disappeared in {correction_count} answers")

    if "low_pause_variance" in all_flags:
        suspicion += 15
        flags.append("Identical thinking pause across all difficulty levels")

    # ── Pause consistency ────────────────────────────────────────────────
    if pause_history:
        pause_analysis = analyze_pause_consistency(pause_history)
        if pause_analysis.get("suspicious"):
            suspicion += 20
            flags.append(
                f"Earpiece/proxy pattern: pause stddev={pause_analysis['stddev']}s "
                f"across {pause_analysis['sample_size']} questions"
            )

    # ── Instant answers on hard questions ────────────────────────────────
    instant_hard = [
        f for f in all_flags
        if f.startswith("instant_answer_on_")
    ]
    if instant_hard:
        suspicion += len(instant_hard) * 8
        flags.append(f"Instant answers on {len(instant_hard)} hard question(s)")

    # ── Honest admission check ───────────────────────────────────────────
    eval_qualities = [
        (t.get("eval_scores", {}) or {}).get("quality", "")
        for t in turn_history
    ]
    honest_count = sum(1 for q in eval_qualities if q == "honest_admission")
    scored_count = len([t for t in turn_history if t.get("eval_scores")])
    if scored_count >= 8 and honest_count == 0:
        suspicion += 12
        flags.append("Zero honest admissions in full interview")

    # ── Dangerous fake pattern ───────────────────────────────────────────
    df_count = sum(
        1 for t in turn_history
        if (t.get("eval_scores", {}) or {}).get("quadrant") == "dangerous_fake"
    )
    if df_count >= 3:
        suspicion += df_count * 8
        flags.append(f"Confident+wrong pattern in {df_count} turns")

    # ── Answer length spikes ─────────────────────────────────────────────
    spike_count = sum(
        1 for f in all_flags
        if f == "answer_length_spike_on_hard_question"
    )
    if spike_count:
        suspicion += spike_count * 10
        flags.append("Answer length spiked on hard questions")

    # ── Smooth talker ────────────────────────────────────────────────────
    if len(smooth_talker_signals) >= 3:
        suspicion += len(smooth_talker_signals) * 5
        flags.append(f"Smooth talker pattern: {'; '.join(smooth_talker_signals[:3])}")

    # ── Above level ──────────────────────────────────────────────────────
    above_level_count = sum(
        1 for t in turn_history if t.get("above_level")
    )
    if above_level_count:
        suspicion += above_level_count * 8
        flags.append("Answer sophistication above calibrated level")

    # ── Contradictions ───────────────────────────────────────────────────
    contradiction_count = sum(
        1 for t in turn_history if t.get("contradiction_inconsistency")
    )
    if contradiction_count:
        suspicion += contradiction_count * 12
        flags.append(f"Contradicted earlier answers in {contradiction_count} turn(s)")

    # ── Compute final result ─────────────────────────────────────────────
    suspicion = min(100, suspicion)

    signal_count = count_active_signals(
        anticheat_events=anticheat_events,
        behavioral_flags_all=all_flags,
        smooth_talker_detected=len(smooth_talker_signals) >= 3,
        honest_admission_count=honest_count,
        total_scored_turns=scored_count,
        dangerous_fake_count=df_count,
        contradiction_count=contradiction_count,
        above_level_count=above_level_count,
    )

    verdict = "critical" if signal_count >= 7 else None

    if suspicion < 15:
        level = "clean"
    elif suspicion < 35:
        level = "low_risk"
    elif suspicion < 60:
        level = "moderate_risk"
    else:
        level = "high_risk"

    if verdict == "critical":
        level = "high_risk"
        flags.append(f"CRITICAL: {signal_count} distinct suspicion signals detected")

    return {
        "suspicion_score": suspicion,
        "integrity_level": level,
        "signal_count": signal_count,
        "critical_verdict": verdict == "critical",
        "flags": flags,
    }
