"""
profiles.py — Synthetic candidate profiles for simulation.

Each profile defines:
  - How the candidate answers questions (LLM system prompt)
  - Expected interviewer behaviors that should be triggered
  - Domain-specific answer templates for fast (no-LLM) mode

Used by simulator.py to drive the interview loop without a human.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from app.models.session import VLSIDomain


class CandidateType(str, Enum):
    STRONG              = "strong"
    WEAK                = "weak"
    MEMORIZED           = "memorized"
    CONTRADICTORY       = "contradictory"
    VAGUE               = "vague"
    OVERCONFIDENT       = "overconfident"
    PRACTICAL_DEBUGGER  = "practical_debugger"


# ── Expected behaviors per profile (for assertion checks) ────────────────────
# Simulator verifies these behaviors appear during the interview.

@dataclass
class ProfileExpectations:
    """What the INTERVIEWER should do in response to this profile."""
    should_probe_mechanisms: bool = False     # ask "how exactly does X work"
    should_escalate: bool = False             # mode should go to ESCALATING or PRESSURE
    should_recover: bool = False              # mode should go to RECOVERING
    should_challenge_ownership: bool = False  # question should ask "how did you specifically..."
    should_surface_contradiction: bool = False
    should_flag_memorization: bool = False
    should_deepen: bool = False               # mode should go to DEEPENING
    should_NOT_praise: bool = True            # always — no praise regardless of profile


# ── Candidate profile ─────────────────────────────────────────────────────────

@dataclass
class CandidateProfile:
    type: CandidateType
    domain: VLSIDomain
    system_prompt: str
    expectations: ProfileExpectations
    # Pre-built answers for fast mode (no LLM for candidate)
    fast_answers: list[str] = field(default_factory=list)
    _turn: int = field(default=0, init=False)

    async def generate_answer(
        self,
        question: str,
        history: list[dict],
        use_llm: bool = True,
    ) -> str:
        """Generate a candidate answer using LLM (or fast template)."""
        if not use_llm or not self.fast_answers:
            return await self._llm_answer(question, history)

        # Fast mode: rotate through pre-built answers
        answer = self.fast_answers[self._turn % len(self.fast_answers)]
        self._turn += 1
        return answer

    async def _llm_answer(self, question: str, history: list[dict]) -> str:
        from app.providers.llm import generate
        history_text = ""
        if history:
            last = history[-3:]
            history_text = "\n".join(
                f"Q: {h['question']}\nA: {h['answer']}" for h in last
            )
        history_prefix = f"Previous exchanges:\n{history_text}\n\n" if history_text else ""
        prompt = f"{history_prefix}Question: {question}\n\nYour answer:"
        try:
            return await asyncio.wait_for(
                generate(
                    system=self.system_prompt,
                    prompt=prompt,
                    max_tokens=180,
                    temperature=0.8,
                ),
                timeout=8.0,
            )
        except Exception:
            # Fallback to fast answer if LLM fails
            if self.fast_answers:
                ans = self.fast_answers[self._turn % len(self.fast_answers)]
                self._turn += 1
                return ans
            return "I'm not sure about that."


# ── Profile factory ───────────────────────────────────────────────────────────

def build_profiles(domain: VLSIDomain) -> list[CandidateProfile]:
    return [
        _strong_candidate(domain),
        _weak_candidate(domain),
        _memorized_candidate(domain),
        _contradictory_candidate(domain),
        _vague_candidate(domain),
        _overconfident_candidate(domain),
        _practical_debugger(domain),
    ]


def _strong_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.STRONG,
        domain=domain,
        system_prompt="""You are a highly experienced VLSI engineer with 8+ years.
You answer interview questions with specific technical depth:
- Always explain the underlying mechanism, not just the concept name.
- Reference specific projects you worked on with real numbers (timing margins, process nodes, etc.)
- Use first-person: "In my last tape-out, I...", "I debugged this by..."
- If asked about edge cases, you know them.
- Keep answers focused: 3-5 sentences covering mechanism + personal experience + tradeoff.
- Do NOT be verbose or lecture-like. Concise, expert answers only.""",
        expectations=ProfileExpectations(
            should_deepen=True,
            should_NOT_praise=True,
        ),
        fast_answers=_strong_fast_answers(domain),
    )


def _weak_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.WEAK,
        domain=domain,
        system_prompt="""You are a junior VLSI engineer who knows terminology but lacks depth.
