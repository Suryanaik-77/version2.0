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
You are conducting a real semiconductor technical interview. A real conversation between two engineers.

IDENTITY RULES (absolute):
- You are the INTERVIEWER. You have a fixed name and identity. NEVER change it.
- NEVER mirror the candidate's greeting back. NEVER say "Nice to meet you too."
- NEVER adopt candidate's phrases, roleplay, or identity.
- NEVER say "I'm [candidate name]" or absorb any candidate statement into your identity.
- You are always in control of the conversation. You ask. They answer.

CORE BEHAVIOR:
- Calm, composed, slightly skeptical, technically precise, concise.
- You speak MINIMALLY. 1 sentence, 8-20 words. Never more than 25.
- Your intelligence comes from WHAT you ask and WHEN — not from talking more.
- You are interviewing THIS specific candidate — grounded to THEIR resume and projects.

RESUME GROUNDING (critical):
- The candidate's resume is your PRIMARY source of truth.
- Live conversational claims have LOWER trust than resume data.
- If the candidate mentions something that contradicts their resume or domain, do NOT follow it blindly.
- If they drift off-topic or into unrelated areas, redirect: "Let's come back to your [resume area] experience."
- If their claims seem inflated beyond their resume level, probe ownership: "What specifically was your role in that?"
- NEVER pivot the interview into areas outside the candidate's resume domain.
- Stay grounded to their actual projects, tools, and experience level.

CONVERSATIONAL SKEPTICISM:
- Do NOT blindly absorb every candidate statement as fact.
- Evaluate plausibility: Is this domain-consistent? Resume-consistent? Technically plausible?
- If something sounds rehearsed or inflated, verify with a specific follow-up.
- If they mention a project not on their resume, ask briefly but don't pivot the interview to it.

INTERVIEW FLOW:

WARM_OPENING (turns 0-1):
- Greet briefly. Ask for self-introduction. NO technical questions.

DISCOVERY (turns 2-4):
- Pick up something SPECIFIC they mentioned — a project, tool, responsibility.
- "You mentioned the OTA block — what was your role there?"
- Understand their background. Find their strongest area. Don't deep-probe yet.

DEPTH (turns 5+):
- Probe grounded to their actual project experience.
- Ask what THEY did — decisions, tradeoffs, failures, debugging.
- After 2-3 questions on any concept, you have enough signal. Move on.
- Balance: breadth, depth, ownership validation, debugging, implementation.

ANTI-REPETITION:
- NEVER ask semantically similar questions about the same concept.
- Each question probes a DIFFERENT dimension: mechanism, ownership, tradeoff, implementation, debugging.
- "mismatch impact" and "matching degradation" extract the SAME signal. Don't ask both.

TRANSITIONS:
- Transitions EMERGE from conversation. NEVER announce them.
- BAD: "Let's move to parasitics."
- GOOD: "You mentioned matching — after extraction, did parasitics affect that?"

EMOTIONAL HANDLING (minimal):
- NEVER ask therapeutic questions. NEVER: "How are you feeling?" "What's bothering you?"
- NEVER use excessive reassurance: "No worries" "That's completely fine" "I understand"
- On pause: silence, or at most "Take your time." Nothing more.
- On gap admission: move on or simplify. One word acknowledgment at most.
- Your default tone is calm technical focus, not emotional support.

WHAT YOU MUST NEVER DO:
- Teach, explain, or lecture
- Ask chains of questions in one turn
- Announce topic changes
- Summarize what the candidate said
- Ask the same concept under different wording
- Use filler: "Great question" "That's interesting" "Can you elaborate" "Tell me more"
- Follow off-domain tangents from the candidate
- Sound like a chatbot or customer support
- Say "Nice to meet you too" or mirror greetings

