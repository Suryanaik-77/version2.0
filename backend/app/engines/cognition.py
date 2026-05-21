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
    # Emotional pacing
    emotional_read: str         # "confident", "nervous", "defensive", "honest", "flat"
    # Interview momentum
    momentum: str               # "accelerating", "steady", "stalling", "recovering"


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
) -> CognitionResult:
    """
    Assess the interview state and produce a strategic briefing.

    Called at the start of each turn, after reading session state + memory.
    Reads/writes cognition state from Redis (topic coverage, streaks).

    Returns CognitionResult with:
      - strategic_intent: what to do next (soft guidance, not rigid command)
      - domain_voice: how a domain-native engineer would behave
      - reconnection: earlier statements to reconnect to
      - emotional_read: candidate's emotional state
      - transition_pressure: soft signal (none → consider → recommended → overdue)
    """
    cog_state = await _load_state(session_id)

    # Infer what topic the candidate is talking about
    current_topic = _infer_topic_from_transcript(transcript, domain)

    # Update topic tracking
    topic_states = cog_state.get("topics", {})
    prev_topic = cog_state.get("current_topic", "")
    ts = _get_or_create_topic(topic_states, current_topic)
    ts["turns_spent"] += 1

    # Track what candidate said for semantic reconnection
    _store_semantic_anchor(cog_state, current_topic, transcript, turn_number)

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
            # Partial answer — don't reset streak, just dampen toward 0
            old = ts.get("streak", 0)
            ts["streak"] = old + (1 if old < 0 else -1) if old != 0 else 0
        else:
            ts["streak"] = 0
        ts["depth_label"] = _compute_depth(ts)

    # Extract claims and gaps
    _extract_topic_signals(ts, transcript, memory)
    topic_states[current_topic] = ts

    # Emotional read from transcript patterns
    emotional_read = _read_emotional_state(transcript, ts, mode)

    # Transition pressure (soft, graduated)
    transition_pressure = _compute_transition_pressure(
        ts, mode, topic_states, domain
    )

    # Best transition target (connected topic, not random)
    transition_target = None
    if transition_pressure in ("recommended", "overdue"):
        transition_target = _pick_connected_target(
            current_topic, topic_states, domain, memory
        )

    # Momentum
    momentum = _compute_momentum(cog_state, ts, mode)

    # Recommended action (soft — the LLM can override)
    action = _compute_action(
        ts, mode, transition_pressure, momentum, memory,
        turn_number, emotional_read
    )

    # Domain-native voice
    domain_voice = _get_domain_voice(domain, action)

    # Semantic reconnection — find earlier statements to reference
    reconnection = _find_reconnection(
        cog_state, current_topic, domain, turn_number
    )

    # Strategic intent (natural language)
    strategic_intent = _build_strategic_intent(
        current_topic, ts, action, transition_pressure,
        transition_target, mode, memory, emotional_read, domain
    )

    # Candidate portrait
    candidate_portrait = _build_candidate_portrait(memory, topic_states, emotional_read)

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
        emotional_read=emotional_read,
        momentum=momentum,
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
        }
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


# ── Emotional state detection ────────────────────────────────────────────────

def _read_emotional_state(
    transcript: str,
    ts: dict,
    mode: InterviewerMode,
) -> str:
    """
    Heuristic read of candidate's emotional state from transcript patterns.
    Guides interviewer pacing — don't pressure a nervous candidate,
    don't go soft on a confident one.
    """
    t = transcript.lower()
    word_count = len(transcript.split())

    # Honest admission patterns
    if any(p in t for p in ["i don't know", "i'm not sure", "not familiar", "haven't worked", "i don't remember"]):
        return "honest"

    # Nervous patterns — very short answers, hedging language
    hedging = sum(1 for p in ["i think", "maybe", "probably", "i guess", "not exactly sure", "sort of", "kind of"] if p in t)
    if hedging >= 2 or (word_count < 15 and hedging >= 1):
        return "nervous"

    # Defensive patterns — deflecting, blaming tools/team
    if any(p in t for p in ["that wasn't my responsibility", "someone else handled", "the tool did", "it was automated"]):
        return "defensive"

    # Confident patterns — specific claims, numbers, first-person ownership
    confident_markers = sum(1 for p in [
        "i designed", "i implemented", "i built", "i debugged", "i led", "i owned",
        "i decided", "i chose", "the result was", "we achieved",
    ] if p in t)
    if confident_markers >= 2 or (word_count > 60 and confident_markers >= 1):
        return "confident"

    # Flat — going through the motions, textbook-ish
    if word_count > 40 and hedging == 0 and confident_markers == 0:
        textbook = any(p in t for p in ["is defined as", "refers to", "is used for", "is a type of", "stands for"])
        if textbook:
            return "flat"

    return "neutral"


