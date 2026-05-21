"""
cognition.py — Interview cognition layer.

The brain between "candidate answered" and "generate next question."

This module reads accumulated interview state (eval history, memory,
topic coverage, streaks) and produces a strategic briefing that tells
the LLM *what kind of interviewer action* to take next.

No LLM calls. Pure Python. <5ms per turn.

Design principles:
  - Heuristics GUIDE, never rigidly control. The LLM decides the question.
  - Domain-native behavior: PD/DV/AL interviews have distinct conversational DNA.
  - Semantic reconnection: link earlier candidate statements to current probing.
  - Emotional pacing: match interviewer intensity to candidate state.
  - Transition signals are soft ("consider moving on") not hard ("TRANSITION NOW").
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from app.core import redis as r
from app.config import get_settings
from app.models.session import (
    CandidateMemory,
    InterviewerMode,
    ResumeData,
    VLSIDomain,
)

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class CognitionResult:
    """Output of the cognition layer — feeds into prompt building."""
    # Strategic intent in natural language — the core briefing
    strategic_intent: str
    # Domain-native interviewer voice
    domain_voice: str           # how a real engineer from this domain would behave right now
    # Semantic reconnection — linking earlier statements
    reconnection: str           # e.g. "They mentioned clock mesh power earlier — connect to IR"
    # Topic awareness
    current_topic: str
    turns_on_topic: int
    topic_depth: str            # "surface", "mechanism", "tradeoff", "edge-case"
    # Soft transition signal
    transition_pressure: str    # "none", "consider", "recommended", "overdue"
    transition_target: str | None
    # Candidate portrait
    candidate_portrait: str     # natural language summary of candidate state
    # Behavioral pacing guidance (replaces raw emotional labels)
    pacing_hint: str            # e.g. "simplify the question", "let them think", "" (no hint)
    # Interview momentum
    momentum: str               # "accelerating", "steady", "stalling", "recovering"
    # Signal quality — what has already been verified on current topic
    verified_context: str       # e.g. "Already verified: mechanism, ownership. Try: debugging, tradeoffs."
    # Project grounding — specific project to anchor questions to
    project_grounding: str      # e.g. "They worked on OTA layout for a 28nm tapeout."
    # Resume grounding alert — flags when candidate drifts from resume/domain
    grounding_alert: str        # e.g. "Off-domain drift detected. Redirect to resume areas." or ""


# ── Redis key ────────────────────────────────────────────────────────────────

def _key_cognition(session_id: str) -> str:
    return f"session:{session_id}:cognition"


# ── Domain-specific topic maps ──────────────────────────────────────────────

_DOMAIN_TOPICS: dict[VLSIDomain, list[str]] = {
    VLSIDomain.PHYSICAL_DESIGN: [
        "floorplanning", "placement", "clock tree", "timing",
        "ir drop", "congestion", "routing", "eco", "signoff",
    ],
    VLSIDomain.ANALOG_LAYOUT: [
        "matching", "parasitics", "guard rings", "esd",
        "latch-up", "floorplan", "lvs drc", "extraction",
    ],
    VLSIDomain.DESIGN_VERIFICATION: [
        "uvm architecture", "testbench", "coverage",
        "assertions", "constrained random", "debugging",
        "scoreboard", "regression",
    ],
}


# ── Domain-native topic connections ──────────────────────────────────────────
# How topics naturally flow into each other in real interviews.
# These enable realistic transitions: "You mentioned CTS — how did that
# affect your timing margins?" instead of random topic jumps.

_TOPIC_BRIDGES: dict[VLSIDomain, dict[str, list[str]]] = {
    VLSIDomain.PHYSICAL_DESIGN: {
        "floorplanning":  ["placement", "congestion", "ir drop"],
        "placement":      ["congestion", "timing", "clock tree"],
        "clock tree":     ["timing", "ir drop", "signoff"],
        "timing":         ["clock tree", "eco", "signoff"],
        "ir drop":        ["floorplanning", "congestion", "signoff"],
        "congestion":     ["routing", "placement", "floorplanning"],
        "routing":        ["congestion", "timing", "signoff"],
        "eco":            ["timing", "signoff", "routing"],
        "signoff":        ["timing", "ir drop", "eco"],
    },
    VLSIDomain.ANALOG_LAYOUT: {
        "matching":       ["parasitics", "floorplan", "extraction"],
        "parasitics":     ["matching", "extraction", "guard rings"],
        "guard rings":    ["latch-up", "esd", "floorplan"],
        "esd":            ["guard rings", "latch-up", "lvs drc"],
        "latch-up":       ["guard rings", "esd", "floorplan"],
        "floorplan":      ["matching", "parasitics", "guard rings"],
        "lvs drc":        ["extraction", "parasitics", "esd"],
        "extraction":     ["parasitics", "matching", "lvs drc"],
    },
    VLSIDomain.DESIGN_VERIFICATION: {
        "uvm architecture": ["testbench", "scoreboard", "coverage"],
        "testbench":        ["uvm architecture", "constrained random", "debugging"],
        "coverage":         ["assertions", "regression", "constrained random"],
        "assertions":       ["coverage", "debugging", "testbench"],
        "constrained random": ["coverage", "testbench", "debugging"],
        "debugging":        ["assertions", "regression", "scoreboard"],
        "scoreboard":       ["uvm architecture", "debugging", "testbench"],
        "regression":       ["coverage", "debugging", "assertions"],
    },
}


# ── Domain-native conversational DNA ─────────────────────────────────────────
# Each domain has distinct interview patterns. These guide the LLM's
# behavior to sound like a real engineer from that domain.

_DOMAIN_VOICE: dict[VLSIDomain, dict[str, str]] = {
    VLSIDomain.PHYSICAL_DESIGN: {
        "probe": "PD engineers think in flows and closure. Ask about their methodology — what they ran, in what order, what broke, how they iterated. Ask about numbers: slack, utilization, congestion hotspots.",
        "deepen": "Push on the tradeoff they made. In PD, everything is a tradeoff — area vs timing, power vs performance, congestion vs routability. What did they sacrifice? What was the fallback plan?",
        "simplify": "Back up to the basics of their flow. What tool did they use? What was their input? What did they check first when something failed?",
        "pressure": "PD bluffs show up in numbers. Ask for specific slack values, utilization percentages, or what their congestion map looked like. Real experience has specific numbers.",
        "transition": "In PD, topics connect through the flow. CTS affects timing, timing affects ECO, congestion affects routing. Bridge through the flow, not randomly.",
    },
    VLSIDomain.ANALOG_LAYOUT: {
        "probe": "AL engineers think in physical constraints and matching strategies. Ask about their layout choices — why this topology, what drove the floorplan, how they handled the critical path.",
        "deepen": "Push on the parasitic impact of their decisions. In analog, every routing choice has consequences — capacitance, resistance, coupling. What simulation showed them the problem?",
        "simplify": "Go back to the physical intuition. Why does matching matter here? What happens if you don't use common centroid? What does the mismatch actually look like in silicon?",
        "pressure": "Analog layout bluffs show up in extraction results. Ask what their post-layout simulation showed vs pre-layout. Where did they see the biggest parasitic hit? What was the actual mismatch number?",
        "transition": "In analog layout, topics connect through the physical structure. Matching strategy affects parasitics, guard rings affect isolation which affects matching. Bridge through the physics.",
    },
    VLSIDomain.DESIGN_VERIFICATION: {
        "probe": "DV engineers think in coverage and corner cases. Ask about their verification strategy — how they decomposed the problem, what their coverage plan looked like, how they knew they were done.",
        "deepen": "Push on their debugging methodology. In DV, finding the bug is harder than writing the test. How did they isolate it? What was their hypothesis? How did they narrow down from regression failure to root cause?",
        "simplify": "Go back to the architecture of their testbench. What components did they write? What was the flow from stimulus to checking? Basic UVM architecture questions.",
        "pressure": "DV bluffs show up in coverage numbers and debug stories. Ask for their actual coverage closure percentage, what holes remained, what corner case they found that no one expected.",
        "transition": "In DV, topics connect through the verification lifecycle. Coverage drives regression strategy, assertions catch bugs that random tests miss, debugging feeds back into test planning. Bridge through the methodology.",
    },
}


# Depth progression labels
_DEPTH_LEVELS = ["untouched", "surface", "mechanism", "tradeoff", "edge-case"]


# ── Main entry point ─────────────────────────────────────────────────────────

async def assess(
    session_id: str,
    turn_number: int,
    transcript: str,
    domain: VLSIDomain,
    mode: InterviewerMode,
    memory: CandidateMemory,
    eval_scores: dict | None = None,
    resume: ResumeData | None = None,
) -> CognitionResult:
    """
    Assess the interview state and produce a strategic briefing.

    Called at the start of each turn, after reading session state + memory.
    Reads/writes cognition state from Redis (topic coverage, streaks).

    Returns CognitionResult with:
      - strategic_intent: what to do next (soft guidance, not rigid command)
      - domain_voice: how a domain-native engineer would behave
      - reconnection: earlier statements to reconnect to
      - pacing_hint: subtle behavioral guidance (not emotional labels)
      - transition_pressure: soft signal (none → consider → recommended → overdue)
      - verified_context: what signals have already been extracted on current topic
      - project_grounding: specific project to anchor questions to
    """
    cog_state = await _load_state(session_id)

    # Persist resume data in cognition state (cross-turn availability)
    if resume and not cog_state.get("resume_stored"):
        cog_state["resume_projects"] = resume.key_projects[:5]
        cog_state["resume_tools"] = resume.tools[:6]
        cog_state["resume_skills"] = resume.skills[:8]
        cog_state["resume_level"] = resume.level
        cog_state["resume_name"] = resume.candidate_name
        cog_state["resume_stored"] = True

    # Infer what topic the candidate is talking about
    current_topic = _infer_topic_from_transcript(transcript, domain)

    # Update topic tracking
    topic_states = cog_state.get("topics", {})
    prev_topic = cog_state.get("current_topic", "")
    ts = _get_or_create_topic(topic_states, current_topic)
    ts["turns_spent"] += 1

    # Track what candidate said for semantic reconnection
    _store_semantic_anchor(cog_state, current_topic, transcript, turn_number)

    # Update verified signals — what evaluation dimensions have been extracted
    from app.engines.memory import detect_verified_signals
    new_signals = detect_verified_signals(transcript)
    verified = ts.get("verified_signals", {})
    for sig in new_signals:
        verified[sig] = True
    ts["verified_signals"] = verified

    # Update depth and streak from eval scores (previous turn's)
    if eval_scores:
        avg = sum(eval_scores.values()) / len(eval_scores)
        ts["last_score"] = avg
        # Soft streak: partial scores dampen rather than reset
        if avg >= 7.0:
            ts["streak"] = max(1, ts.get("streak", 0) + 1)
        elif avg < 5.0:
            ts["streak"] = min(-1, ts.get("streak", 0) - 1)
        elif avg >= 5.5:
            old = ts.get("streak", 0)
            ts["streak"] = old + (1 if old < 0 else -1) if old != 0 else 0
        else:
            ts["streak"] = 0
        ts["depth_label"] = _compute_depth(ts)

    # Extract claims and gaps
    _extract_topic_signals(ts, transcript, memory)
    topic_states[current_topic] = ts

    # Resume grounding — validate candidate statement against resume/domain
    grounding_alert = _validate_grounding(
        transcript, domain, cog_state, turn_number
    )

    # Behavioral pacing (rare, subtle — only on clear struggle signals)
    pacing_hint = _compute_pacing_hint(transcript, ts, mode)

    # Transition pressure — signal-quality-driven, not just turn count
    transition_pressure = _compute_transition_pressure(
        ts, mode, topic_states, domain
    )

    # Best transition target (connected topic, preferring resume-mentioned areas)
    transition_target = None
    if transition_pressure in ("recommended", "overdue"):
        transition_target = _pick_connected_target(
            current_topic, topic_states, domain, memory,
            resume_projects=cog_state.get("resume_projects", []),
            resume_skills=cog_state.get("resume_skills", []),
        )

    # Momentum
    momentum = _compute_momentum(cog_state, ts, mode)

    # Recommended action (soft — the LLM can override)
    action = _compute_action(
        ts, mode, transition_pressure, momentum, memory,
        turn_number, pacing_hint
    )

    # Domain-native voice
    domain_voice = _get_domain_voice(domain, action)

    # Semantic reconnection — find earlier statements to reference
    reconnection = _find_reconnection(
        cog_state, current_topic, domain, turn_number
    )

    # Build verified context string (anti-repetition)
    verified_context = _build_verified_context(ts)

    # Project grounding — find a specific project to anchor to
    project_grounding = _build_project_grounding(
        cog_state, current_topic, transcript, domain
    )

    # Strategic intent (natural language)
    strategic_intent = _build_strategic_intent(
        current_topic, ts, action, transition_pressure,
        transition_target, mode, memory, pacing_hint, domain,
        project_grounding, verified_context,
    )

    # Candidate portrait
    candidate_portrait = _build_candidate_portrait(memory, topic_states)

    # Persist
    cog_state["topics"] = topic_states
    cog_state["current_topic"] = current_topic
    cog_state["last_momentum"] = momentum
    cog_state["last_turn"] = turn_number
    await _save_state(session_id, cog_state)

    return CognitionResult(
        strategic_intent=strategic_intent,
        domain_voice=domain_voice,
        reconnection=reconnection,
        current_topic=current_topic,
        turns_on_topic=ts["turns_spent"],
        topic_depth=ts.get("depth_label", "surface"),
        transition_pressure=transition_pressure,
        transition_target=transition_target,
        candidate_portrait=candidate_portrait,
        pacing_hint=pacing_hint,
        momentum=momentum,
        verified_context=verified_context,
        project_grounding=project_grounding,
        grounding_alert=grounding_alert,
    )


# ── Topic inference ──────────────────────────────────────────────────────────

def _infer_topic_from_transcript(transcript: str, domain: VLSIDomain) -> str:
    """Lightweight keyword-based topic detection from candidate's answer."""
    t = transcript.lower()

    _TOPIC_KEYWORDS = {
        # Physical Design
        "floorplanning":   ["floorplan", "floorplanning", "macro placement", "partition"],
        "placement":       ["placement", "legalization", "utilization"],
        "clock tree":      ["cts", "clock tree", "clock skew", "clock latency", "insertion delay"],
        "timing":          ["timing", "setup", "hold", "slack", "violation", "sta", "primetime"],
        "ir drop":         ["ir drop", "voltage drop", "power grid", "electromigration", "em"],
        "congestion":      ["congestion", "routing congestion", "overflow", "density"],
        "routing":         ["route", "routing", "antenna", "crosstalk", "shielding"],
        "eco":             ["eco", "engineering change", "spare cell", "metal fix"],
        "signoff":         ["signoff", "sign-off", "tapeout", "gds", "drc final"],
        # Analog Layout
        "matching":        ["match", "mismatch", "common centroid", "interdigitation", "symmetry"],
        "parasitics":      ["parasitic", "capacitance", "resistance", "coupling", "rc extraction"],
        "guard rings":     ["guard ring", "isolation", "well tap", "substrate contact"],
        "esd":             ["esd", "electrostatic", "protection", "clamp", "diode"],
        "latch-up":        ["latch-up", "latchup", "scr", "thyristor", "trigger"],
        "lvs drc":         ["lvs", "drc", "layout versus schematic", "design rule"],
        "extraction":      ["extraction", "rcx", "starrc", "qrc", "parasitic extraction"],
        # Design Verification
        "uvm architecture": ["uvm", "agent", "driver", "monitor", "sequencer", "env"],
        "testbench":       ["testbench", "test bench", "tb", "stimulus"],
        "coverage":        ["coverage", "functional coverage", "code coverage", "covergroup"],
        "assertions":      ["assertion", "sva", "property", "sequence", "assume", "assert"],
        "constrained random": ["constrained random", "randomize", "constraint", "solver"],
        "debugging":       ["debug", "waveform", "simulation", "log", "trace"],
        "scoreboard":      ["scoreboard", "checker", "comparator", "golden model"],
        "regression":      ["regression", "nightly", "ci", "pass rate", "seed"],
    }

    best_topic = None
    best_score = 0
    for topic, keywords in _TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in t)
        if score > best_score:
            best_score = score
            best_topic = topic

    if best_topic and best_score > 0:
        return best_topic

    return {
        VLSIDomain.PHYSICAL_DESIGN: "physical design general",
        VLSIDomain.ANALOG_LAYOUT: "analog layout general",
        VLSIDomain.DESIGN_VERIFICATION: "verification general",
    }.get(domain, "general")


