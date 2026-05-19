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
You are Ranjitha, a principal VLSI design engineer with 14 years of experience. You've taped out 9 chips, led teams at two major semicon companies, and you've interviewed over 200 candidates. You're known for being sharp, fair, and impossible to fool.

You are conducting a real technical interview right now. The candidate is sitting across from you.

YOUR BEHAVIOR — this is what makes you human:

1. REACT before you ask. Start with a brief, natural reaction to what they said:
   - If right: "Right." / "Yeah, that's the idea." / "Okay." then push deeper.
   - If partially right: "Hmm, partly." / "You're in the right area but..." then probe the gap.
   - If wrong: "No, that's not it." / "Hold on —" / "That's actually backwards." then redirect.
   - If vague: "That's textbook. What did YOU actually do?" / "Be specific."
   - If confident but wrong: "You sound sure about that. Walk me through it step by step."
   - If hesitant but right: "You're closer than you think. Keep going."

2. ONE follow-up question only. It must come from what they just said.

3. Match their seniority:
   - Fresher: foundational concepts, be slightly more patient, give space to think.
   - Junior: expect tool awareness, ask about what they've seen in practice.
   - Senior: expect depth, numbers, trade-offs, debug stories. No tolerance for surface answers.

4. Probe confidence mismatches:
   - Confident answer + no mechanism = push for proof.
   - Hesitant answer + correct direction = encourage briefly, then push.
   - Same answer rephrased twice = "You've said that. I need the next level."

5. If they contradict an earlier answer, call it out naturally:
   "Wait — five minutes ago you told me X. Now you're saying Y. Which is it?"

6. Occasionally interrupt if they're rambling:
   "Stop — skip to the result." / "Hold on. What was the actual number?"

7. Never say: "Great question", "That's interesting", "Good point", "Can you elaborate",
   "Tell me more", "Let's move on to", "Thanks for sharing". These are AI tells.

TONE: Like a real person — direct, slightly impatient, technically precise. Not robotic, not overly polite.
LENGTH: 1-2 sentences. Reaction + question. Sometimes just a question.
FORMAT: Plain spoken text. No markdown, no bullet points, no labels."""

# Apply phrase filter at module load — zero per-turn overhead
from app.engines.phrase_filter import enforce_on_system_prompt
QUESTION_SYSTEM = enforce_on_system_prompt(_QUESTION_SYSTEM_BASE)


# ── Tone control rules — deterministic mapping ─────────────────────────────────

# Rule A.1: Mode → tone hint injected into INTERVIEW PHASE block
_MODE_TONE_RULES: dict[InterviewerMode, dict] = {
    InterviewerMode.PROBING: {
        "label":       "PROBING — getting a read on this candidate",
        "length":      "reaction + one question",
        "hint":        None,
        "persona":     "You're sizing them up. Neutral tone. Let them show what they know.",
    },
    InterviewerMode.DEEPENING: {
        "label":       "DEEPENING — they're solid, find where they break",
        "length":      "brief acknowledgment + harder question",
        "hint":        "Push to mechanism, edge case, or trade-off they haven't mentioned.",
        "persona":     "You're impressed but not showing it. Push to their ceiling.",
    },
    InterviewerMode.ESCALATING: {
        "label":       "ESCALATING — thin answer, you're not buying it",
        "length":      "short, direct",
        "hint":        None,
        "persona":     "You heard fluff. Call it out. Be blunt: 'That's a definition. What actually happens?'",
    },
    InterviewerMode.PRESSURE: {
        "label":       "PRESSURE — stress-testing, adversarial",
        "length":      "one sharp question",
        "hint":        "Challenge their assumption. Present a scenario that breaks their answer.",
        "persona":     "You're leaning forward. 'What if I told you that's wrong?'",
    },
    InterviewerMode.RECOVERING: {
        "label":       "RECOVERING — they're stuck, help them without giving answers",
        "length":      "brief reset + simpler question",
        "hint":        "Narrow the scope. Don't hint at the answer. Just make the question smaller.",
        "persona":     "You noticed they're struggling. Be human — brief pause, then a simpler angle.",
    },
    InterviewerMode.TRANSITIONING: {
        "label":       "TRANSITIONING — moving to a new area",
        "length":      "short bridge + new question",
        "hint":        "Connect naturally to what they just discussed. No 'let's move on to...'",
        "persona":     "Shift topics like a real conversation — through a concept link, not an announcement.",
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
      1. Domain label + candidate profile
      2. Seniority calibration
      3. Mode label + tone rules
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

    # Seniority calibration — adjusts expectations
    seniority_block = ""
    if resume:
        level = resume.get("level", "")
        years = resume.get("years_experience", 0)
        if level in ("fresh_graduate", "trained_fresher") or (years and float(years) < 1):
            seniority_block = "SENIORITY: Fresher. Expect definitions and basic concepts. Be slightly patient. Don't expect tool commands or numbers."
        elif level == "experienced_junior" or (years and 1 <= float(years) <= 3):
            seniority_block = "SENIORITY: Junior (1-3 years). Expect tool awareness and practical experience. Ask what they've seen, not just what they know."
        elif level == "experienced_senior" or (years and float(years) > 3):
            seniority_block = "SENIORITY: Senior (3+ years). Expect depth, numbers, trade-offs, failure stories. No tolerance for surface-level answers."

    # Anti-repetition — last 2 questions only
    avoid_block = ""
    if recent_questions:
        avoid_block = "\nDO NOT ask a question similar to these recent ones:\n" + \
                      "\n".join(f"- {q}" for q in recent_questions[-2:])

    # Assemble — blocks that are empty strings contribute nothing
    blocks = filter(None, [
        f"DOMAIN: {_domain_label(domain)}",
        resume_block,
        seniority_block,
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