# ── Transition pressure (graduated, not binary) ─────────────────────────────

def _compute_transition_pressure(
    ts: dict,
    mode: InterviewerMode,
    all_topics: dict,
    domain: VLSIDomain,
) -> str:
    """
    Returns graduated transition pressure: none → consider → recommended → overdue.

    These are SOFT signals. The LLM can stay on topic if the conversation
    is genuinely productive. But if pressure is "overdue", the strategic
    intent will strongly suggest moving on.
    """
    turns = ts.get("turns_spent", 0)
    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")

    # Strategy engine already decided to transition
    if mode == InterviewerMode.TRANSITIONING:
        return "recommended"

    # Topic fully explored — high depth + many turns
    if turns >= 4 and depth in ("tradeoff", "edge-case"):
        return "overdue" if turns >= 5 else "recommended"

    # Candidate mastered it — strong streak + decent depth
    if turns >= 3 and streak >= 2 and depth in ("mechanism", "tradeoff", "edge-case"):
        return "recommended"

    # Candidate struggling persistently — enough signal
    if turns >= 3 and streak <= -2:
        return "recommended"

    # Coverage concern — many unexplored topics
    domain_topics = _DOMAIN_TOPICS.get(domain, [])
    explored = sum(
        1 for t in domain_topics
        if t in all_topics and all_topics[t].get("turns_spent", 0) > 0
    )
    if turns >= 3 and explored < len(domain_topics) // 2:
        return "consider"

    # Soft signal — starting to spend a while here
    if turns >= 3:
        return "consider"

    return "none"


# ── Connected transition target ──────────────────────────────────────────────

def _pick_connected_target(
    current_topic: str,
    topic_states: dict,
    domain: VLSIDomain,
    memory: CandidateMemory,
) -> str | None:
    """
    Pick next topic that CONNECTS to current topic.
    Uses topic bridge map — transitions feel natural because topics
    are related in the real engineering workflow.
    """
    bridges = _TOPIC_BRIDGES.get(domain, {}).get(current_topic, [])
    domain_topics = _DOMAIN_TOPICS.get(domain, [])

    # Priority 1: Connected topic that's also a known weakness
    weak_names = {t.topic for t in memory.weak_topics}
    for topic in bridges:
        if topic in weak_names:
            ts = topic_states.get(topic, {})
            if ts.get("turns_spent", 0) < 2:
                return topic

    # Priority 2: Connected topic that's unexplored
    for topic in bridges:
        if topic not in topic_states:
            return topic

    # Priority 3: Connected topic that's least-explored
    for topic in bridges:
        ts = topic_states.get(topic, {})
        if ts.get("turns_spent", 0) < 2:
            return topic

    # Priority 4: Any unexplored topic in domain
    for topic in domain_topics:
        if topic not in topic_states and topic != current_topic:
            return topic

    # Priority 5: Least-explored domain topic
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
    emotional_read: str,
) -> str:
    """
    Determine recommended action. This is a SUGGESTION — the LLM
    may choose differently based on conversational context.
    """
    # Hard transition signal
    if transition_pressure == "overdue":
        return "transition"

    # Early interview — explore
    if turn_number <= 4:
        return "probe"

    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")

    # Emotional overrides — match interviewer to candidate state
    if emotional_read == "honest":
        return "encourage"  # they admitted a gap — be human, then simplify
    if emotional_read == "nervous" and mode in (InterviewerMode.PRESSURE, InterviewerMode.ESCALATING):
        return "ease"  # back off pressure on nervous candidate
    if emotional_read == "defensive":
        return "reframe"  # don't confront, ask from a different angle

    # Soft transition
    if transition_pressure == "recommended":
        return "transition"

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
        "transition": "transition", "encourage": "simplify",
        "ease": "simplify", "reframe": "probe",
    }
    key = key_map.get(action, "probe")
    return voices.get(key, "")


