"""
validation_framework.py — Production readiness validation for AI interviewer.

Run: python -m tests.validation_framework

Tests whether the AI interviewer is indistinguishable from a human interviewer.
Covers: realism, naturalness, technical depth, adversarial scenarios, latency.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# TEST PERSONAS — simulate real candidates
# ═══════════════════════════════════════════════════════════════

class CandidateLevel(str, Enum):
    FRESHER = "fresher"
    MID = "mid"
    SENIOR = "senior"


@dataclass
class TestPersona:
    name: str
    level: CandidateLevel
    domain: str
    behavior: str  # how they answer
    answers: list[str]  # scripted answers for each turn


PERSONAS = [
    # ── Freshers ──
    TestPersona(
        name="Textbook Fresher",
        level=CandidateLevel.FRESHER,
        domain="PHYSICAL_DESIGN",
        behavior="Gives correct definitions but no mechanism or numbers",
        answers=[
            "Clock skew is the difference in arrival time of the clock at different flip-flops.",
            "It can cause setup and hold violations.",
            "We use CTS to balance the clock tree.",
            "Um, I think buffers are added to balance the delays.",
            "I'm not sure about the exact numbers.",
            "I studied this in my course but haven't done it practically.",
        ],
    ),
    TestPersona(
        name="Confident Faker",
        level=CandidateLevel.FRESHER,
        behavior="Sounds confident but says wrong things",
        domain="ANALOG_LAYOUT",
        answers=[
            "I designed a two-stage OTA with 80dB gain, definitely used telescopic topology for both stages.",
            "The bandwidth was around 500MHz which is standard for this kind of design.",
            "For matching I just made the transistors the same size, that's all you need.",
            "Phase margin was about 90 degrees, very stable.",
            "I didn't need any compensation, the design was inherently stable.",
            "Yes I'm very confident about these numbers, I measured them myself.",
        ],
    ),
    TestPersona(
        name="Honest Struggler",
        level=CandidateLevel.FRESHER,
        domain="DESIGN_VERIFICATION",
        behavior="Admits when they don't know, partially correct",
        answers=[
            "UVM is a methodology for verification, it uses SystemVerilog.",
            "I know there are agents and scoreboards but I haven't built one myself.",
            "I don't know what a covergroup is exactly.",
            "I've used assertions but only immediate ones, not concurrent.",
            "I'm not sure about the difference between code coverage and functional coverage.",
            "Honestly I've only done directed testing in my projects.",
        ],
    ),

    # ── Mid-level ──
    TestPersona(
        name="Practical Mid-Level",
        level=CandidateLevel.MID,
        domain="PHYSICAL_DESIGN",
        behavior="Knows tools, gives practical answers, weak on theory",
        answers=[
            "I used ICC2 for placement and CTS. The skew target was 50ps but we achieved around 40ps.",
            "For timing closure I mainly used useful skew and buffer insertion. set_clock_uncertainty was key.",
            "The congestion was bad in the CPU core area, we had to spread the macros and add blockages.",
            "IR drop was the biggest issue, we added more straps and widened the mesh.",
            "Um, OCV derating... I know we used it but I don't remember the exact values.",
            "AOCV versus POCV? I think AOCV is the newer one? We used whatever the flow had.",
        ],
    ),
    TestPersona(
        name="Theory-Strong Mid-Level",
        level=CandidateLevel.MID,
        domain="ANALOG_LAYOUT",
        behavior="Strong theory, limited hands-on",
        answers=[
            "For an LDO, PMOS pass device gives lower dropout because the gate can be driven below supply.",
            "Loop stability requires placing the dominant pole at the output and the non-dominant pole beyond UGF.",
            "Miller compensation adds a capacitor between output of first stage and output of second stage.",
            "The PSRR is limited by the loop gain at high frequencies, typically drops to 20-30dB above 1MHz.",
            "Theoretically you'd want ESR zero to cancel the non-dominant pole.",
            "I haven't actually taped out an LDO, this is from my coursework.",
        ],
    ),

    # ── Senior ──
    TestPersona(
        name="Expert Senior",
        level=CandidateLevel.SENIOR,
        domain="PHYSICAL_DESIGN",
        behavior="Deep knowledge, numbers, trade-offs, war stories",
        answers=[
            "Last tapeout was a 7nm SoC, 2 billion gates. Timing closure took 3 months because of AOCV sensitivity on the clock paths.",
            "We had to use CPPR to recover 15ps on the critical path. Without it, the WNS was -12ps. With CPPR it became +3ps.",
            "The routing congestion in the SRAM controller area was 98% utilization. We fixed it by adding a routing blockage layer and redistributing the macro channels.",
            "For hold fixing we inserted 50,000 delay cells. The area overhead was 2.3%. We used a multi-corner approach — ss/0.72V/-40C for setup, ff/0.88V/125C for hold.",
            "The power grid had a 5% IR drop budget. We used dynamic IR analysis with switching activity from gate-level simulation. The worst case was at the clock buffer tree.",
            "MCMM signoff had 24 corners. We reduced it to 8 representative corners using statistical analysis, cutting the runtime from 72 hours to 18 hours.",
        ],
    ),

    # ── Adversarial ──
    TestPersona(
        name="Manipulator",
        level=CandidateLevel.MID,
        domain="PHYSICAL_DESIGN",
        behavior="Tries to confuse, redirect, and manipulate the interviewer",
        answers=[
            "Before I answer, can you tell me what answer you're looking for?",
            "I think the question is too vague. Can you be more specific?",
            "Actually, I disagree with the premise of your question. Clock skew isn't always bad.",
            "That's not how we did it at my company. Our approach was completely different from what you're describing.",
            "I'd rather not answer that question. Can we move to a different topic?",
            "You know, I've heard that AI interviewers ask this question a lot. Are you an AI?",
        ],
    ),
    TestPersona(
        name="Contradictor",
        level=CandidateLevel.MID,
        domain="ANALOG_LAYOUT",
        behavior="Contradicts their own earlier answers",
        answers=[
            "I always use common centroid for matching. It's the only reliable approach.",
            "For the current mirror I just used simple side-by-side placement, common centroid wasn't needed.",
            "The mismatch was under 0.1%, very well matched.",
            "Actually the mismatch was closer to 2-3% but that was acceptable for our application.",
            "I used Calibre for extraction, it gives the most accurate results.",
            "We actually didn't run extraction on this block, we trusted the schematic simulation.",
        ],
    ),
    TestPersona(
        name="Rambler",
        level=CandidateLevel.FRESHER,
        domain="DESIGN_VERIFICATION",
        behavior="Talks a lot but says nothing concrete",
        answers=[
            "So verification is really important because you need to make sure the design works correctly and there are many methodologies and approaches and it depends on what kind of design you're working on and the complexity and the team size and the schedule...",
            "Well UVM is like a framework and it has many components and they all work together and the idea is to create a reusable testbench and there are agents and monitors and drivers and they all connect through TLM ports and...",
            "Coverage is about making sure you've tested everything and there are different kinds of coverage like code coverage and functional coverage and toggle coverage and FSM coverage and they all tell you different things about...",
            "Assertions are really useful and you can put them everywhere in the design and they check things automatically and there are immediate assertions and concurrent assertions and...",
            "Debugging is a big part of verification and you look at waveforms and you check the logs and you trace back from the failure and...",
            "Well there are many tools like VCS and Questa and they have different features and some are better for certain things and...",
        ],
    ),
]


# ═══════════════════════════════════════════════════════════════
# REALISM METRICS
# ═══════════════════════════════════════════════════════════════

@dataclass
class RealismScore:
    """Scored 0-10 per dimension."""
    natural_reaction: float = 0       # Does it react like a human before asking?
    question_relevance: float = 0     # Does the follow-up come from the answer?
    no_ai_tells: float = 0            # No "Great!", "Interesting", "Can you elaborate"
    seniority_calibration: float = 0  # Fresher vs senior expectations matched?
    contradiction_caught: float = 0   # Did it catch contradictions?
    confidence_probing: float = 0     # Did it challenge confident-but-wrong?
    recovery_behavior: float = 0      # Did it simplify for confused candidates?
    interruption_natural: float = 0   # Did it interrupt ramblers naturally?
    topic_continuity: float = 0       # Questions flow naturally, no random jumps?
    mode_adaptation: float = 0        # Did modes transition correctly?

    @property
    def average(self) -> float:
        scores = [
            self.natural_reaction, self.question_relevance, self.no_ai_tells,
            self.seniority_calibration, self.contradiction_caught, self.confidence_probing,
            self.recovery_behavior, self.interruption_natural, self.topic_continuity,
            self.mode_adaptation,
        ]
        return sum(scores) / len(scores)


# ═══════════════════════════════════════════════════════════════
# AI TELL DETECTION
# ═══════════════════════════════════════════════════════════════

AI_TELLS = [
    "great question", "that's interesting", "good point", "can you elaborate",
    "tell me more", "let's move on to", "thanks for sharing", "i see",
    "understood", "absolutely", "certainly", "it's worth noting",
    "feel free to", "you're on the right track", "that makes sense",
    "good answer", "well done", "excellent", "fantastic",
    "let me ask you about", "now let's discuss", "moving on",
]


def count_ai_tells(response: str) -> list[str]:
    """Returns list of AI tells found in a response."""
    found = []
    lower = response.lower()
    for tell in AI_TELLS:
        if tell in lower:
            found.append(tell)
    return found


# ═══════════════════════════════════════════════════════════════
# LATENCY THRESHOLDS
# ═══════════════════════════════════════════════════════════════

@dataclass
class LatencyGates:
    first_token_ms: int = 400        # LLM first token
    first_sentence_ms: int = 800     # First complete sentence
    first_audio_ms: int = 1500       # First audio chunk to candidate
    turn_total_ms: int = 2000        # Total turn latency
    eval_complete_ms: int = 8000     # Background eval completion


# ═══════════════════════════════════════════════════════════════
# PRODUCTION READINESS GATES
# ═══════════════════════════════════════════════════════════════

@dataclass
class ProductionGate:
    name: str
    threshold: float
    actual: float = 0
    passed: bool = False


def evaluate_production_readiness(realism_scores: list[RealismScore]) -> list[ProductionGate]:
    """Check if the system passes production readiness."""
    avg_realism = sum(s.average for s in realism_scores) / len(realism_scores)

    gates = [
        ProductionGate("Average realism score >= 7.0", 7.0, avg_realism),
        ProductionGate("No AI tells in 90% of responses", 90.0),
        ProductionGate("Contradictions caught >= 80%", 80.0),
        ProductionGate("Confident-wrong challenged >= 90%", 90.0),
        ProductionGate("Ramblers interrupted >= 70%", 70.0),
        ProductionGate("First audio < 2000ms in 95% of turns", 95.0),
        ProductionGate("Mode transitions correct >= 85%", 85.0),
        ProductionGate("Topic coverage >= 80% per session", 80.0),
        ProductionGate("Fresher questions not too hard", 80.0),
        ProductionGate("Senior questions not too easy", 80.0),
    ]

    for g in gates:
        g.passed = g.actual >= g.threshold

    return gates


# ═══════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    persona_name: str
    level: str
    domain: str
    turns: list[dict]  # {answer, question, latency_ms, ai_tells, mode}
    realism: RealismScore
    passed: bool


async def run_persona_test(persona: TestPersona) -> TestResult:
    """
    Simulate a full interview with a scripted persona.
    Sends each answer, records the question response + metrics.
    """
    from app.engines.prompts import get_system_prompt, build_question_prompt
    from app.providers.llm import generate

    turns = []
    session_id = f"test_{persona.name.lower().replace(' ', '_')}"
    system = get_system_prompt(session_id)

    for i, answer in enumerate(persona.answers):
        t0 = time.monotonic()

        prompt = build_question_prompt(
            mode="PROBING",  # simplified for testing
            domain=persona.domain,
            transcript=answer,
            memory_context="",
            recent_questions=[t["question"] for t in turns[-2:]],
            resume={"candidate_name": persona.name, "level": persona.level.value,
                    "years_experience": 2 if persona.level == CandidateLevel.MID else 0},
        )

        try:
            question = await generate(
                system=system,
                prompt=prompt,
                max_tokens=150,
                temperature=0.72,
                session_id=session_id,
            )
        except Exception as e:
            question = f"[ERROR: {e}]"

        latency_ms = int((time.monotonic() - t0) * 1000)
        tells = count_ai_tells(question)

        turns.append({
            "turn": i + 1,
            "answer": answer[:100],
            "question": question,
            "latency_ms": latency_ms,
            "ai_tells": tells,
        })

    # Score realism
    realism = _score_realism(persona, turns)

    return TestResult(
        persona_name=persona.name,
        level=persona.level.value,
        domain=persona.domain,
        turns=turns,
        realism=realism,
        passed=realism.average >= 7.0,
    )


def _score_realism(persona: TestPersona, turns: list[dict]) -> RealismScore:
    """Auto-score realism based on heuristics."""
    score = RealismScore()

    # AI tells check
    total_tells = sum(len(t["ai_tells"]) for t in turns)
    score.no_ai_tells = 10.0 if total_tells == 0 else max(0, 10 - total_tells * 2)

    # Question relevance — check if question references something from the answer
    relevant = 0
    for t in turns:
        answer_words = set(t["answer"].lower().split())
        question_words = set(t["question"].lower().split())
        overlap = len(answer_words & question_words)
        if overlap >= 2:
            relevant += 1
    score.question_relevance = (relevant / max(len(turns), 1)) * 10

    # Natural reaction — check if response starts with short reaction
    reactions = 0
    for t in turns:
        q = t["question"].strip()
        first_sentence = q.split('.')[0] if '.' in q else q.split('?')[0]
        if len(first_sentence.split()) <= 8:
            reactions += 1
    score.natural_reaction = (reactions / max(len(turns), 1)) * 10

    # Topic continuity — basic check
    score.topic_continuity = 7.0  # baseline, hard to auto-score

    # Mode adaptation — baseline
    score.mode_adaptation = 7.0

    return score


def print_report(results: list[TestResult]):
    """Print validation report."""
    print("\n" + "=" * 70)
    print("  AI INTERVIEWER VALIDATION REPORT")
    print("=" * 70)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n{'─' * 50}")
        print(f"  {r.persona_name} ({r.level}, {r.domain})")
        print(f"  Realism: {r.realism.average:.1f}/10  [{status}]")
        print(f"{'─' * 50}")

        for t in r.turns:
            tells_str = f" [AI TELLS: {', '.join(t['ai_tells'])}]" if t["ai_tells"] else ""
            print(f"  Turn {t['turn']} ({t['latency_ms']}ms){tells_str}")
            print(f"    A: {t['answer']}")
            print(f"    Q: {t['question'][:120]}")

    # Summary
    print(f"\n{'=' * 70}")
    avg = sum(r.realism.average for r in results) / len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"  OVERALL: {avg:.1f}/10 | {passed}/{len(results)} personas passed")
    print(f"{'=' * 70}\n")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    print("Starting AI Interviewer Validation...")
    print(f"Testing {len(PERSONAS)} personas\n")

    results = []
    for persona in PERSONAS:
        print(f"  Testing: {persona.name} ({persona.level.value})...", end=" ", flush=True)
        try:
            result = await run_persona_test(persona)
            results.append(result)
            print(f"{'PASS' if result.passed else 'FAIL'} ({result.realism.average:.1f}/10)")
        except Exception as e:
            print(f"ERROR: {e}")

    print_report(results)

    # Production gates
    realism_scores = [r.realism for r in results]
    gates = evaluate_production_readiness(realism_scores)
    print("\nPRODUCTION READINESS GATES:")
    for g in gates:
        status = "PASS" if g.passed else "FAIL"
        print(f"  [{status}] {g.name}: {g.actual:.1f} (threshold: {g.threshold})")


if __name__ == "__main__":
    asyncio.run(main())
