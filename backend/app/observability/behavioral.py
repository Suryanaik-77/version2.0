"""
behavioral.py — Behavioral anomaly detection engine.

Ported from monolith. Detects behavioral deviations that may indicate:
  - AI-generated answers (clean speech, no fillers, no self-corrections)
  - Proxy candidate (low pause variance, instant answers on hard questions)
  - Answer sophistication above resume level (expert terms from a fresher)
  - Smooth talker patterns (confident + shallow knowledge)

All functions are pure Python — no LLM calls, no I/O.
Called from eval_engine after each turn's evaluation completes.

Behavioral baseline is established during warmup (first 3-4 turns).
Deviations from baseline are tracked per turn and aggregated.
"""
from __future__ import annotations

import re
import statistics
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Text analysis helpers ────────────────────────────────────────────────────

_FILLER_WORDS = frozenset([
    "um", "uh", "like", "you know", "basically", "actually", "right",
    "so", "well", "i mean", "kind of", "sort of", "let me think",
])

_SELF_CORRECTION_PATTERNS = [
    r"\bi mean\b",
    r"\bwait\b",
    r"\bsorry\b",
    r"\blet me rephrase\b",
    r"\bactually no\b",
    r"\bwhat i meant was\b",
    r"\bno wait\b",
]

_PERSONAL_PRONOUNS = frozenset(["i", "my", "me", "we", "our", "mine"])


def count_fillers(text: str) -> float:
    """Filler word rate (fillers / total words)."""
    words = text.lower().split()
    if not words:
        return 0.0
    count = sum(1 for w in words if w in _FILLER_WORDS)
    # Also check multi-word fillers
    text_lower = text.lower()
    for filler in ("you know", "i mean", "kind of", "sort of", "let me think"):
        count += text_lower.count(filler)
    return count / len(words)


def count_personal_pronouns(text: str) -> float:
    """Personal pronoun rate (pronouns / total words)."""
    words = text.lower().split()
    if not words:
        return 0.0
    count = sum(1 for w in words if w in _PERSONAL_PRONOUNS)
    return count / len(words)


def count_self_corrections(text: str) -> float:
    """Self-correction rate (corrections / total words)."""
    words = text.split()
    if not words:
        return 0.0
    text_lower = text.lower()
    count = sum(1 for p in _SELF_CORRECTION_PATTERNS if re.search(p, text_lower))
    return count / len(words)


# ── Behavioral baseline ─────────────────────────────────────────────────────

def build_baseline(warmup_answers: list[dict]) -> dict:
    """
    Build behavioral baseline from warmup answers (first 3-4 turns).

    Each entry in warmup_answers should have:
      - text: str (the answer text)
      - duration_sec: float (how long the candidate took)
      - word_count: int
      - thinking_pause_sec: float

    Returns a baseline dict for deviation comparison.
    """
    if len(warmup_answers) < 2:
        return {"sample_size": 0}

    durations = [a.get("duration_sec", 0) for a in warmup_answers if a.get("duration_sec", 0) > 0]
    word_counts = [a.get("word_count", 0) for a in warmup_answers if a.get("word_count", 0) > 0]
    fillers = [count_fillers(a.get("text", "")) for a in warmup_answers if a.get("text")]
    pronouns = [count_personal_pronouns(a.get("text", "")) for a in warmup_answers if a.get("text")]
    corrections = [count_self_corrections(a.get("text", "")) for a in warmup_answers if a.get("text")]

    return {
        "sample_size": len(warmup_answers),
        "avg_duration_sec": sum(durations) / len(durations) if durations else 0,
        "avg_word_count": sum(word_counts) / len(word_counts) if word_counts else 0,
        "avg_filler_rate": sum(fillers) / len(fillers) if fillers else 0,
        "avg_pronoun_rate": sum(pronouns) / len(pronouns) if pronouns else 0,
        "avg_correction_rate": sum(corrections) / len(corrections) if corrections else 0,
    }


# ── Per-turn deviation analysis ──────────────────────────────────────────────

