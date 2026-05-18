"""
chaos_generator.py — Generates chaotic candidate transcripts for stability testing.

Each chaos type targets a specific failure mode in the system.
Transcripts are realistic VLSI engineering speech — not random noise.

Chaos types:
  SELF_CORRECTION      — candidate corrects themselves mid-answer
  INCOMPLETE_REASONING — trailing off, incomplete technical chains
  TOPIC_SWITCH         — subject changes mid-sentence
  OVERCONFIDENT_WRONG  — confident but factually incorrect statements
  MIXED_CORRECTNESS    — partially correct with embedded errors
  LONG_PAUSE           — near-empty transcript (simulated silence/hesitation)
  CONTRADICTION_CHAIN  — sequential statements that contradict each other
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from app.models.session import VLSIDomain


class ChaosType(str, Enum):
    SELF_CORRECTION      = "self_correction"
    INCOMPLETE_REASONING = "incomplete_reasoning"
    TOPIC_SWITCH         = "topic_switch"
    OVERCONFIDENT_WRONG  = "overconfident_wrong"
    MIXED_CORRECTNESS    = "mixed_correctness"
    LONG_PAUSE           = "long_pause"
    CONTRADICTION_CHAIN  = "contradiction_chain"


@dataclass
class ChaosTurn:
    chaos_type: ChaosType
    transcript: str
    domain: VLSIDomain
    turn_in_sequence: int          # which turn this is in a multi-turn chain
    expected_memory_claims: list[str]  # what memory should store after this turn
    should_detect_contradiction: bool = False
    should_detect_memorization: bool = False
    expected_min_words: int = 5    # minimum words for non-empty check


# ── Corpus per domain ─────────────────────────────────────────────────────────

_ANALOG_CHAOS = {
    ChaosType.SELF_CORRECTION: [
        ChaosTurn(
            chaos_type=ChaosType.SELF_CORRECTION,
            transcript=(
                "The matching was achieved through common centroid — actually wait, "
                "I need to correct that. We used interdigitation, not common centroid. "
                "The common centroid was on a different block. For this OTA we used "
                "4-finger interdigitation with dummy cells on both sides."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["4-finger interdigitation"],
        ),
        ChaosTurn(
            chaos_type=ChaosType.SELF_CORRECTION,
            transcript=(
                "The parasitic capacitance was 8fF — no wait, that was the total node "
                "capacitance. The parasitic from routing was more like 3fF. "
                "I had to re-route on M4 to achieve that. The original M2 route "
                "was giving 6fF which was too high."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=2,
            expected_memory_claims=["3fF", "M4", "6fF"],
        ),
    ],
    ChaosType.INCOMPLETE_REASONING: [
        ChaosTurn(
            chaos_type=ChaosType.INCOMPLETE_REASONING,
            transcript=(
                "The latch-up happened because the substrate resistance was... "
                "so basically the lateral pnp and the vertical npn were forming a... "
                "and then when the current exceeded a certain threshold the... "
                "anyway we fixed it by adding guard rings around the NMOS devices."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["guard rings", "NMOS"],
        ),
        ChaosTurn(
            chaos_type=ChaosType.INCOMPLETE_REASONING,
            transcript=(
                "The ESD protection was... we used an RC-triggered clamp but the "
                "problem was the RC time constant was... I think around 500ps? "
                "The triggering was inconsistent so we had to... yeah we redesigned "
                "the clamp sizing."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=2,
            expected_memory_claims=["RC-triggered clamp", "500ps"],
        ),
    ],
    ChaosType.TOPIC_SWITCH: [
        ChaosTurn(
            chaos_type=ChaosType.TOPIC_SWITCH,
            transcript=(
                "The matching was achieved through common centroid and — actually "
                "speaking of analog, I also worked on a BGR circuit where we had to "
                "deal with temperature coefficient matching, but for this specific OTA "
                "the matching was common centroid with aspect ratio constraints."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["common centroid", "BGR"],
        ),
    ],
    ChaosType.OVERCONFIDENT_WRONG: [
        ChaosTurn(
            chaos_type=ChaosType.OVERCONFIDENT_WRONG,
            transcript=(
                "Common centroid eliminates mismatch completely by ensuring all devices "
                "have exactly the same threshold voltage. When devices share a common "
                "center, their Vt is identical because Vt is a function of position. "
                "This is the fundamental principle — you get perfect matching."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["common centroid"],
            should_detect_memorization=True,
        ),
        ChaosTurn(
            chaos_type=ChaosType.OVERCONFIDENT_WRONG,
            transcript=(
                "Guard rings are only needed for digital circuits. In analog design "
                "latch-up is never really an issue because the current levels are too "
                "low to trigger the parasitic thyristor. I've never added a guard ring "
                "to an analog block in my 6 years of experience."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=2,
            expected_memory_claims=["guard rings", "6 years"],
        ),
    ],
    ChaosType.MIXED_CORRECTNESS: [
        ChaosTurn(
            chaos_type=ChaosType.MIXED_CORRECTNESS,
            transcript=(
                "The parasitic cap from M2 routing was 8fF which I reduced to 3fF by "
                "rerouting on M4. The threshold voltage shift was also a factor — "
                "actually wait, Vt shift is temperature sensitivity not routing parasitic. "
                "The main fix was definitely the layer change. Final post-layout sim "
                "showed 2.8fF which was within spec."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["8fF", "3fF", "M4", "2.8fF"],
        ),
    ],
    ChaosType.LONG_PAUSE: [
        ChaosTurn(
            chaos_type=ChaosType.LONG_PAUSE,
            transcript="I... hm. Common centroid. That's it.",
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=[],
            should_detect_memorization=True,
            expected_min_words=3,
        ),
        ChaosTurn(
            chaos_type=ChaosType.LONG_PAUSE,
            transcript="",   # complete silence
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=2,
            expected_memory_claims=[],
            expected_min_words=0,
        ),
    ],
    ChaosType.CONTRADICTION_CHAIN: [
        ChaosTurn(
            chaos_type=ChaosType.CONTRADICTION_CHAIN,
            transcript=(
                "We always use common centroid for every matched pair — "
                "it's a strict rule in our design guidelines. No exceptions."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=1,
            expected_memory_claims=["common centroid", "always use"],
            should_detect_contradiction=False,  # first statement, no prior to contradict
        ),
        ChaosTurn(
            chaos_type=ChaosType.CONTRADICTION_CHAIN,
            transcript=(
                "For the resistor divider in that block we didn't bother with "
                "common centroid — it's overkill for a divider, and the area "
                "penalty wasn't justified. We just used regular placement."
            ),
            domain=VLSIDomain.ANALOG_LAYOUT,
            turn_in_sequence=2,
            expected_memory_claims=["resistor divider", "regular placement"],
            should_detect_contradiction=True,  # contradicts turn 1
        ),
    ],
}

_PHYSICAL_DESIGN_CHAOS = {
    ChaosType.SELF_CORRECTION: [
        ChaosTurn(
            chaos_type=ChaosType.SELF_CORRECTION,
            transcript=(
                "The timing violation was 50ps — no, sorry, that was the slack not "
                "the violation. The violation was 120ps on the critical path. "
                "I resolved it through a combination of upsizing the driver and "
                "running an incremental synthesis pass targeting that path."
            ),
            domain=VLSIDomain.PHYSICAL_DESIGN,
            turn_in_sequence=1,
            expected_memory_claims=["120ps", "critical path", "upsizing"],
        ),
    ],
    ChaosType.OVERCONFIDENT_WRONG: [
        ChaosTurn(
            chaos_type=ChaosType.OVERCONFIDENT_WRONG,
            transcript=(
                "IR drop doesn't really affect timing in well-designed chips. "
                "If you size your power mesh correctly at the start, you'll never "
                "have IR drop issues. The tool handles it automatically in the "
                "floorplan stage. I never needed to debug IR drop in my career."
            ),
            domain=VLSIDomain.PHYSICAL_DESIGN,
            turn_in_sequence=1,
            expected_memory_claims=["IR drop", "power mesh"],
        ),
    ],
}

_DV_CHAOS = {
    ChaosType.SELF_CORRECTION: [
        ChaosTurn(
            chaos_type=ChaosType.SELF_CORRECTION,
            transcript=(
                "The scoreboard had 3 queues — actually 2. One for read completions "
                "and one for write completions. I had a third queue initially but "
                "removed it after we realized the protocol didn't need it."
            ),
            domain=VLSIDomain.DESIGN_VERIFICATION,
            turn_in_sequence=1,
            expected_memory_claims=["scoreboard", "read completions", "write completions"],
        ),
    ],
    ChaosType.MIXED_CORRECTNESS: [
        ChaosTurn(
            chaos_type=ChaosType.MIXED_CORRECTNESS,
            transcript=(
                "Code coverage and functional coverage are different — code coverage "
                "checks if every line ran, functional coverage checks if every design "
                "intent was exercised. We targeted 100% toggle coverage — wait, "
                "toggle is a subset of code coverage not separate. Anyway we hit "
                "95% functional and 98% code coverage."
            ),
            domain=VLSIDomain.DESIGN_VERIFICATION,
            turn_in_sequence=1,
            expected_memory_claims=["95%", "98%", "functional coverage"],
        ),
    ],
}


def get_chaos_corpus(domain: VLSIDomain) -> dict[ChaosType, list[ChaosTurn]]:
    """Returns all chaos turns for a domain."""
    if domain == VLSIDomain.ANALOG_LAYOUT:
        return _ANALOG_CHAOS
    elif domain == VLSIDomain.PHYSICAL_DESIGN:
        return _PHYSICAL_DESIGN_CHAOS
    else:
        return _DV_CHAOS


def get_all_chaos_turns(domain: VLSIDomain = VLSIDomain.ANALOG_LAYOUT) -> list[ChaosTurn]:
    """Flat list of all chaos turns for a domain."""
    corpus = get_chaos_corpus(domain)
    turns = []
    for chaos_turns in corpus.values():
        turns.extend(chaos_turns)
    return turns


def build_contradiction_sequence(domain: VLSIDomain = VLSIDomain.ANALOG_LAYOUT) -> list[ChaosTurn]:
    """Returns the contradiction chain sequence (must be run in order)."""
    corpus = get_chaos_corpus(domain)
    return corpus.get(ChaosType.CONTRADICTION_CHAIN, [])