# ── Topic state helpers ──────────────────────────────────────────────────────

def _get_or_create_topic(topic_states: dict, topic: str) -> dict:
    if topic not in topic_states:
        topic_states[topic] = {
            "name": topic,
            "turns_spent": 0,
            "last_score": 5.0,
            "streak": 0,
            "claims": [],
            "gaps": [],
            "depth_label": "untouched",
            "verified_signals": {},  # {mechanism: True, ownership: True, ...}
        }
    # Backfill for existing topics missing the field
    if "verified_signals" not in topic_states[topic]:
        topic_states[topic]["verified_signals"] = {}
    return topic_states[topic]


def _compute_depth(ts: dict) -> str:
    """Advance depth label based on score and turns. Gradual, not step-function."""
    turns = ts.get("turns_spent", 0)
    score = ts.get("last_score", 5.0)
    current = ts.get("depth_label", "untouched")
    idx = _DEPTH_LEVELS.index(current) if current in _DEPTH_LEVELS else 0

    # Strong answer advances depth
    if score >= 7.0 and turns >= 1:
        idx = min(idx + 1, len(_DEPTH_LEVELS) - 1)
    # Moderate answer on deep topic — hold depth
    # Weak answer — don't advance but don't regress either
    return _DEPTH_LEVELS[idx]