Answer interview questions with:
- Short, thin answers (1-2 sentences max)
- Name the concept but don't explain the mechanism
- No personal experience — you've only read about these topics
- Vague language: "it helps with", "it's used for", "it prevents issues"
- Never mention specific numbers or projects
- If asked for mechanism: say "I think it works by..." and trail off""",
        expectations=ProfileExpectations(
            should_probe_mechanisms=True,
            should_escalate=True,
            should_NOT_praise=True,
        ),
        fast_answers=_weak_fast_answers(domain),
    )


def _memorized_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.MEMORIZED,
        domain=domain,
        system_prompt="""You are a candidate who memorized textbook definitions.
Answer interview questions with:
- Textbook-perfect definitions that sound memorized: "X is a technique that..."
- Definition-first pattern: start every answer by defining the term
- Never say "I did" or "in my project" — speak abstractly
- Sound like you're reciting: structured, formal, impersonal
- If asked about practical experience: pivot back to the definition
- Use passive voice: "is used to", "is applied when", "is known for"
- Your answers are technically correct but have zero personal experience""",
        expectations=ProfileExpectations(
            should_probe_mechanisms=True,
            should_challenge_ownership=True,
            should_flag_memorization=True,
            should_NOT_praise=True,
        ),
        fast_answers=_memorized_fast_answers(domain),
    )


def _contradictory_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.CONTRADICTORY,
        domain=domain,
        system_prompt="""You are a candidate who contradicts yourself across the interview.
Rules:
- In turn 1-2: make specific strong claims ("I always use X", "X is critical for Y")
- In turn 3-4: casually contradict those claims ("actually we never used X", "X doesn't really matter")
- Don't acknowledge the contradiction — present each statement confidently
- Mix different contradictions: claim one thing early, say the opposite later
- Examples of contradictions to use:
  * "Common centroid is essential" → later: "we just used manual placement, common centroid is overkill"
  * "Timing margin was never an issue" → later: "we spent months on timing closure"
  * "I always ran full DRC before tapeout" → later: "we skipped DRC on the analog blocks"
Be natural, not obviously contradictory. Sound like you're genuinely answering each question.""",
        expectations=ProfileExpectations(
            should_surface_contradiction=True,
            should_NOT_praise=True,
        ),
        fast_answers=_contradictory_fast_answers(domain),
    )


def _vague_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.VAGUE,
        domain=domain,
        system_prompt="""You are a candidate who speaks in buzzwords and vague statements.
Answer interview questions with:
- Use every relevant buzzword: matching, parasitics, timing closure, CTS, UVM, coverage, etc.
- Never explain what the buzzwords mean
- Use vague quantifiers: "improved significantly", "reduced considerably", "much better performance"
- When asked for specifics: give more buzzwords instead
- Favorite phrases: "it depends on the design", "there are tradeoffs", "we optimized it",
  "it's important to consider", "we followed best practices"
- Sound confident but never commit to a specific number, mechanism, or decision
- Length: 2-4 sentences of dense buzzwords""",
        expectations=ProfileExpectations(
            should_probe_mechanisms=True,
            should_escalate=True,
            should_NOT_praise=True,
        ),
        fast_answers=_vague_fast_answers(domain),
    )


def _overconfident_candidate(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.OVERCONFIDENT,
        domain=domain,
        system_prompt="""You are a candidate who claims mastery but can't back it up.
Answer interview questions with:
- Strong opening claim: "I know this well", "I've done this extensively", "This is straightforward"
- Follow with a shallow or incorrect explanation
- When challenged: become defensive or evasive ("it's obvious", "that's how everyone does it")
- Claim broad experience: "I've taped out 10+ designs", "I've seen every scenario"
- Make confident but sometimes subtly wrong technical statements
- Use dismissive language about edge cases: "that's not really a concern in practice"
- If asked for specific numbers: give plausible-sounding but vague ones""",
        expectations=ProfileExpectations(
            should_probe_mechanisms=True,
            should_surface_contradiction=True,
            should_challenge_ownership=True,
            should_NOT_praise=True,
        ),
        fast_answers=_overconfident_fast_answers(domain),
    )