# Expected thinking pause by question difficulty (seconds)
_EXPECTED_PAUSE = {
    "basic": 2.0,
    "intermediate": 3.0,
    "advanced": 4.0,
    "expert": 5.0,
}


def analyze_deviation(
    baseline: dict,
    answer_text: str,
    duration_sec: float = 0,
    word_count: int = 0,
    thinking_pause_sec: float = 0,
    difficulty: str = "intermediate",
    pause_history: list[float] | None = None,
) -> dict:
    """
    Compare a single answer against the behavioral baseline.
    Returns deviation score and list of behavioral flags.

    Flags are signals, not convictions. Multiple flags across
    multiple turns build the suspicion case.
    """
    if baseline.get("sample_size", 0) == 0:
        filler_rate = count_fillers(answer_text)
        pronoun_rate = count_personal_pronouns(answer_text)
        correction_rate = count_self_corrections(answer_text)
        return {
            "deviation_score": 0.0,
            "flags": [],
            "filler_rate": filler_rate,
            "pronoun_rate": pronoun_rate,
            "correction_rate": correction_rate,
        }

    flags = []
    score = 0.0

    filler_rate = count_fillers(answer_text)
    pronoun_rate = count_personal_pronouns(answer_text)
    correction_rate = count_self_corrections(answer_text)

    # Duration deviation
    avg_dur = baseline.get("avg_duration_sec", 0)
    if avg_dur > 0 and duration_sec > 0:
        ratio = duration_sec / avg_dur
        if ratio < 0.25:
            flags.append("unusually_short_answer")
            score += 1.5
        elif ratio > 5.0:
            flags.append("unusually_long_answer")
            score += 0.5

    # Word count deviation
    avg_wc = baseline.get("avg_word_count", 0)
    if avg_wc > 5 and word_count > 0 and word_count / avg_wc < 0.2:
        flags.append("very_few_words")
        score += 1.0

    # Filler words vanished (signal: AI-generated answer)
    avg_fr = baseline.get("avg_filler_rate", 0)
    if avg_fr > 0.008 and filler_rate < avg_fr * 0.1 and word_count > 25:
        flags.append("suspiciously_clean_speech")
        score += 2.0

    # Personal pronouns vanished (signal: proxy or AI)
    avg_pr = baseline.get("avg_pronoun_rate", 0)
    if avg_pr > 0.02 and pronoun_rate < avg_pr * 0.15 and word_count > 25:
        flags.append("personal_pronouns_vanished")
        score += 1.5

    # Self-corrections vanished (signal: pre-prepared answer)
    avg_cr = baseline.get("avg_correction_rate", 0)
    if avg_cr > 0.002 and correction_rate < avg_cr * 0.1 and word_count > 30:
        flags.append("self_corrections_vanished")
        score += 1.0

    # Pause variance too low (signal: earpiece/proxy)
    if pause_history and len(pause_history) >= 4:
        try:
            stddev = statistics.stdev(pause_history)
            if stddev < 0.5:
                flags.append("low_pause_variance")
                score += 2.0
        except statistics.StatisticsError:
            pass

    # Instant answer on hard question
    expected_pause = _EXPECTED_PAUSE.get(difficulty, 3.0)
    if difficulty in ("advanced", "expert") and thinking_pause_sec > 0:
        if thinking_pause_sec < expected_pause * 0.3:
            flags.append(f"instant_answer_on_{difficulty}_question")
            score += 1.5

    # Answer length spike on hard question
    if difficulty in ("advanced", "expert") and avg_wc > 0:
        if word_count / max(avg_wc, 1) > 4.0:
            flags.append("answer_length_spike_on_hard_question")
            score += 1.5

    return {
        "deviation_score": score,
        "flags": flags,
        "filler_rate": filler_rate,
        "pronoun_rate": pronoun_rate,
        "correction_rate": correction_rate,
    }


# ── Pause consistency analysis ───────────────────────────────────────────────