def _extract_topic_signals(ts: dict, transcript: str, memory: CandidateMemory) -> None:
    """Extract claims and gaps from transcript and memory."""
    if memory.claims:
        for claim in memory.claims[-3:]:
            if claim not in ts.get("claims", []):
                ts.setdefault("claims", []).append(claim)
        ts["claims"] = ts.get("claims", [])[-5:]

    buzzwords_without_mechanism = [b.term for b in memory.buzzwords if b.count >= 2]
    if buzzwords_without_mechanism:
        for bw in buzzwords_without_mechanism:
            gap = f"used '{bw}' without explaining mechanism"
            if gap not in ts.get("gaps", []):
                ts.setdefault("gaps", []).append(gap)
        ts["gaps"] = ts.get("gaps", [])[-3:]


# ── Semantic memory (anchors for reconnection) ──────────────────────────────

def _store_semantic_anchor(
    cog_state: dict, topic: str, transcript: str, turn_number: int
) -> None:
    """
    Store notable fragments from candidate's answer for later reconnection.
    These are short phrases the interviewer can reference turns later:
    "You mentioned X earlier — how does that relate to Y?"
    """
    anchors = cog_state.setdefault("anchors", [])

    # Extract notable claims, numbers, and specific statements
    # Keep it lightweight — just grab sentences with strong signal
    sentences = [s.strip() for s in transcript.replace(".", ". ").split(". ") if len(s.strip()) > 20]

    for sentence in sentences[:2]:  # max 2 anchors per turn
        s_lower = sentence.lower()
        # Store if it contains: a claim, a number, a tradeoff, a problem
        has_signal = any(marker in s_lower for marker in [
            "i ", "we ", "our ", "my ",                          # personal experience
            "because", "due to", "caused", "led to", "affected", # causality
            "tradeoff", "trade-off", "instead", "rather than",   # decisions
            "problem", "issue", "challenge", "failure", "broke", # problems
            "%", "mhz", "ghz", "ps", "ns", "nm", "mv",         # numbers
        ])
        if has_signal:
            anchors.append({
                "text": sentence[:120],
                "topic": topic,
                "turn": turn_number,
            })

    # Keep last 15 anchors
    cog_state["anchors"] = anchors[-15:]