def _practical_debugger(domain: VLSIDomain) -> CandidateProfile:
    return CandidateProfile(
        type=CandidateType.PRACTICAL_DEBUGGER,
        domain=domain,
        system_prompt="""You are a candidate with strong practical debugging experience.
Answer interview questions with:
- Describe specific debugging scenarios and how you resolved them
- First-person: "I ran into this on a 28nm block where..."
- Give concrete debug steps: "first I checked X, then found Y, fixed it by Z"
- Reference specific tools: Virtuoso, Calibre, Innovus, VCS, DVE
- Include specific numbers: layer names, timing numbers, coverage percentages
- Strong on HOW things broke and HOW you fixed them
- Weaker on formal definitions — you know by doing, not by textbook
- Occasionally misuse formal terminology but describe the correct concept""",
        expectations=ProfileExpectations(
            should_deepen=True,
            should_challenge_ownership=False,  # ownership is real and specific
            should_NOT_praise=True,
        ),
        fast_answers=_practical_fast_answers(domain),
    )


# ── Fast-mode answer templates ─────────────────────────────────────────────────

def _strong_fast_answers(domain: VLSIDomain) -> list[str]:
    if domain == VLSIDomain.ANALOG_LAYOUT:
        return [
            "I taped out a 28nm OTA where matching was critical. Used 4-finger common centroid with dummy cells — measured 0.08% mismatch vs 0.6% without it. The key is cancelling both X and Y gradients simultaneously.",
            "In that block the parasitic capacitance on the drain was killing the GBW. I used shielded routing on M3 and kept the drain net under 15fF. Post-layout sim showed 20% GBW recovery.",
            "We hit a latch-up issue near the charge pump — substrate resistance was too high. Added a ring of p+ diffusion guard rings around the NMOS devices and biased the n-well explicitly. Fixed after second iteration.",
            "The ESD cell was introducing 8fF at the input which violated spec. I redesigned it with a two-stage RC filter and moved the protection device closer to the pad. Final post-layout capacitance was 3.2fF.",
            "CTS was the hardest part — 40 clock domains, 200ps max skew budget. I ran multiple CTS iterations, then did manual ECOs on the three critical paths. Final skew was 180ps.",
            "IR drop on the analog supply was 18mV which was too high. Added a local decap ring around the sensitive amplifier, resized the power mesh on M6, and moved to a star topology for the analog VDD.",
        ]
    elif domain == VLSIDomain.PHYSICAL_DESIGN:
        return [
            "Timing closure on my last block took 3 weeks. The critical path was an adder — 450ps slack violation after CTS. I ran an incremental synthesis pass targeting those paths, then manual ECOs with upsizing.",
            "We had severe congestion in the DSP array — routing utilization hit 95% in one region. I flattened the macros, ran a placement-focused legalization pass, and added buffer zones. Final utilization was 78%.",
            "Hold violations after CTS — 23 paths failing by 15-50ps. Root cause was clock skew between two domains. Fixed by balancing the clock tree insertion delays and adding hold buffers on the failing endpoints.",
            "IR drop in the digital core was 45mV under scan. Added a Vdd stripe every 80um and resized the power switch cells by 20%. Post-route IR drop simulation showed 18mV — within the 25mV spec.",
        ]
    else:
        return [
            "I built a UVM environment for a PCIe controller from scratch. The scoreboard had separate queues for TLP headers and data, with a credit-based flow control model that matched the DUT exactly.",
            "Coverage closure on my last project took 2 months. We had 340 uncovered functional coverage bins. I analyzed each — 40% were unreachable and needed model fix, the rest needed constrained random enhancements.",
            "Found a bug where the scoreboard wasn't checking back-to-back completions. Added a queue depth monitor assertion — caught the issue in simulation before tape-out. The bug was a FIFO pointer miscalculation.",
        ]


