"""
phrase_filter.py — Anti-AI phrase filter.

Runs ONLY at prompt construction time inside build_question_prompt().
Zero runtime overhead — this is static text injection, not post-processing.

Three parts:
  1. FORBIDDEN_BLOCK   — static text appended to QUESTION_SYSTEM at construction
  2. REPLACEMENT_MAP   — lookup for any post-generation validation (evaluator use)
  3. enforce()         — appends FORBIDDEN_BLOCK to any system prompt string

Integration:
    from app.engines.phrase_filter import enforce_on_system_prompt
    QUESTION_SYSTEM_V2 = enforce_on_system_prompt(QUESTION_SYSTEM)

The model sees the forbidden list before generating any token.
No post-generation filtering. No latency.
"""
from __future__ import annotations


# ── Forbidden phrase block ─────────────────────────────────────────────────────
# Injected into QUESTION_SYSTEM. Deterministic. Static text.
# Placed AFTER behavioral rules, BEFORE FORMAT line.

FORBIDDEN_BLOCK = """

BANNED — never use:
Affirmations: Great / Good / Excellent / Perfect / Wonderful / Nice / Impressive / Brilliant
Fillers: Interesting / I see / Understood / Absolutely / Certainly / Of course / Sure / Right so
Chatbot: "Can you elaborate" / "Tell me more" / "Feel free to" / "Don't hesitate"
Validators: "Good point" / "That makes sense" / "That's correct" / "You're on the right track"
Softeners: "It sounds like" / "What I'm hearing is" / "To summarize what you said"
Transitions: "Let's move on" / "Let's switch to" / "Moving on"
Service: "Thanks for sharing" / "Thank you for that" / "Does that make sense"

Rule: Delete any opener from the list above. Start with a technical word from their answer."""


# ── Replacement map ────────────────────────────────────────────────────────────
# Used by evaluator.py for replacement suggestions in reports.
# Not used in hot path.

REPLACEMENT_MAP: dict[str, str] = {
    # Vague elaboration requests → specific dimension probes
    "Can you elaborate on {X}?":
        "{X} — what specifically? (name the dimension: mechanism, tradeoff, number, or failure mode)",
    "Can you tell me more about {X}?":
        "{X} — at the device level / at the circuit level / in your specific project?",
    "Tell me more.":
        "[name the specific gap from the answer and ask for it directly]",

    # Praise + question → question only
    "Great answer. {question}":
        "{question}  (drop the praise entirely)",
    "Good point. {question}":
        "{question}  (start with the content, not a reaction)",
    "Interesting. {question}":
        "{question}  (the reaction word adds nothing)",
    "Exactly. {question}":
        "{question}  (confirming correctness softens what should be continued pressure)",

    # Validation → challenge
    "That's correct. {question}":
        "{question}  (if correct, the next question should probe deeper — not confirm)",
    "That makes sense. {question}":
        "{question}  (skip the validation; challenge or deepen directly)",
    "You're on the right track. {question}":
        "[do not coach candidate toward the answer] {question}",

    # Transitions → abrupt pivot
    "Let's move on to {topic}.":
        "On {topic} — {question}  (topic change is implicit in the new question)",
    "Let's switch topics and talk about {topic}.":
        "{question about new topic}  (no announcement needed)",

    # Service phrases → silence
    "Thanks for sharing that.":
        "[delete entirely — ask the next question]",
    "Thank you for that explanation.":
        "[delete entirely]",
    "Does that make sense?":
        "[interviewer never asks if they were clear — delete]",

    # Softeners → direct framing
    "It sounds like you're saying {X}.":
        "You said {X} — [challenge or verify directly]",
    "What I'm hearing is {X}.":
        "[don't reflect back — challenge or probe directly]",
    "To summarize, you mentioned {X}.":
        "[don't summarize — ask the next question about X]",
}


# ── Enforcement function ───────────────────────────────────────────────────────

def enforce_on_system_prompt(system_prompt: str) -> str:
    """
    Appends FORBIDDEN_BLOCK to a system prompt string.
    Call once at module load to produce the hardened system prompt.

    Zero runtime overhead — this is called at construction, not per-turn.

    Usage:
        from app.engines.phrase_filter import enforce_on_system_prompt
        HARDENED_QUESTION_SYSTEM = enforce_on_system_prompt(QUESTION_SYSTEM)
    """
    return system_prompt + FORBIDDEN_BLOCK


def get_replacement(phrase: str) -> str | None:
    """
    Returns a replacement suggestion for a detected forbidden phrase.
    Used in evaluator reports — not in hot path.
    """
    # Exact match first
    if phrase in REPLACEMENT_MAP:
        return REPLACEMENT_MAP[phrase]
    # Partial match (phrase is a substring of a key)
    phrase_lower = phrase.lower()
    for key, replacement in REPLACEMENT_MAP.items():
        if phrase_lower in key.lower():
            return replacement
    return None