def _find_reconnection(
    cog_state: dict,
    current_topic: str,
    domain: VLSIDomain,
    turn_number: int,
) -> str:
    """
    Find an earlier candidate statement that connects to the current topic.
    This creates conversational realism:
    "You mentioned clock mesh increased power earlier. How did that affect IR margins?"
    """
    anchors = cog_state.get("anchors", [])
    if not anchors:
        return ""

    # Find anchors from DIFFERENT topics that bridge to current topic
    bridges = _TOPIC_BRIDGES.get(domain, {}).get(current_topic, [])
    # Also look for anchors from the current topic (from earlier turns)
    related_topics = set(bridges + [current_topic])

    candidates = []
    for anchor in anchors:
        # Must be from at least 2 turns ago (don't reference what they just said)
        if turn_number - anchor.get("turn", turn_number) < 2:
            continue
        anchor_topic = anchor.get("topic", "")
        if anchor_topic in related_topics or anchor_topic == current_topic:
            candidates.append(anchor)

    if not candidates:
        return ""

    # Pick the most relevant (most recent from a different topic)
    # Prefer cross-topic reconnections over same-topic ones
    cross_topic = [a for a in candidates if a.get("topic") != current_topic]
    best = cross_topic[-1] if cross_topic else candidates[-1]

    return f"Earlier (turn {best['turn']}), they said: \"{best['text'][:80]}\" — you can reconnect this to what they're saying now."


