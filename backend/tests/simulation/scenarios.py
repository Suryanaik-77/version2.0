"""
scenarios.py — Deterministic behavioral test cases.

Each scenario tests ONE specific interviewer behavior with a known input.
These are faster and cheaper than full simulations (no candidate LLM needed).

Complements full simulation: scenarios give reproducible CI-safe tests,
simulation gives exploratory coverage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from app.models.session import InterviewerMode, VLSIDomain


@dataclass
class Scenario:
    """
    A single behavioral test case.

    setup_answers: list of prior answers (to build memory context)
    test_answer:   the answer the interviewer should respond to
    expected_behaviors: list of behaviors the question MUST exhibit
    forbidden_behaviors: list of behaviors the question MUST NOT exhibit
    description: human-readable test name
    """
    name: str
    domain: VLSIDomain
    mode: InterviewerMode
    setup_answers: list[str]       # prior answers (to seed memory)
    test_answer: str                # the answer to respond to
    expected_behaviors: list[str]  # must be present in question
    forbidden_behaviors: list[str] # must NOT be present
    description: str
    memory_context: str = ""


SCENARIOS: list[Scenario] = [

    # ── Mechanism probing ─────────────────────────────────────────────────────

    Scenario(
        name="mechanism_probe_on_shallow",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        setup_answers=[],
        test_answer="I used common centroid for matching.",
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase", "praise"],
        description="Shallow answer should trigger mechanism question",
    ),

    Scenario(
        name="mechanism_probe_on_buzzword_only",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.ESCALATING,
        setup_answers=["We used matching techniques in the layout."],
        test_answer="We used interdigitation and dummy cells to minimize parasitics.",
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase", "praise"],
        description="Buzzword-only answer should trigger mechanism probe",
    ),

    Scenario(
        name="no_mechanism_probe_on_strong",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        setup_answers=[],
        test_answer=(
            "I used 4-finger common centroid with dummy cells on both ends. "
            "The key is that systematic gradients — both X and Y — cancel because "
            "each device sees the same gradient average. In our 28nm OTA we measured "
            "0.08% mismatch versus 0.6% without it."
        ),
        expected_behaviors=["topic_continuity"],
        forbidden_behaviors=["ai_phrase"],
        description="Strong mechanism answer should NOT just ask for mechanism again",
    ),

    # ── Ownership verification ────────────────────────────────────────────────

    Scenario(
        name="ownership_challenge",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        setup_answers=[],
        test_answer="I designed a 28nm analog front-end with matching-critical devices.",
        expected_behaviors=["topic_continuity"],
        forbidden_behaviors=["ai_phrase"],
        description="Ownership claim should be challenged with specifics",
    ),

    Scenario(
        name="memorized_ownership_rejection",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        setup_answers=[],
        test_answer=(
            "Common centroid is a technique used to match devices by placing them "
            "symmetrically around a common center point to cancel gradient effects."
        ),
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase", "praise"],
        description="Memorized textbook answer should be probed, not accepted",
    ),

    # ── Contradiction surface ─────────────────────────────────────────────────

    Scenario(
        name="contradiction_must_be_surfaced",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        setup_answers=[
            "We always use common centroid for matched pairs — it's mandatory in our flow.",
        ],
        test_answer=(
            "For that analog block we just used regular placement. "
            "Common centroid adds too much area overhead."
        ),
        expected_behaviors=["contradiction_surface"],
        forbidden_behaviors=["ai_phrase"],
        description="Direct contradiction of prior statement must be surfaced",
        memory_context='CANDIDATE CLAIMED: "We always use common centroid for matched pairs"',
    ),

    Scenario(
        name="timing_contradiction",
        domain=VLSIDomain.PHYSICAL_DESIGN,
        mode=InterviewerMode.PROBING,
        setup_answers=[
            "Timing closure was not an issue on our chip — we had plenty of margin.",
        ],
        test_answer=(
            "We spent about 3 months on timing closure actually. "
            "The critical path kept failing after CTS."
        ),
        expected_behaviors=["contradiction_surface"],
        forbidden_behaviors=["ai_phrase"],
        description="Timing claim contradiction must be surfaced",
        memory_context='CANDIDATE CLAIMED: "Timing closure was not an issue on our chip"',
    ),

    # ── Pressure escalation ───────────────────────────────────────────────────

    Scenario(
        name="pressure_on_mastered_topic",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PRESSURE,
        setup_answers=[],
        test_answer=(
            "I implemented full 4-way interdigitated common centroid with well-matched "
            "aspect ratios, dummy cells on all four sides, and verified sub-0.1% "
            "mismatch in post-layout simulation."
        ),
        expected_behaviors=["pressure"],
        forbidden_behaviors=["ai_phrase", "praise"],
        description="Strong PRESSURE mode answer should trigger edge case probe",
    ),

    Scenario(
        name="edge_case_after_strong",
        domain=VLSIDomain.PHYSICAL_DESIGN,
        mode=InterviewerMode.DEEPENING,
        setup_answers=[],
        test_answer=(
            "I debugged a hold violation after CTS — traced it to clock skew between "
            "two clock domains. Fixed by rebalancing the buffers and inserting hold cells "
            "on the 23 failing paths. Violation was 15-50ps, resolved to zero slack."
        ),
        expected_behaviors=["pressure", "topic_continuity"],
        forbidden_behaviors=["ai_phrase"],
        description="Detailed strong answer should trigger edge case exploration",
    ),

    # ── Vague acceptance prevention ───────────────────────────────────────────

    Scenario(
        name="vague_acceptance_rejected",
        domain=VLSIDomain.PHYSICAL_DESIGN,
        mode=InterviewerMode.PROBING,
        setup_answers=[],
        test_answer="We optimized the timing closure using ECO flows and various techniques.",
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase", "praise"],
        description="Vague 'techniques' answer must trigger probe, not acceptance",
    ),

    Scenario(
        name="it_depends_probe",
        domain=VLSIDomain.DESIGN_VERIFICATION,
        mode=InterviewerMode.ESCALATING,
        setup_answers=[],
        test_answer="It depends on the design requirements and the coverage model you're using.",
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase"],
        description="'It depends' without explanation must trigger probe",
    ),

    # ── Topic continuity ──────────────────────────────────────────────────────

    Scenario(
        name="follow_up_references_prior",
        domain=VLSIDomain.DESIGN_VERIFICATION,
        mode=InterviewerMode.DEEPENING,
        setup_answers=[],
        test_answer=(
            "I built a UVM scoreboard that checked AXI completions. "
            "It had separate queues for read and write channels with a FIFO order checker."
        ),
        expected_behaviors=["topic_continuity"],
        forbidden_behaviors=["ai_phrase", "disconnected_question"],
        description="Follow-up must reference AXI/scoreboard/UVM from the answer",
    ),

    # ── Debugging probe ───────────────────────────────────────────────────────

    Scenario(
        name="debug_scenario_injection",
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.ESCALATING,
        setup_answers=[],
        test_answer="I know how to do latch-up analysis and add guard rings.",
        expected_behaviors=["mechanism_probe"],
        forbidden_behaviors=["ai_phrase"],
        description="Abstract knowledge claim should trigger practical debug scenario",
    ),
]