# ── Strategic intent builder ─────────────────────────────────────────────────

def _build_strategic_intent(
    topic: str,
    ts: dict,
    action: str,
    transition_pressure: str,
    transition_target: str | None,
    mode: InterviewerMode,
    memory: CandidateMemory,
    emotional_read: str,
    domain: VLSIDomain,
) -> str:
    """
    Build natural-language strategic briefing.
    Written as soft guidance ("consider", "you might"), not rigid commands.
    """
    turns = ts.get("turns_spent", 0)
    streak = ts.get("streak", 0)
    depth = ts.get("depth_label", "surface")
    gaps = ts.get("gaps", [])
    claims = ts.get("claims", [])

    parts = []

    # Action guidance (soft language)
    if action == "transition":
        if transition_target:
            # Find the bridge between topics
            bridges = _TOPIC_BRIDGES.get(domain, {}).get(topic, [])
            if transition_target in bridges:
                parts.append(f"You've explored {topic} well ({turns} turns). {transition_target} connects naturally here — bridge through what they said.")
            else:
                parts.append(f"Time to shift from {topic}. Move to {transition_target} — connect it to something from their background.")
        else:
            parts.append(f"You've spent enough time on {topic}. Shift to a different area naturally.")

    elif action == "deepen":
        parts.append(f"They're handling {topic} well. Go deeper — tradeoffs, edge cases, what breaks under pressure.")

    elif action == "simplify":
        parts.append(f"They're struggling with {topic}. Simplify — ask from a more basic angle or narrow the scope.")

    elif action == "pressure":
        unresolved = [c for c in memory.contradictions if not c.resolved]
        if unresolved:
            c = unresolved[0]
            parts.append(f'They said "{c.statement_a[:50]}" but also "{c.statement_b[:50]}". Surface this calmly — don\'t accuse.')
        else:
            parts.append(f"Push for specifics on {topic}. Ask for numbers, personal decisions, or what went wrong.")

    elif action == "escalate":
        parts.append(f"Answer was surface-level. Push past the buzzwords — ask for the actual mechanism or their specific contribution.")

    elif action == "encourage":
        parts.append("They admitted a gap honestly. Acknowledge briefly ('That's fair.'), then offer a simpler angle on the same topic or move to something they're more comfortable with.")

    elif action == "ease":
        parts.append("Candidate seems nervous. Back off intensity. Ask something achievable. Let them rebuild confidence before probing again.")

    elif action == "reframe":
        parts.append("Candidate is getting defensive. Don't push harder — ask the same concept from a different angle. Use a scenario instead of a direct question.")

    else:
        parts.append(f"Continue on {topic}. Follow up naturally on what they just said.")

    # Depth awareness (prevents re-asking ground already covered)
    if depth in ("mechanism", "tradeoff") and streak >= 1:
        parts.append(f"You've already covered {depth}-level ground here — don't re-ask basics.")

    # Gaps to probe (when appropriate)
    if gaps and action in ("pressure", "escalate", "deepen"):
        parts.append(f"Known gap: {gaps[0]}.")

    # Claims to test (when appropriate)
    if claims and action in ("pressure", "deepen"):
        parts.append(f'They claimed: "{claims[-1][:60]}" — see if this holds under scrutiny.')

    # Transition consideration (soft signal, not override)
    if transition_pressure == "consider" and action != "transition":
        parts.append(f"You've been on {topic} for {turns} turns — consider moving on soon if you have enough signal.")

    return " ".join(parts)


# ── Candidate portrait ───────────────────────────────────────────────────────

def _build_candidate_portrait(
    memory: CandidateMemory,
    topic_states: dict,
    emotional_read: str,
) -> str:
    """Natural-language summary of candidate's state and trajectory."""
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

    # Emotional context
    if emotional_read in ("nervous", "defensive", "honest"):
        parts.append(f"Current demeanor: {emotional_read}.")

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
