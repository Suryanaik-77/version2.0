"""
candidates.py — Chaos candidate answer library.

Six chaos types, each designed to stress a different system component.
Answers are crafted to be realistic — the kind of chaos a real interviewer
encounters, not artificial edge cases.

Each chaos answer includes:
  - The answer text
  - Which system components it stresses
  - Expected interviewer behavior
  - What a FAILURE looks like
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from app.models.session import VLSIDomain


class ChaosType(str, Enum):
    SELF_CORRECTION        = "self_correction"       # "wait, I mean..."
    INCOMPLETE_REASONING   = "incomplete_reasoning"  # trails off, can't finish
    TOPIC_SWITCH           = "topic_switch"          # jumps topics mid-answer
    OVERCONFIDENT_WRONG    = "overconfident_wrong"   # confident but wrong facts
    EMPTY_AFTER_PAUSE      = "empty_after_pause"     # very short, post-silence answer
    MIXED_CORRECTNESS      = "mixed_correctness"     # right + wrong in same answer


@dataclass
class ChaosAnswer:
    chaos_type: ChaosType
    domain: VLSIDomain
    text: str
    stresses: list[str]              # which components are stressed
    expected_behavior: str           # what interviewer SHOULD do
    failure_behavior: str            # what a BROKEN interviewer does
    technical_errors: list[str]      # any wrong facts for eval validation


# ── Chaos answer library ───────────────────────────────────────────────────────

CHAOS_LIBRARY: list[ChaosAnswer] = [

    # ── Self-correction (memory engine stress) ────────────────────────────────

    ChaosAnswer(
        chaos_type=ChaosType.SELF_CORRECTION,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text=(
            "So I used common centroid for the matching — wait, no. "
            "That was a different project. For this one we actually used "
            "interdigitation with four fingers per device and dummy cells on the ends. "
            "The common centroid thing I was thinking of was the resistor ladder, not the diff pair."
        ),
        stresses=["memory_engine", "question_engine"],
        expected_behavior=(
            "Ask about the interdigitation approach specifically — "
            "why four fingers, how were dummies placed, what mismatch was achieved."
        ),
        failure_behavior=(
            "Creates a contradiction between 'common centroid' and 'interdigitation' "
            "even though the candidate corrected themselves."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.SELF_CORRECTION,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text=(
            "The hold violation — actually I'm getting confused. That wasn't hold, "
            "that was setup. No wait. Setup was the one that cleared after synthesis. "
            "The hold violations were the ones that came up after CTS, and there were "
            "about 23 of them on the boundary clock domain. Those took two weeks."
        ),
        stresses=["memory_engine", "eval_engine"],
        expected_behavior=(
            "Focus on the confirmed fact: 23 hold violations post-CTS, two weeks. "
            "Ask what the root cause was."
        ),
        failure_behavior=(
            "Misclassifies setup/hold confusion as wrong technical knowledge "
            "and penalizes heavily in eval."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.SELF_CORRECTION,
        domain=VLSIDomain.DESIGN_VERIFICATION,
        text=(
            "I built the scoreboard in UVM — well, it was mostly SystemVerilog with "
            "some UVM base classes. Actually we inherited an existing environment and "
            "I extended the scoreboard specifically. The key part I owned was the "
            "AXI completion checker and the FIFO depth monitor."
        ),
        stresses=["memory_engine", "eval_engine"],
        expected_behavior=(
            "Challenge ownership: 'so you extended an existing scoreboard rather than "
            "built from scratch — what specifically did you add?'"
        ),
        failure_behavior=(
            "Stores 'built UVM scoreboard from scratch' as a claim despite "
            "the candidate clarifying they only extended an existing one."
        ),
        technical_errors=[],
    ),

    # ── Incomplete reasoning (question engine stress) ──────────────────────────

    ChaosAnswer(
        chaos_type=ChaosType.INCOMPLETE_REASONING,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text=(
            "The latch-up protection was... we added guard rings. "
            "The reason it worked is because... it basically isolates the substrate. "
            "The mechanism involves the p-well and n-well, and the key thing is... "
            "I mean, it prevents the current from triggering the... the structure."
        ),
        stresses=["question_engine", "eval_engine"],
        expected_behavior=(
            "Ask directly: 'You mentioned the p-well and n-well — "
            "what's the specific parasitic structure that guard rings prevent from triggering?'"
        ),
        failure_behavior=(
            "Accepts the incomplete answer as adequate and moves to a new topic."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.INCOMPLETE_REASONING,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text=(
            "Timing closure was... we ran the tool multiple times. "
            "The ECO flow involved... I know we used Innovus. "
            "The specific violations were setup violations on the... the adder path. "
            "We fixed them by... yeah, upsizing and rerouting basically."
        ),
        stresses=["question_engine", "strategy_engine"],
        expected_behavior=(
            "Probe the mechanism: 'When you say upsizing fixed the setup violation, "
            "what was the actual root cause — was it cell delay or interconnect delay?'"
        ),
        failure_behavior=(
            "Moves to a new topic instead of pressing on what was actually done."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.INCOMPLETE_REASONING,
        domain=VLSIDomain.DESIGN_VERIFICATION,
        text=(
            "Coverage closure was... we ran a lot of simulations. "
            "The uncovered bins were... some functional, some not. "
            "We added constraints to... reach those corner cases. "
            "The coverage metric went from... I think 60-something to over 90."
        ),
        stresses=["question_engine", "eval_engine"],
        expected_behavior=(
            "Ask: 'When you say you added constraints to reach corner cases, "
            "what kind of constraints — were these solve constraints on the randomizer "
            "or directed sequences?'"
        ),
        failure_behavior="Accepts vague 'we added constraints' without probing what kind.",
        technical_errors=[],
    ),

    # ── Topic switching (memory + continuity stress) ──────────────────────────

    ChaosAnswer(
        chaos_type=ChaosType.TOPIC_SWITCH,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text=(
            "Common centroid works by placing devices symmetrically. "
            "Also I wanted to mention our chip had a very tight floorplan constraint "
            "because we were on 28nm with a 2mm x 2mm die. "
            "Back to matching — we used 8-finger interdigitation and got 0.05% mismatch."
        ),
        stresses=["memory_engine", "question_engine"],
        expected_behavior=(
            "Ignore the floorplan tangent. Anchor on the returned topic: "
            "'0.05% mismatch with 8-finger interdigitation — how did you verify that number post-layout?'"
        ),
        failure_behavior=(
            "Asks about floorplan constraints instead of the matching result."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.TOPIC_SWITCH,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text=(
            "The IR drop issue was in the analog supply domain. "
            "Completely unrelated but we also had a massive congestion issue in Q3 2023 "
            "where routing completion dropped to 87%. "
            "Anyway for IR drop we added a decap ring and the voltage drop went from 22mV to 9mV."
        ),
        stresses=["question_engine", "memory_engine"],
        expected_behavior=(
            "Anchor to the IR drop result: 'From 22mV to 9mV — "
            "what was the size of the decap ring relative to the block?'"
        ),
        failure_behavior=(
            "Asks about the congestion issue instead of the stated IR drop fix."
        ),
        technical_errors=[],
    ),

    # ── Overconfident wrong (eval engine + strategy stress) ───────────────────

    ChaosAnswer(
        chaos_type=ChaosType.OVERCONFIDENT_WRONG,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text=(
            "Common centroid prevents mismatch by adding guard rings around "
            "the matched devices. The guard rings shield the devices from substrate "
            "noise and also ensure they see the same temperature. "
            "I've done this on every analog block I've designed — it's straightforward."
        ),
        stresses=["eval_engine", "strategy_engine"],
        expected_behavior=(
            "Challenge immediately: 'Guard rings prevent latch-up, not mismatch — "
            "what does common centroid actually do to reduce systematic mismatch?'"
        ),
        failure_behavior=(
            "Scores the answer highly because it sounds confident and mentions "
            "correct terms (guard rings) in a wrong context."
        ),
        technical_errors=[
            "Guard rings prevent latch-up, NOT mismatch",
            "Common centroid is about gradient cancellation, not shielding",
        ],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.OVERCONFIDENT_WRONG,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text=(
            "Hold violations happen when the clock arrives too late at the capture flop. "
            "You fix them by inserting delay cells on the data path to slow it down. "
            "This is standard practice and the tool handles it automatically in most flows. "
            "Setup violations are when the clock is too fast."
        ),
        stresses=["eval_engine", "strategy_engine"],
        expected_behavior=(
            "Challenge the wrong definition: 'Hold violations occur when the clock "
            "arrives too EARLY, not too late — the data path is too fast. "
            "Can you re-explain the timing relationship?'"
        ),
        failure_behavior=(
            "Misses the wrong definition because the overall answer sounds competent "
            "and mentions correct terms (delay cells, setup vs hold)."
        ),
        technical_errors=[
            "Hold violation: clock arrives too EARLY (not too late)",
            "Hold fix: add delay on DATA path (correct) but wrong root cause stated",
        ],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.OVERCONFIDENT_WRONG,
        domain=VLSIDomain.DESIGN_VERIFICATION,
        text=(
            "UVM sequences are responsible for checking the DUT output against "
            "the expected values. The sequence contains the prediction logic "
            "and the actual comparison. I've built complete UVM sequences that "
            "handle end-to-end checking including protocol compliance."
        ),
        stresses=["eval_engine"],
        expected_behavior=(
            "Challenge: 'That's actually the scoreboard's responsibility — "
            "what does a sequence actually do in UVM?'"
        ),
        failure_behavior=(
            "Accepts the confident wrong answer about sequence responsibility."
        ),
        technical_errors=[
            "Sequences drive stimulus, scoreboards check output — conflated here",
        ],
    ),

    # ── Empty after pause (voice pipeline + question engine stress) ───────────

    ChaosAnswer(
        chaos_type=ChaosType.EMPTY_AFTER_PAUSE,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text="I... I'm not sure about that specific part.",
        stresses=["question_engine", "strategy_engine"],
        expected_behavior=(
            "Simplify framing without giving answer: "
            "'Let me approach it differently — when you place two matched transistors "
            "in a layout, what determines how similar their threshold voltages are?'"
        ),
        failure_behavior=(
            "Accepts silence/confusion and moves to a completely new topic."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.EMPTY_AFTER_PAUSE,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text="Hmm. I know what timing closure is but I'm drawing a blank on the specifics.",
        stresses=["question_engine", "strategy_engine"],
        expected_behavior=(
            "Narrow the question to something concrete: "
            "'Walk me through what you look at first when you open a timing report "
            "with setup violations.'"
        ),
        failure_behavior=(
            "Transitions to a new topic as if the question was fully answered."
        ),
        technical_errors=[],
    ),

    # ── Mixed correctness (eval + strategy stress) ────────────────────────────

    ChaosAnswer(
        chaos_type=ChaosType.MIXED_CORRECTNESS,
        domain=VLSIDomain.ANALOG_LAYOUT,
        text=(
            "Common centroid places matched devices so each one sees the same "
            "average gradient — that part I'm solid on. The implementation details "
            "I'm less certain about. I think you use interdigitated fingers but "
            "I'm not 100% sure if dummy cells are strictly required or just good practice."
        ),
        stresses=["eval_engine", "strategy_engine"],
        expected_behavior=(
            "Credit the correct mechanism (gradient cancellation), probe the uncertainty: "
            "'Dummy cells — what problem does having an incomplete row at the array "
            "boundary actually create?'"
        ),
        failure_behavior=(
            "Either scores the whole answer high (accepts uncertainty as honesty) "
            "or scores it low (penalizes uncertainty as weakness) without addressing "
            "the specific gap."
        ),
        technical_errors=[],
    ),

    ChaosAnswer(
        chaos_type=ChaosType.MIXED_CORRECTNESS,
        domain=VLSIDomain.PHYSICAL_DESIGN,
        text=(
            "Setup violations are when there isn't enough time for data to propagate "
            "and be captured — that I know for certain. For hold, I always mix up "
            "the exact condition. I think it's when data changes too soon after the clock "
            "edge? Or is it before? I know you fix it with delay buffers."
        ),
        stresses=["eval_engine", "strategy_engine"],
        expected_behavior=(
            "Note the correct setup definition, probe the hold confusion directly: "
            "'Hold violation — the data path is faster than it should be. "
            "Given that, why would adding delay buffers on the data path fix it?'"
        ),
        failure_behavior=(
            "Accepts 'I always mix it up' without probing and moves on."
        ),
        technical_errors=[
            "Hold: data changes too SOON (before clock edge captures it) — "
            "candidate has the direction confused but fix is correct"
        ],
    ),
]


def get_chaos_answers(domain: VLSIDomain | None = None) -> list[ChaosAnswer]:
    """Return chaos answers, optionally filtered by domain."""
    if domain is None:
        return CHAOS_LIBRARY
    return [a for a in CHAOS_LIBRARY if a.domain == domain]


def get_chaos_by_type(chaos_type: ChaosType) -> list[ChaosAnswer]:
    return [a for a in CHAOS_LIBRARY if a.chaos_type == chaos_type]
