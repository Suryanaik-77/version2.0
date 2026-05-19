"""
prompts.py — All LLM system prompts + rule-based assembly engine.

Intelligence Layer v1 adds:
  - Corpus-driven example injection (corpus.py)
  - Anti-AI phrase enforcement (phrase_filter.py)
  - Deterministic tone control rules
  - Memory injection rules with token budget
  - Pressure adaptation rules

Integration: build_question_prompt() is the single assembly point.
question_engine.py calls it unchanged — no pipeline modifications.
"""
from __future__ import annotations
from app.models.session import InterviewerMode, VLSIDomain, InlineSignals, SignalLevel, Correctness


# ── Base system prompt ────────────────────────────────────────────────────────

_QUESTION_SYSTEM_BASE = """\
You are a senior VLSI engineer conducting a technical interview. You are evaluating candidates for a real role.

Your only task: ask ONE precise follow-up question based on the candidate's last answer.

RULES (non-negotiable):
1. One question only. Never two.
2. Never summarize, repeat, or paraphrase the candidate's answer back to them.
3. Never explain concepts. Never teach. You are not a tutor.
4. Never ask a disconnected question. Every question must emerge directly from what they just said.
5. If they used a term without explaining it — name the term and ask what they mean by it.
6. If their answer lacked a mechanism — ask for the mechanism. ("How exactly does X prevent Y?")
7. If their answer was strong — push to a harder edge case. ("What breaks first if...?")
8. If they claimed to have done something — ask how they specifically did it.
9. If they contradicted something said earlier — surface it. ("Earlier you said X. Now you're saying Y.")
10. If they are confused — simplify the framing. One level only. Do not rescue them.

TONE: Concise. Technical. Direct. Skeptical. Like a senior engineer with limited patience.
LENGTH: Maximum two sentences. Usually one sentence is enough.
FORMAT: Plain question. No markdown, no labels, no explanations."""

# Apply phrase filter at module load — zero per-turn overhead
from app.engines.phrase_filter import enforce_on_system_prompt
QUESTION_SYSTEM = enforce_on_system_prompt(_QUESTION_SYSTEM_BASE)


# ── Tone control rules — deterministic mapping ─────────────────────────────────

# Rule A.1: Mode → tone hint injected into INTERVIEW PHASE block
_MODE_TONE_RULES: dict[InterviewerMode, dict] = {
    InterviewerMode.PROBING: {
        "label":       "PROBING — calibrating foundational understanding",
        "length":      "one sentence",
        "hint":        None,
        "persona":     "senior engineer taking first measure of the candidate",
    },
    InterviewerMode.DEEPENING: {
        "label":       "DEEPENING — candidate is solid, push to mechanisms and edge cases",
        "length":      "one sentence, may include a specific technical constraint",
        "hint":        "At the mechanism level — ",
        "persona":     "engineer who knows this candidate can go deeper",
    },
    InterviewerMode.ESCALATING: {
        "label":       "ESCALATING — answer was shallow, increase difficulty, no softening",
        "length":      "one sentence — shorter than DEEPENING",
        "hint":        None,  # abruptness is intentional
        "persona":     "engineer who heard a thin answer and isn't going to let it pass",
    },
    InterviewerMode.PRESSURE: {
        "label":       "PRESSURE — apply adversarial edge case, failure mode, or cross-domain consequence",
        "length":      "one sentence maximum — adversarial framing",
        "hint":        "Adversarial: what breaks, what fails, what's the consequence of X on Y",
        "persona":     "engineer actively stress-testing the candidate's ceiling",
    },
    InterviewerMode.RECOVERING: {
        "label":       "RECOVERING — candidate is confused, narrow scope to one concrete concept",
        "length":      "one sentence — narrower than the question that caused confusion",
        "hint":        "Simpler framing — isolate ONE concept only. DO NOT hint at the answer.",
        "persona":     "engineer who recognized confusion and is resetting — not rescuing",
    },
    InterviewerMode.TRANSITIONING: {
        "label":       "TRANSITIONING — this topic is exhausted, move to an adjacent concept",
        "length":      "one bridging clause (max 5 words) then a new question",
        "hint":        "Bridge then pivot — no ceremony",
        "persona":     "engineer shifting topics efficiently",
    },
}


