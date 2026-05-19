"""
eval_validator.py — Rule-based evaluation validation.

Catches contradictions in LLM scoring before they affect strategy.
Pure logic, no I/O, no LLM calls. ~0.1ms per validation.

Rules ported from V1 evaluation_validator.py.
"""
from __future__ import annotations

QUALITY_SCORE_BOUNDS = {
    "strong":            (7, 10),
    "adequate":          (4, 8),
    "weak":              (0, 5),
    "honest_admission":  (0, 2),
    "poor_articulation": (2, 5),
}


def validate_and_fix(scores: dict, transcript: str) -> tuple[dict, list[str]]:
    """
    Validate eval scores and fix contradictions.
    Returns (fixed_scores, list_of_flags).
    Never fails — always returns usable scores.
    """
    flags: list[str] = []

    # Rule 1: All scores must be 0-10
    for key in ["accuracy", "depth", "completeness", "clarity", "maturity", "ownership", "correctness"]:
        if key in scores:
            scores[key] = max(0, min(10, int(scores.get(key, 5))))

    avg = sum(scores.get(k, 5) for k in ["accuracy", "depth", "completeness", "clarity", "maturity", "ownership", "correctness"]) / 7

    # Rule 2: Very short answers can't score high
    word_count = len(transcript.strip().split())
    if word_count < 5 and avg > 4:
        for key in scores:
            if isinstance(scores[key], (int, float)):
                scores[key] = min(scores[key], 3)
        flags.append(f"short_answer_capped: {word_count} words, avg capped to 3")

    # Rule 3: "I don't know" detection — cap at 2
    dont_know_phrases = [
        "i don't know", "i dont know", "no idea", "not sure",
        "i haven't studied", "i haven't learned", "i'm not aware",
        "i have no idea", "i don't remember",
    ]
    transcript_lower = transcript.lower().strip()
    if any(p in transcript_lower for p in dont_know_phrases) and word_count < 15:
        for key in scores:
            if isinstance(scores[key], (int, float)):
                scores[key] = min(scores[key], 2)
        flags.append("honest_admission_capped: score capped to 2")

    # Rule 4: Empty or noise transcript — score 0
    if word_count < 3:
        for key in scores:
            if isinstance(scores[key], (int, float)):
                scores[key] = 0
        flags.append("empty_answer: all scores set to 0")

    # Rule 5: High correctness but low accuracy is contradictory
    if scores.get("correctness", 5) >= 8 and scores.get("accuracy", 5) <= 3:
        scores["accuracy"] = scores["correctness"] - 1
        flags.append("accuracy_correctness_conflict: accuracy raised to match correctness")

    # Rule 6: Low correctness but high everything else
    if scores.get("correctness", 5) <= 2:
        for key in ["depth", "completeness", "maturity"]:
            if scores.get(key, 5) > 5:
                scores[key] = min(scores[key], 4)
        flags.append("wrong_answer_cascade: depth/completeness/maturity capped")

    return scores, flags