def analyze_pause_consistency(pause_history: list[float]) -> dict:
    """
    If pause variance is too low across all questions,
    candidate is likely receiving answers externally (earpiece / proxy).

    Natural human thinking pauses vary significantly with difficulty.
    Stddev below 0.8 seconds across 5+ questions is suspicious.
    """
    # Filter out very short pauses (likely data artifacts)
    pauses = [p for p in pause_history if p > 0.5]

    if len(pauses) < 5:
        return {"suspicious": False, "reason": "insufficient_data"}

    try:
        stddev = statistics.stdev(pauses)
        avg = statistics.mean(pauses)

        if stddev < 0.8:
            return {
                "suspicious": True,
                "reason": "low_pause_variance",
                "stddev": round(stddev, 3),
                "avg_pause": round(avg, 3),
                "sample_size": len(pauses),
            }
        return {"suspicious": False, "stddev": round(stddev, 3)}
    except statistics.StatisticsError:
        return {"suspicious": False, "reason": "statistics_error"}


# ── Answer sophistication detection ──────────────────────────────────────────

# Expert terms that shouldn't appear from candidates at given level
_LEVEL_UNEXPECTED_VOCAB: dict[str, list[str]] = {
    "fresh_graduate": [
        "derating", "ocv", "mmmc", "ir drop budget", "pelgrom",
        "electromigration limit", "multi-voltage", "cpf", "upf",
        "parasitic extraction corners", "mcmm signoff", "dtco",
        "3d-ic integration", "custom node pdk", "advanced node finfet",
        "process corner", "monte carlo", "sigma variation",
        "voltage domain crossing", "power intent",
        "adaptive voltage scaling", "near-threshold",
    ],
    "trained_fresher": [
        "mcmm signoff", "multi-voltage", "cpf", "upf",
        "parasitic extraction corners", "custom node pdk",
        "advanced node finfet", "dtco", "3d-ic integration",
        "voltage domain crossing", "power intent", "ir drop budget",
        "electromigration limit", "adaptive voltage scaling",
        "near-threshold", "stochastic timing", "on-chip variation",
    ],
    "experienced_junior": [
        "custom node pdk", "advanced node finfet", "dtco",
        "3d-ic integration", "voltage domain crossing",
        "adaptive voltage scaling", "near-threshold computing",
        "stochastic timing", "sub-threshold leakage model",
        "gidl", "nbti aging", "self-heating effect",
    ],
}


def score_answer_sophistication(answer: str, resume_level: str) -> dict:
    """
    Detect expert terminology above resume level.
    A fresh graduate using terms like 'MMMC signoff' or 'Pelgrom' signals
    proxy candidate or AI-generated answers.
    """
    unexpected = _LEVEL_UNEXPECTED_VOCAB.get(resume_level, [])
    answer_lower = answer.lower()
    expert_terms_found = [t for t in unexpected if t in answer_lower]
    above_level = len(expert_terms_found) >= 2

    return {
        "above_level": above_level,
        "expert_terms_found": expert_terms_found,
        "sophistication_gap": len(expert_terms_found),
    }


# ── Smooth talker detection ──────────────────────────────────────────────────

def detect_smooth_talker_signal(
    eval_quality: str,
    eval_confidence: str,
    eval_accuracy: str,
    eval_quadrant: str,
    question_type: str,
) -> str | None:
    """
    Detect smooth talker pattern: confident surface-level answers
    that collapse under probing.

    Returns a signal description or None.
    """
    if question_type == "scenario" and eval_quadrant == "dangerous_fake":
        return "Collapsed on scenario after confident definition"
    if question_type == "why_probe" and eval_accuracy in ("wrong", "partial"):
        return "Could not explain WHY — surface-level knowledge only"
    if question_type == "numerical" and eval_quality in ("weak", "adequate") and eval_confidence == "high":
        return "Evaded numerical probe with no real numbers"
    if question_type == "personal_anchor" and eval_quality == "weak":
        return "Generic answer to personal experience question"
    if question_type == "contradiction" and eval_accuracy == "wrong":
        return "Contradicted earlier answer — memorized not understood"
    return None