# Rule A.2: Inline signal → prompt modifier
def _signal_modifier(signals: InlineSignals | None) -> str:
    """
    Deterministic modifier text injected into prompt based on inline signals.
    Priority: contradiction > wrong > vagueness > memorization.
    Returns empty string if no signals or no modifier needed.
    """
    if signals is None:
        return ""

    parts = []

    # Contradiction active — highest priority
    if signals.contradiction_with:
        parts.append(
            f"ACTIVE CONTRADICTION: candidate previously stated "
            f"\"{signals.contradiction_with[:80]}\" — surface this if the current answer conflicts with it."
        )

    # Wrong answer — correct + redirect
    if signals.correctness == Correctness.WRONG:
        parts.append(
            "SIGNAL: answer contained a technical error. "
            "State the error flatly. Redirect to the correct concept. Do NOT explain the answer."
        )

    # High vagueness — mechanism forcing
    if signals.vagueness == SignalLevel.HIGH:
        if signals.missing_mechanism:
            parts.append(
                f"SIGNAL: answer was vague. Missing mechanism: \"{signals.missing_mechanism}\". "
                f"Ask what that mechanism actually is."
            )
        else:
            parts.append(
                "SIGNAL: answer used technical terms without explaining mechanisms. "
                "Ask for the mechanism behind the most important term in their answer."
            )

    # Memorization suspected — force first-person
    if signals.memorization_suspected:
        parts.append(
            "SIGNAL: answer sounds memorized. "
            "Force first-person: ask what they specifically did on their actual project, not what 'is typically done'."
        )

    return "\n".join(parts)


# Rule A.3: Eval trend → tone persistence note
def _eval_trend_note(consecutive_weak: int = 0, consecutive_strong: int = 0) -> str:
    """
    Injects a brief trend note into the prompt when pattern is clear.
    Only fires when trend is 2+ turns — prevents over-reaction to single answers.
    """
    if consecutive_weak >= 2:
        return "TREND: multiple shallow answers. Stay on topic. Narrow — do not broaden."
    if consecutive_strong >= 2:
        return "TREND: consistently strong. Apply maximum pressure — adversarial edge case only."
    return ""


# ── Memory injection rules ─────────────────────────────────────────────────────

# Rule B.3: Token budget = 120 tokens max. Priority order below.
# See full rules in intelligence_layer_v1.md Part 2 Rule Set B.

def build_memory_block(memory_context: str) -> str:
    """
    Wraps memory context with enforcement rules.
    Returns empty string if context is empty — do not inject empty CONTEXT block.
    """
    if not memory_context.strip():
        return ""
    return f"\nCONTEXT (use this to anchor your question — do not repeat these facts back, probe them):\n{memory_context}"


# ── Corpus example injection ───────────────────────────────────────────────────

def _corpus_example_block(
    mode: InterviewerMode,
    signals: InlineSignals | None,
) -> str:
    """
    Injects 1 corpus example as a tone reference for the current mode/signal.
    Example shows the LLM what a realistic question looks like in this context.
    Token cost: ~30 tokens per example. Always exactly 1 example injected.
    """
    from app.engines.corpus import get_mode_examples, get_signal_examples

    # Signal-driven example takes priority over mode example
    signal_examples = []
    if signals:
        signal_examples = get_signal_examples(
            vagueness_high=(signals.vagueness == SignalLevel.HIGH),
            wrong_answer=(signals.correctness == Correctness.WRONG),
            memorization_suspected=signals.memorization_suspected,
            contradiction_active=bool(signals.contradiction_with),
            n=1,
        )

    if signal_examples:
        ex = signal_examples[0]
    else:
        mode_examples = get_mode_examples(mode, n=1)
        if not mode_examples:
            return ""
        ex = mode_examples[0]

    return f'\nEXAMPLE TONE (style reference only — do not copy the topic):\n"{ex.utterance}"'


# ── Main assembly function ────────────────────────────────────────────────────

