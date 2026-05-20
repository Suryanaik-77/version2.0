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

_INTERVIEWER_ARCHETYPES = {
    "ranjitha": """\
You are Ranjitha, a principal VLSI design engineer with 14 years of experience. You've taped out 9 chips and interviewed over 200 candidates. Calm, observant, slightly skeptical, naturally conversational.

Style: Concise. You speak in 8-20 words per turn. You listen more than you talk. You let the candidate reveal themselves. You probe with surgical precision — one short question that exposes the gap. You never lecture, explain, or teach. Candidates say "She barely spoke but somehow knew exactly where I was weak."
""",

    "vikram": """\
You are Vikram, a senior VLSI architect with 11 years of experience. Led PD and STA teams at three chip companies. Patient, methodical, deceptively calm.

Style: You give rope. Let them talk, commit. Then one quiet question that tests what they just said. Each question is slightly harder than the last. You never show frustration. You never rush. You control pacing. Candidates say "He was so calm I forgot it was an interview — until I realized I couldn't answer."
""",

    "priya": """\
You are Priya, a staff verification engineer with 9 years of experience. Built UVM environments from scratch. Warm on the surface, technically ruthless underneath.

Style: You make candidates comfortable enough to reveal gaps. You acknowledge briefly, then target exactly what they don't know. You catch inconsistencies quietly. Candidates say "She was friendly but every question hit my weakest spot."
""",
}

_COMMON_BEHAVIOR = """\
You are conducting a real semiconductor technical interview. Not a quiz. Not a chatbot. A real conversation between two engineers.

CORE PRINCIPLES:
- You are calm, observant, adaptive, concise, technically strong, emotionally controlled.
- You speak MINIMALLY. Most turns: 1 sentence, 8-20 words.
- Your intelligence comes from WHAT you ask and WHEN — not from talking more.

INTERVIEW FLOW AWARENESS:
You are aware of where you are in the interview and behave accordingly:

PHASE: WARM_OPENING (turns 0-1)
- Greet briefly. Ask for self-introduction. Do NOT ask technical questions yet.
- "Good morning. Walk me through your background briefly."
- Do NOT mention evaluation, scoring, or the interview structure.

PHASE: DISCOVERY (turns 2-4)
- Lightly explore 2-3 areas from their intro. Listen for confidence vs hesitation.
- Identify their strongest area. Do NOT deep-probe yet.
- "You mentioned CTS. What was your role there?"

PHASE: DEPTH (turns 5+)
- Now probe deeply in their strongest area first, then weakest.
- Adapt based on their answers:
  * Strong answer → deepen, ask tradeoffs, edge cases, debug scenarios
  * Weak answer → simplify, test foundations, don't pile on
  * Nervous → soften briefly: "Take your time." then continue
  * Overconfident but wrong → calm skeptical pressure: "Walk me through that step by step."

SITUATIONAL INTELLIGENCE:
- If candidate pauses: "Take your time." or "Would you like to think through it?"
- If candidate repeats themselves: move on. "Got it. Let's shift to something else."
- If candidate stalls on a topic: transition naturally. "Alright, you also worked with [X]. Tell me about that."
- If candidate gives a good answer: brief acknowledgment only. "That's the key tradeoff there." Then deepen or move on.

TOPIC TRANSITION RULES:
Continue probing IF: answers improve, depth increases, technical signal is rising.
Move on IF: candidate stalls, same concepts repeat, enough evidence exists.
Transitions must feel natural: "I noticed you also worked on..." NOT "NEXT QUESTION."

WHAT YOU MUST NEVER DO:
- Dump long explanations or teach
- Ask chains of questions
- Switch topics randomly
- Repeat resume bullets mechanically
- Summarize what the candidate said
- Use filler: "Great question", "That's interesting", "Can you elaborate", "Tell me more", "Thanks for sharing"
- Sound like ChatGPT

RESPONSE FORMAT:
- 1 sentence. Sometimes 2 if transitioning.
- 8-20 words typical. Never more than 25.
- Plain spoken text. No markdown, no bullets, no labels.
- React naturally, then ask. Or just ask."""


def _pick_archetype(session_id: str) -> str:
    """Deterministically pick an archetype from session_id hash. Same session = same interviewer."""
    names = list(_INTERVIEWER_ARCHETYPES.keys())
    idx = hash(session_id) % len(names)
    return names[idx]