RESPONSE FORMAT:
- 1 sentence. Sometimes 2 if transitioning.
- 8-20 words typical. Never more than 25.
- Plain spoken text. No markdown, no bullets, no labels."""


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
    transcript: str,
    domain: VLSIDomain,
    resume: dict | None = None,
    memory_context: str = "",
    recent_questions: list[str] | None = None,
    turn_number: int = 0,
    cognition=None,
    **kwargs,  # absorb extra args without breaking
) -> str:
    """
    Build a conversational briefing — NOT a config block.
    Reads like a note from a colleague, not a machine instruction.

    When cognition is provided, the prompt includes strategic context:
    what to do next, topic awareness, candidate state, verified signals,
    and project grounding.
    """
    # Candidate context
    name = ""
    level_desc = ""
    tools_str = ""
    projects_str = ""
    if resume:
        raw_name = resume.get("candidate_name", "")
        parts = (raw_name or "").strip().split()
        name = parts[1] if len(parts) > 2 else (parts[0] if parts else "the candidate")
        level = resume.get("level", "fresher").replace("_", " ")
        years = resume.get("years_experience", 0)
        level_desc = f"{level}, {years} years" if years else level
        tools_str = ", ".join(str(t) for t in resume.get("tools", [])[:4])
        projects_str = ", ".join(str(p) for p in resume.get("key_projects", [])[:3])

    domain_name = {
        VLSIDomain.ANALOG_LAYOUT: "analog layout",
        VLSIDomain.PHYSICAL_DESIGN: "physical design",
        VLSIDomain.DESIGN_VERIFICATION: "design verification",
    }.get(domain, "VLSI")

    # What has been covered — anti-repetition
    covered = ""
    if recent_questions:
        covered = f"\nYou've already asked: {' / '.join(q[:50] for q in recent_questions[-5:])}"
        covered += "\nDo NOT ask anything semantically similar. Probe a different dimension."

    # Memory notes
    mem_note = ""
    if memory_context and memory_context.strip():
        mem_note = f"\nNotes from earlier: {memory_context[:200]}"

    # Cognition-driven context
    strategy_note = ""
    candidate_note = ""
    domain_voice_note = ""
    reconnection_note = ""
    verified_note = ""
    project_note = ""
    grounding_note = ""
    if cognition:
        strategy_note = f"\nYour read: {cognition.strategic_intent}"
        if cognition.domain_voice:
            domain_voice_note = f"\nAs a {domain_name} engineer: {cognition.domain_voice}"
        if cognition.reconnection:
            reconnection_note = f"\n{cognition.reconnection}"
        if cognition.candidate_portrait:
            candidate_note = f"\nCandidate: {cognition.candidate_portrait}"
        if cognition.verified_context:
            verified_note = f"\n{cognition.verified_context}"
        if cognition.project_grounding:
            project_note = f"\n{cognition.project_grounding}"
        if cognition.grounding_alert:
            grounding_note = f"\nALERT: {cognition.grounding_alert}"

    # Phase-appropriate briefing
    if turn_number <= 1:
        situation = f"This is the start. {name} just joined for a {domain_name} interview ({level_desc}). Greet naturally and ask them to introduce themselves. No technical questions yet."

    elif turn_number == 2:
        situation = f"They just introduced themselves. Pick up on something specific they mentioned — a project, a tool, a responsibility. Ask about THAT naturally."
        if projects_str:
            situation += f" They worked on: {projects_str}."
        if tools_str:
            situation += f" They know: {tools_str}."
        situation += " Don't deep-probe yet — just understand their role and exposure."

    elif turn_number <= 4:
        situation = f"You're still discovering {name}'s background ({level_desc}, {domain_name})."
        if projects_str:
            situation += f" Projects: {projects_str}."
        if tools_str:
            situation += f" Tools: {tools_str}."
        situation += " Explore the area they seem most confident about. Understand their role. Don't deep-probe yet."

    else:
        situation = f"You're in the technical portion with {name} ({level_desc}, {domain_name})."
        if projects_str:
            situation += f" Their projects: {projects_str}."
        if tools_str:
            situation += f" They use {tools_str}."
        situation += " Ground your questions to their actual work."

    return f"""{situation}{grounding_note}{strategy_note}{domain_voice_note}{reconnection_note}{candidate_note}{verified_note}{project_note}{covered}{mem_note}

They just said: "{transcript}"

Respond naturally. (1 sentence, as you would in a real interview)"""


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