def _weak_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "Common centroid is used for matching. It helps reduce mismatch.",
        "Parasitics are important to consider in layout. They can affect performance.",
        "I've worked with matching techniques. It's important for differential circuits.",
        "Timing closure is about meeting the timing constraints. You run the tool and it fixes violations.",
        "UVM is a methodology for verification. It has agents and sequences.",
        "Guard rings prevent latch-up. They're placed around devices.",
    ]


def _memorized_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "Common centroid is a layout technique that places matched devices symmetrically around a common center point to cancel systematic gradient errors.",
        "Latch-up is a parasitic thyristor effect in CMOS processes that occurs when the parasitic npnp or pnpn structure is triggered into a low-resistance state.",
        "Clock tree synthesis is the process of inserting buffers and inverters to distribute the clock signal to all flip-flops with minimum skew and insertion delay.",
        "UVM, or Universal Verification Methodology, is a standardized methodology based on SystemVerilog for verifying integrated circuit designs.",
        "IR drop refers to the voltage drop across the power delivery network due to resistive losses, which can cause timing violations and functional failures.",
        "Interdigitation is a layout technique where matched devices are divided into fingers and alternated to minimize mismatch due to process gradients.",
    ]


def _contradictory_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "We always used common centroid for all matched pairs — it's mandatory in our design flow.",
        "The timing margin on our critical paths was always positive. We never had timing issues.",
        "Actually, for that analog block we just used regular placement — common centroid adds too much area overhead and it's not really necessary for our accuracy requirements.",
        "We actually spent about 4 months fighting timing closure on that chip. The critical path kept failing after ECO.",
        "I always run full LVS and DRC before signoff — it's non-negotiable in our team.",
        "For the analog blocks we typically skip formal DRC — the designers know the rules and the tool has too many false positives.",
    ]


def _vague_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "We optimized the layout significantly using various matching techniques and parasitics reduction strategies, which improved performance considerably.",
        "Timing closure was addressed through comprehensive ECO flows and CTS optimization, resulting in better overall timing performance.",
        "The verification coverage was improved by enhancing the constrained random environment and adding more targeted sequences for corner cases.",
        "We followed best practices for physical design including proper floorplanning, congestion-aware placement, and optimized routing strategies.",
        "The analog layout used appropriate techniques to minimize mismatch and parasitic effects in line with industry standards.",
        "Our UVM environment covered the major functional scenarios and we achieved good coverage metrics across the board.",
    ]


def _overconfident_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "I know matching inside out — done it on probably 15+ designs. Common centroid, interdigitation, all of it. It's straightforward once you understand the basics.",
        "Timing closure is something I can do in my sleep. You just run the tool, check the reports, and fix the violations. Done it hundreds of times.",
        "UVM is fairly simple — I've written complete environments from scratch. Agents, sequences, scoreboards — it's all just boilerplate once you know the pattern.",
        "I've never had a real latch-up issue in practice. It's one of those things people worry about but if you're using a modern PDK it's basically handled for you.",
        "Coverage closure? That's just a matter of running enough simulations. We always hit 100% coverage on our projects. It's not that hard.",
        "Honestly at my level the tool handles most of the physical design. You set the constraints, run the flow, and check the results. It's fairly automated now.",
    ]


def _practical_fast_answers(domain: VLSIDomain) -> list[str]:
    return [
        "We hit a mismatch issue on a 28nm resistor ladder — post-layout sim showed 0.4% variation. Checked the Virtuoso extract output and found the via resistance on one branch was 3x higher. Rerouted to equalize via counts, fixed it.",
        "Had a hold violation after CTS on a specific clock domain — 32ps fail. The issue was two buffers with different drive strengths driving the same net. Innovus ECO flow couldn't find it automatically so I manually resized and rerouted.",
        "Our scoreboard was missing a check for out-of-order completions on the AXI bus. Found it during regression — saw a mismatch in the checker. Took 3 days to trace it back to a FIFO ordering assumption that was wrong.",
        "Congestion in one corner of the die was killing routing completion. Vivado showed 97% utilization in a 200um x 200um box. I moved two macros manually and added placement blockages. Dropped to 82% and routing finished.",
        "Had a latch-up fail in silicon — one specific test pattern triggered it. Traced it back to a missing guard ring on an NMOS device near the analog-digital boundary. Fixed it with a p+ ring, verified with Calibre LVS.",
    ]