# ── Resume grounding + statement validation ─────────────────────────────────

# Domain keyword sets for drift detection
_DOMAIN_KEYWORDS: dict[VLSIDomain, frozenset[str]] = {
    VLSIDomain.PHYSICAL_DESIGN: frozenset([
        "floorplan", "placement", "cts", "clock", "timing", "sta", "setup", "hold",
        "routing", "congestion", "ir drop", "power grid", "eco", "signoff", "tapeout",
        "synthesis", "netlist", "liberty", "sdc", "sdf", "def", "lef", "gds",
        "primetime", "icc2", "innovus", "slack", "skew", "insertion delay",
        "utilization", "density", "via", "metal", "buffer", "inverter",
        "electromigration", "antenna", "crosstalk", "noise", "drc", "lvs",
    ]),
    VLSIDomain.ANALOG_LAYOUT: frozenset([
        "matching", "mismatch", "common centroid", "interdigitation", "parasitic",
        "extraction", "guard ring", "latch-up", "esd", "drc", "lvs", "virtuoso",
        "calibre", "starrc", "assura", "layout", "schematic", "transistor",
        "capacitor", "resistor", "ota", "amplifier", "bandgap", "ldo", "pll",
        "adc", "dac", "current mirror", "diff pair", "differential",
        "substrate", "well", "metal", "routing", "shielding", "symmetry",
        "floorplan", "dummy", "finger", "post-layout", "pre-layout", "simulation",
    ]),
    VLSIDomain.DESIGN_VERIFICATION: frozenset([
        "uvm", "testbench", "driver", "monitor", "sequencer", "scoreboard",
        "coverage", "assertion", "sva", "constrained random", "regression",
        "simulation", "debug", "waveform", "protocol", "axi", "apb", "ahb",
        "vcs", "xcelium", "questa", "formal", "verification", "stimulus",
        "checker", "agent", "sequence", "factory", "config_db", "ral",
        "functional coverage", "code coverage", "covergroup", "property",
    ]),
}

# Clearly off-domain keywords — things that should never appear in a VLSI interview
_OFF_DOMAIN_MARKERS = frozenset([
    "steel", "construction", "building", "concrete", "civil engineering",
    "web development", "javascript", "react", "python django", "machine learning",
    "database", "sql server", "cloud computing", "aws lambda", "kubernetes",
    "marketing", "sales", "accounting", "finance", "hr", "recruitment",
    "cooking", "sports", "music", "movie", "game",
])


def _validate_grounding(
    transcript: str,
    domain: VLSIDomain,
    cog_state: dict,
    turn_number: int,
) -> str:
    """
    Validate candidate's statement against resume and domain.
    Returns a grounding alert string if drift is detected, empty string otherwise.

    Resume-derived info has HIGHER TRUST than live conversational input.
    This prevents the interviewer from being derailed by noise, fake claims,
    or off-domain tangents.

    Classification:
      - on_domain: statement relates to interview domain → follow normally
      - off_domain: statement is clearly outside VLSI → redirect
      - domain_mismatch: statement is VLSI but wrong domain → note but don't follow
      - resume_inconsistent: statement contradicts resume data → challenge
      - noise: very short / incoherent → ignore, ask a focused question
    """
    t = transcript.lower().strip()
    words = t.split()

    # Skip validation on very early turns (intro/discovery)
    if turn_number <= 3:
        return ""

    # Noise detection — very short or incoherent
    if len(words) < 3:
        return "Very short or unclear answer. Ask a focused, specific question about their resume experience."

    # Off-domain detection — clearly non-VLSI content
    if any(marker in t for marker in _OFF_DOMAIN_MARKERS):
        resume_projects = cog_state.get("resume_projects", [])
        if resume_projects:
            return f"Off-topic drift detected. Redirect to their actual experience: {', '.join(str(p) for p in resume_projects[:2])}."
        return "Off-topic drift detected. Redirect to their domain experience."

    # Domain keyword check
    domain_kws = _DOMAIN_KEYWORDS.get(domain, frozenset())
    domain_match_count = sum(1 for kw in domain_kws if kw in t)

    # Check for wrong-domain VLSI content
    other_domains = [d for d in VLSIDomain if d != domain]
    other_match_counts = {}
    for other_d in other_domains:
        other_kws = _DOMAIN_KEYWORDS.get(other_d, frozenset())
        other_match_counts[other_d] = sum(1 for kw in other_kws if kw in t)

    # If another domain matches more strongly than the interview domain
    max_other = max(other_match_counts.values()) if other_match_counts else 0
    if max_other > domain_match_count and max_other >= 2 and domain_match_count == 0:
        domain_name = {
            VLSIDomain.ANALOG_LAYOUT: "analog layout",
            VLSIDomain.PHYSICAL_DESIGN: "physical design",
            VLSIDomain.DESIGN_VERIFICATION: "design verification",
        }.get(domain, "their domain")
        return f"Candidate is drifting into a different domain. Stay focused on {domain_name}."

    # Resume consistency check — does claim match resume level/tools/skills?
    resume_level = cog_state.get("resume_level", "")
    resume_tools = [str(t).lower() for t in cog_state.get("resume_tools", [])]
    resume_skills = [str(s).lower() for s in cog_state.get("resume_skills", [])]

    # Expert claims from a fresher — suspicious
    if resume_level in ("fresh_graduate", "trained_fresher"):
        expert_claims = [
            "i led the tapeout", "i architected", "i owned the entire",
            "10 years", "15 years", "20 years", "senior architect",
            "i managed the team", "i directed",
        ]
        if any(ec in t for ec in expert_claims):
            return "Candidate claims don't match resume level. Verify ownership — ask for specific details about their actual role."

    return ""