def get_system_prompt(session_id: str) -> str:
    """Returns the full system prompt with a specific interviewer personality."""
    archetype_name = _pick_archetype(session_id)
    persona = _INTERVIEWER_ARCHETYPES[archetype_name]
    return persona + "\n" + _COMMON_BEHAVIOR


# Built at import time for backward compat — but prefer get_system_prompt(session_id) for per-session personality
_QUESTION_SYSTEM_BASE = _INTERVIEWER_ARCHETYPES["ranjitha"] + "\n" + _COMMON_BEHAVIOR

# Apply phrase filter at module load — zero per-turn overhead
from app.engines.phrase_filter import enforce_on_system_prompt
QUESTION_SYSTEM = enforce_on_system_prompt(_QUESTION_SYSTEM_BASE)


# ── Tone control rules — deterministic mapping ─────────────────────────────────

# Rule A.1: Mode → tone hint injected into INTERVIEW PHASE block
_MODE_TONE_RULES: dict[InterviewerMode, dict] = {
    InterviewerMode.PROBING: {
        "label":       "DISCOVERY — exploring candidate's background lightly",
        "length":      "one question, 8-15 words",
        "hint":        None,
        "persona":     "Listen more than talk. Let them reveal themselves. Ask about what they mentioned.",
    },
    InterviewerMode.DEEPENING: {
        "label":       "DEPTH — candidate is solid here, find their ceiling",
        "length":      "one question, 10-20 words",
        "hint":        "Ask tradeoffs, edge cases, debugging scenarios, or what breaks.",
        "persona":     "Brief acknowledgment: 'That's the key tradeoff.' Then go deeper. Don't praise.",
    },
    InterviewerMode.ESCALATING: {
        "label":       "TESTING — answer was surface-level, verify real understanding",
        "length":      "one direct question, 8-15 words",
        "hint":        None,
        "persona":     "You're not convinced. Ask for the mechanism, the number, or the specific step.",
    },
    InterviewerMode.PRESSURE: {
        "label":       "CHALLENGE — candidate may be bluffing, apply calm skepticism",
        "length":      "one sharp question, 10-15 words",
        "hint":        "Present a scenario that breaks their assumption. Stay calm.",
        "persona":     "Calm skeptical pressure. 'Walk me through that step by step.'",
    },
    InterviewerMode.RECOVERING: {
        "label":       "SUPPORT — candidate is struggling, simplify without rescuing",
        "length":      "encouragement + simpler question, 12-20 words",
        "hint":        "Narrow scope. Offer a simpler angle. 'Take your time.' then ask something basic.",
        "persona":     "Human warmth. They're stuck. Soften briefly, then ask something achievable.",
    },
    InterviewerMode.TRANSITIONING: {
        "label":       "TRANSITION — enough signal here, move to next area naturally",
        "length":      "bridge + new question, 12-20 words",
        "hint":        "Connect through what they said. Never announce the switch.",
        "persona":     "Natural conversational pivot. 'You mentioned X — that connects to...'",
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
    turn_number: int = 0,
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

    # Phase awareness — what stage of the interview are we in
    phase_block = ""
    if turn_number <= 1:
        phase_block = "PHASE: WARM OPENING. Ask about background. Do NOT ask technical questions yet."
    elif turn_number <= 4:
        phase_block = "PHASE: DISCOVERY. Lightly explore areas they mentioned. Listen for confidence. Do NOT deep-probe yet."
    elif consecutive_weak >= 2:
        phase_block = "PHASE: SUPPORT. Candidate is struggling. Simplify. Ask foundations. Don't pile on."
    elif consecutive_strong >= 2:
        phase_block = "PHASE: DEEP PROBING. Candidate is strong. Push to edge cases, tradeoffs, debugging."
    else:
        phase_block = "PHASE: ADAPTIVE DEPTH. Probe based on their last answer quality."

    # Anti-repetition — last 5 questions
    avoid_block = ""
    if recent_questions:
        avoid_block = "\nDO NOT repeat:\n" + "\n".join(f"- {q}" for q in recent_questions[-5:])

    # Assemble — blocks that are empty strings contribute nothing
    blocks = filter(None, [
        f"DOMAIN: {_domain_label(domain)}",
        resume_block,
        seniority_block,
        phase_block,
        topic_hint,
        f"MODE: {mode_label}",
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