def build_question_prompt(
    mode: InterviewerMode,
    domain: VLSIDomain,
    transcript: str,
    memory_context: str,
    recent_questions: list[str],
    signals: InlineSignals | None = None,
    consecutive_weak: int = 0,
    consecutive_strong: int = 0,
    resume: dict | None = None,
    topic_hint: str = "",
) -> str:
    """
    Deterministic rule-based prompt assembly.

    Token budget target: < 500 tokens total (system + user).
    Assembly order:
      1. Domain label
      2. Mode label + tone rules
      3. Signal modifier (if any)
      4. Eval trend note (if 2+ consecutive same result)
      5. Memory block (if any, with token budget enforcement)
      6. Corpus example (1 example, mode/signal-matched)
      7. Anti-repetition block
      8. Candidate answer
      9. Question prompt
    """
    tone = _MODE_TONE_RULES[mode]

    # Build mode label with optional hint
    mode_label = tone["label"]
    if tone["hint"]:
        mode_label += f"\nTone hint: {tone['hint']}"

    # Signal modifier — empty string if nothing to inject
    signal_block = _signal_modifier(signals)

    # Eval trend — empty string if no clear pattern
    trend_block = _eval_trend_note(consecutive_weak, consecutive_strong)

    # Memory block — empty string if no context
    memory_block = build_memory_block(memory_context)

    # Corpus example — always 1, ~30 tokens
    example_block = _corpus_example_block(mode, signals)

    # Resume block — candidate profile for personalized questions
    resume_block = ""
    if resume:
        name = resume.get("candidate_name", "")
        level = resume.get("level", "").replace("_", " ")
        years = resume.get("years_experience", "")
        tools = ", ".join(resume.get("tools", [])[:5]) or ""
        projects = ", ".join(resume.get("key_projects", [])[:3]) or ""
        skills = ", ".join(resume.get("skills", [])[:8]) or ""
        parts = [f"CANDIDATE: {name}" if name else ""]
        if level or years:
            parts.append(f"{level} | {years} years" if years else level)
        if tools:
            parts.append(f"Tools: {tools}")
        if projects:
            parts.append(f"Projects: {projects}")
        if skills:
            parts.append(f"Skills: {skills}")
        resume_block = " | ".join(p for p in parts if p)

    # Anti-repetition — last 2 questions only
    avoid_block = ""
    if recent_questions:
        avoid_block = "\nDO NOT ask a question similar to these recent ones:\n" + \
                      "\n".join(f"- {q}" for q in recent_questions[-2:])

    # Assemble — blocks that are empty strings contribute nothing
    blocks = filter(None, [
        f"DOMAIN: {_domain_label(domain)}",
        resume_block,
        topic_hint,
        f"INTERVIEW PHASE: {mode_label}",
        signal_block,
        trend_block,
        memory_block,
        example_block,
        avoid_block,
    ])

    header = "\n".join(blocks)

    return (
        f"{header}\n\n"
        f"CANDIDATE ANSWER:\n{transcript}\n\n"
        f"Your question:"
    )


# ── Domain and mode labels ────────────────────────────────────────────────────

def _domain_label(domain: VLSIDomain) -> str:
    return {
        VLSIDomain.ANALOG_LAYOUT:
            "Analog Layout (matching, parasitics, LVS, ESD, guard rings, floorplan)",
        VLSIDomain.PHYSICAL_DESIGN:
            "Physical Design (CTS, timing closure, IR drop, congestion, ECO, PnR)",
        VLSIDomain.DESIGN_VERIFICATION:
            "Design Verification (UVM, assertions, coverage, scoreboards, debugging)",
    }[domain]


def _mode_label(mode: InterviewerMode) -> str:
    """Legacy compatibility — returns simple mode label string."""
    return _MODE_TONE_RULES[mode]["label"]


# ── Eval and contradiction prompts (unchanged) ────────────────────────────────

EVAL_SYSTEM = """\
You are scoring a VLSI engineer's interview answer. Return ONLY a JSON object with no explanation.

Score each dimension 0–10. Be strict. Most candidates score 4–7, not 8–10.

DIMENSIONS:
accuracy     — Is the technical content correct? Wrong facts → penalize hard.
depth        — Did they explain mechanisms, not just name concepts?
completeness — Did they address the full question or only part of it?
clarity      — Was the explanation organized and easy to follow?
maturity     — Does this reflect real hands-on experience, not textbook knowledge?
ownership    — Did they speak with first-person experience ("I designed X") or hide behind "typically..."?
correctness  — No wrong concepts stated as facts? (separate from accuracy — penalizes confident wrongness)

MANDATORY PENALTIES (apply even if answer sounds good):
- No mechanism explanation given → accuracy ≤ 5
- Wrong concept stated as fact → correctness ≤ 3
- Pure buzzword answer with no substance → depth ≤ 3
- Memorized-sounding textbook definition → depth ≤ 4, maturity ≤ 4
- Vague "it depends" without explaining what it depends on → completeness ≤ 5

RETURN FORMAT (no other text, no markdown):
{"accuracy":N,"depth":N,"completeness":N,"clarity":N,"maturity":N,"ownership":N,"correctness":N,"flags":[]}\
"""


def build_eval_prompt(domain: VLSIDomain, question: str, transcript: str) -> str:
    return (
        f"DOMAIN: {_domain_label(domain)}\n\n"
        f"QUESTION ASKED:\n{question}\n\n"
        f"CANDIDATE ANSWER:\n{transcript}"
    )


CONTRADICTION_SYSTEM = """\
You identify contradictions between statements. Return ONLY JSON.
If contradiction found: {"found": true, "claim_a": "...", "claim_b": "..."}
If no contradiction: {"found": false}
No other output.\
"""


def build_contradiction_prompt(new_statement: str, prior_claims: list[str]) -> str:
    claims_text = "\n".join(f"- {c}" for c in prior_claims[-8:])
    return f"NEW STATEMENT:\n{new_statement}\n\nPRIOR CLAIMS:\n{claims_text}"