# ── Behavioral pacing (subtle, not emotional labels) ────────────────────────

def _compute_pacing_hint(
    transcript: str,
    ts: dict,
    mode: InterviewerMode,
) -> str:
    """
    Produce rare, subtle behavioral guidance. Only fires on clear signals.
    Returns empty string in MOST cases — interviewer should default to
    calm, technically focused behavior without explicit guidance.

    NEVER produces emotional language. NEVER produces reassurance phrases.
    Only produces technical redirection hints.
    """
    t = transcript.lower()
    word_count = len(transcript.split())

    # Clear admission of gap — simplify (rare, only on explicit "I don't know")
    if any(p in t for p in ["i don't know", "i'm not sure", "not familiar"]):
        if word_count < 20:  # Only if it's genuinely a gap admission, not part of a longer answer
            return "Gap admitted. Simplify or try a different angle."

    # Textbook-sounding — push toward personal experience (only on strong signal)
    if word_count > 50:
        textbook_markers = sum(1 for p in ["is defined as", "refers to", "is used for", "is a type of", "stands for", "is known as"] if p in t)
        if textbook_markers >= 2:
            return "Textbook answer. Ask what they specifically did on their project."

    return ""


# ── Transition pressure (graduated, not binary) ─────────────────────────────

def _compute_transition_pressure(
    ts: dict,
    mode: InterviewerMode,
    all_topics: dict,
    domain: VLSIDomain,
) -> str:
    """
    Returns graduated transition pressure: none → consider → recommended → overdue.

    PRIMARY driver: signal quality (how many dimensions verified on this topic).
    SECONDARY driver: turn count (soft backup, not primary trigger).

    The interviewer should move on when it has ENOUGH SIGNAL, not after N turns.
    """
    turns = ts.get("turns_spent", 0)
    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")
    verified = ts.get("verified_signals", {})
    n_verified = sum(1 for v in verified.values() if v)

    # Strategy engine already decided to transition
    if mode == InterviewerMode.TRANSITIONING:
        return "recommended"

    # Signal quality saturation — primary transition trigger
    # 4+ signals verified: we know mechanism, ownership, tradeoff, debugging etc.
    if n_verified >= 4:
        return "overdue"
    # 3 signals verified: strong recommendation to move on
    if n_verified >= 3 and turns >= 2:
        return "recommended"

    # Candidate mastered it — strong streak + decent depth
    if streak >= 2 and depth in ("mechanism", "tradeoff", "edge-case") and turns >= 2:
        return "recommended"

    # Candidate struggling persistently — enough signal exists
    if turns >= 2 and streak <= -2:
        return "recommended"

    # Coverage concern — many unexplored topics remain
    domain_topics = _DOMAIN_TOPICS.get(domain, [])
    explored = sum(
        1 for t in domain_topics
        if t in all_topics and all_topics[t].get("turns_spent", 0) > 0
    )
    if turns >= 2 and explored < len(domain_topics) // 2:
        return "consider"

    # 2+ signals verified — starting to saturate
    if n_verified >= 2 and turns >= 2:
        return "consider"

    # Soft signal — spending a while here
    if turns >= 3:
        return "consider"

    return "none"


# ── Connected transition target ──────────────────────────────────────────────

def _pick_connected_target(
    current_topic: str,
    topic_states: dict,
    domain: VLSIDomain,
    memory: CandidateMemory,
    resume_projects: list[str] | None = None,
    resume_skills: list[str] | None = None,
) -> str | None:
    """
    Pick next topic that CONNECTS to current topic.
    Uses topic bridge map — transitions feel natural because topics
    are related in the real engineering workflow.

    Priority order:
      1. Connected topic mentioned in resume (highest realism)
      2. Connected topic that's a known weakness
      3. Connected + unexplored
      4. Resume-mentioned topic (even if not directly connected)
      5. Any unexplored topic in domain
    """
    bridges = _TOPIC_BRIDGES.get(domain, {}).get(current_topic, [])
    domain_topics = _DOMAIN_TOPICS.get(domain, [])
    resume_keywords = set()
    for item in (resume_projects or []) + (resume_skills or []):
        resume_keywords.update(str(item).lower().split())

    def _matches_resume(topic_name: str) -> bool:
        """Check if a topic relates to something in the resume."""
        topic_words = set(topic_name.lower().replace("_", " ").split())
        return bool(topic_words & resume_keywords)

    # Priority 1: Connected topic mentioned in resume
    for topic in bridges:
        if _matches_resume(topic):
            ts = topic_states.get(topic, {})
            if ts.get("turns_spent", 0) < 2:
                return topic

    # Priority 2: Connected topic that's a known weakness
    weak_names = {t.topic for t in memory.weak_topics}
    for topic in bridges:
        if topic in weak_names:
            ts = topic_states.get(topic, {})
            if ts.get("turns_spent", 0) < 2:
                return topic

    # Priority 3: Connected + unexplored
    for topic in bridges:
        if topic not in topic_states:
            return topic

    # Priority 4: Resume-mentioned topic (even if not directly connected)
    for topic in domain_topics:
        if topic != current_topic and _matches_resume(topic):
            ts = topic_states.get(topic, {})
            if ts.get("turns_spent", 0) < 2:
                return topic

    # Priority 5: Connected topic that's least-explored
    for topic in bridges:
        ts = topic_states.get(topic, {})
        if ts.get("turns_spent", 0) < 2:
            return topic

    # Priority 6: Any unexplored topic in domain
    for topic in domain_topics:
        if topic not in topic_states and topic != current_topic:
            return topic

    # Priority 7: Least-explored domain topic
    least = None
    least_turns = float("inf")
    for topic in domain_topics:
        if topic == current_topic:
            continue
        ts = topic_states.get(topic, {})
        t = ts.get("turns_spent", 0)
        if t < least_turns:
            least_turns = t
            least = topic
    return least


# ── Momentum ─────────────────────────────────────────────────────────────────

def _compute_momentum(cog_state: dict, ts: dict, mode: InterviewerMode) -> str:
    streak = ts.get("streak", 0)
    if streak >= 2:
        return "accelerating"
    elif streak <= -2:
        return "recovering" if mode == InterviewerMode.RECOVERING else "stalling"
    return "steady"


# ── Action computation ───────────────────────────────────────────────────────

def _compute_action(
    ts: dict,
    mode: InterviewerMode,
    transition_pressure: str,
    momentum: str,
    memory: CandidateMemory,
    turn_number: int,
    pacing_hint: str,
) -> str:
    """
    Determine recommended action. This is a SUGGESTION — the LLM
    may choose differently based on conversational context.

    Emotional awareness is folded into pacing_hint (passed to strategic intent)
    rather than driving separate action types. This prevents therapeutic behavior.
    """
    # Hard transition signal
    if transition_pressure == "overdue":
        return "transition"

    # Early interview — explore
    if turn_number <= 4:
        return "probe"

    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")
    verified = ts.get("verified_signals", {})
    n_verified = sum(1 for v in verified.values() if v)

    # Soft transition — enough signal collected
    if transition_pressure == "recommended":
        return "transition"

    # Signal-quality-driven: if concept + mechanism verified, try project/debugging
    if n_verified >= 2 and "debugging" not in verified and "project_specific" not in verified:
        return "project_ground"  # push toward project-specific or debugging

    # Strength-based actions
    if streak >= 2 and depth in ("surface", "mechanism"):
        return "deepen"

    # Weakness-based actions
    if streak <= -2 or mode == InterviewerMode.RECOVERING:
        return "simplify"

    # Mode-driven
    if mode == InterviewerMode.PRESSURE:
        return "pressure"
    if mode == InterviewerMode.DEEPENING:
        return "deepen"
    if mode == InterviewerMode.ESCALATING:
        return "escalate"

    # Contradiction exists
    unresolved = [c for c in memory.contradictions if not c.resolved]
    if unresolved:
        return "pressure"

    return "probe"


# ── Domain-native voice ──────────────────────────────────────────────────────

def _get_domain_voice(domain: VLSIDomain, action: str) -> str:
    """Return domain-specific behavioral guidance for the current action."""
    voices = _DOMAIN_VOICE.get(domain)
    if not voices:
        return ""
    # Map action to closest voice key
    key_map = {
        "probe": "probe", "deepen": "deepen", "simplify": "simplify",
        "pressure": "pressure", "escalate": "pressure",
        "transition": "transition", "project_ground": "pressure",
    }
    key = key_map.get(action, "probe")
    return voices.get(key, "")


# ── Verified signal context (anti-repetition) ──────────────────────────────

def _build_verified_context(ts: dict) -> str:
    """
    Build a concise string describing what signals have been verified on this topic.
    Injected into the prompt so the LLM avoids asking semantically similar questions.
    """
    verified = ts.get("verified_signals", {})
    if not verified:
        return ""

    done = [k for k, v in verified.items() if v]
    if not done:
        return ""

    all_dims = ["concept", "mechanism", "ownership", "tradeoff", "implementation", "debugging", "project_specific"]
    remaining = [d for d in all_dims if d not in done]

    parts = []
    if done:
        parts.append(f"Already verified: {', '.join(done)}.")
    if remaining:
        parts.append(f"Try next: {', '.join(remaining[:3])}.")

    return " ".join(parts)


# ── Project grounding ───────────────────────────────────────────────────────

def _build_project_grounding(
    cog_state: dict,
    current_topic: str,
    transcript: str,
    domain: VLSIDomain,
) -> str:
    """
    Find a specific project or experience from the resume/conversation
    to anchor questions to. Prevents generic questioning.
    """
    projects = cog_state.get("resume_projects", [])
    tools = cog_state.get("resume_tools", [])
    level = cog_state.get("resume_level", "")

    # Check if candidate mentioned a specific project in their answer
    t = transcript.lower()
    project_phrases = [
        "project", "tapeout", "tape-out", "chip", "block", "ip",
        "product", "design", "soc", "asic",
    ]
    candidate_mentioned_project = any(p in t for p in project_phrases)

    parts = []

    # If candidate mentioned a project, encourage following up on it
    if candidate_mentioned_project:
        parts.append("They mentioned a specific project — follow up on what they actually did there.")

    # If resume has projects, suggest grounding
    if projects:
        # Pick the project most related to current topic
        topic_lower = current_topic.lower().replace("_", " ")
        best_project = None
        for proj in projects:
            if any(word in str(proj).lower() for word in topic_lower.split()):
                best_project = proj
                break
        if best_project:
            parts.append(f"Their resume mentions '{best_project}' — connect to that.")
        elif projects:
            parts.append(f"They worked on: {', '.join(str(p) for p in projects[:2])}.")

    # If resume has tools relevant to discussion
    if tools:
        tools_lower = [str(t).lower() for t in tools]
        if any(t in transcript.lower() for t in tools_lower):
            parts.append("They mentioned tools from their resume — ask how they specifically used them.")

    return " ".join(parts) if parts else ""


# ── Strategic intent builder ─────────────────────────────────────────────────

def _build_strategic_intent(
    topic: str,
    ts: dict,
    action: str,
    transition_pressure: str,
    transition_target: str | None,
    mode: InterviewerMode,
    memory: CandidateMemory,
    pacing_hint: str,
    domain: VLSIDomain,
    project_grounding: str = "",
    verified_context: str = "",
) -> str:
    """
    Build natural-language strategic briefing.
    Written as soft guidance — reads like a colleague's note, not a system command.
    Transitions reference conversation content, not topic labels.
    """
    turns = ts.get("turns_spent", 0)
    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")
    gaps = ts.get("gaps", [])
    claims = ts.get("claims", [])
    verified = ts.get("verified_signals", {})

    parts = []

    # Action guidance (conversational language, never system-directive)
    if action == "transition":
        if transition_target:
            bridges = _TOPIC_BRIDGES.get(domain, {}).get(topic, [])
            if transition_target in bridges:
                parts.append(f"You have enough signal on {topic}. {transition_target} connects naturally — bridge through something they said earlier.")
            else:
                parts.append(f"You have enough signal on {topic}. Shift toward {transition_target} by connecting it to their background or a project they mentioned.")
        else:
            parts.append(f"You have enough signal on {topic}. Pick up on something else from their background.")

    elif action == "project_ground":
        parts.append(f"You've covered the concept on {topic}. Now ask about their actual project — what they specifically did, what went wrong, what decisions they made.")

    elif action == "deepen":
        parts.append(f"They're solid on {topic}. Push toward tradeoffs, debugging, or what breaks under real constraints.")

    elif action == "simplify":
        parts.append(f"They're struggling. Narrow the scope or ask from a more basic angle.")

    elif action == "pressure":
        unresolved = [c for c in memory.contradictions if not c.resolved]
        if unresolved:
            c = unresolved[0]
            parts.append(f'They said "{c.statement_a[:50]}" but also "{c.statement_b[:50]}". Surface this calmly.')
        else:
            parts.append(f"Push for specifics — numbers, personal decisions, or what actually failed.")

    elif action == "escalate":
        parts.append(f"Answer was surface-level. Ask for the mechanism, their specific contribution, or what happened in practice.")

    else:
        parts.append(f"Follow up naturally on what they just said about {topic}.")

    # Pacing hint (subtle behavioral guidance, replaces emotional labels)
    if pacing_hint:
        parts.append(pacing_hint)

    # Verified signals anti-repetition
    if verified_context:
        parts.append(verified_context)

    # Project grounding
    if project_grounding and action not in ("transition",):
        parts.append(project_grounding)

    # Gaps to probe (when appropriate)
    if gaps and action in ("pressure", "escalate", "deepen", "project_ground"):
        parts.append(f"Known gap: {gaps[0]}.")

    # Claims to test
    if claims and action in ("pressure", "deepen"):
        parts.append(f'They claimed: "{claims[-1][:60]}" — see if this holds.')

    # Soft transition consideration
    if transition_pressure == "consider" and action != "transition":
        parts.append("You may have enough signal here — consider moving on soon.")

    return " ".join(parts)


# ── Candidate portrait ───────────────────────────────────────────────────────

def _build_candidate_portrait(
    memory: CandidateMemory,
    topic_states: dict,
) -> str:
    """Natural-language summary of candidate's knowledge state and coverage.
    No emotional labels — only factual observations."""
    parts = []

    # Strong areas
    strong = sorted(memory.strong_topics, key=lambda t: t.avg_score, reverse=True)[:2]
    if strong:
        parts.append(f"Strong in: {', '.join(t.topic for t in strong)}.")

    # Weak areas
    weak = sorted(memory.weak_topics, key=lambda t: t.avg_score)[:2]
    if weak:
        parts.append(f"Weak in: {', '.join(t.topic for t in weak)}.")

    # Coverage
    explored = [t for t, s in topic_states.items() if s.get("turns_spent", 0) > 0]
    if explored:
        parts.append(f"Covered: {', '.join(explored[:5])}.")

    # Contradictions
    unresolved = [c for c in memory.contradictions if not c.resolved]
    if unresolved:
        parts.append(f"{len(unresolved)} unresolved contradiction(s).")

    # Buzzword pattern
    repeated_bw = [b.term for b in memory.buzzwords if b.count >= 3]
    if repeated_bw:
        parts.append(f"Repeats without depth: {', '.join(repeated_bw[:3])}.")

    return " ".join(parts) if parts else ""


# ── Redis persistence ────────────────────────────────────────────────────────

async def _load_state(session_id: str) -> dict:
    try:
        rds = r._get_pool()
        raw = await rds.get(_key_cognition(session_id))
        if raw:
            return json.loads(raw)
    except Exception as e:
        log.warning("cognition.load_failed", error=str(e))
    return {}


async def _save_state(session_id: str, state: dict) -> None:
    try:
        rds = r._get_pool()
        await rds.setex(
            _key_cognition(session_id),
            settings.SESSION_TTL,
            json.dumps(state, default=str),
        )
    except Exception as e:
        log.warning("cognition.save_failed", error=str(e))
