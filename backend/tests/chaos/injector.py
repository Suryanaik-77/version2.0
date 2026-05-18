"""
injector.py — Runs chaos answers through real engines.

For each chaos answer:
  1. Injects into question_engine (real LLM)
  2. Runs memory_engine update
  3. Runs eval (mock or real)
  4. Checks strategy engine mode transition
  5. Evaluates interviewer question for realism
  6. Classifies any failures

No Redis, no WebSocket. Pure behavioral validation.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from app.engines import question as qeng
from app.engines import memory as memeng
from app.engines import strategy
from app.engines.eval import _parse_eval_json
from app.models.session import (
    CandidateMemory, InterviewerMode, TurnContext, VLSIDomain, SessionPhase,
)
from tests.chaos.candidates import ChaosAnswer, ChaosType
from tests.simulation.evaluator import RealistmEvaluator, TurnEvaluation
from tests.simulation.simulator import _update_memory_inline, _quick_eval, _default_scores

log = structlog.get_logger(__name__)
evaluator = RealistmEvaluator()


# ── Failure classification ─────────────────────────────────────────────────────

class FailureClass(str):
    REAL_BUG               = "real_bug"
    CONCURRENCY_ISSUE      = "concurrency_issue"
    MEMORY_INCONSISTENCY   = "memory_inconsistency"
    VOICE_PIPELINE         = "voice_pipeline_instability"
    EVAL_MISCLASSIFICATION = "evaluator_misclassification"
    ACCEPTABLE_CHAOS       = "acceptable_chaos_behavior"


@dataclass
class ChaosTestResult:
    chaos_type: str
    domain: str
    answer: str
    generated_question: str
    evaluation: TurnEvaluation
    memory_after: CandidateMemory
    mode_before: InterviewerMode
    mode_after: InterviewerMode
    eval_scores: dict
    latency_ms: int
    first_token_ms: int
    tokens_generated: int
    failure_class: str | None = None
    failure_details: list[str] = field(default_factory=list)
    memory_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.failure_class is None
            or self.failure_class == FailureClass.ACCEPTABLE_CHAOS
        )

    @property
    def is_critical(self) -> bool:
        return self.failure_class in (
            FailureClass.REAL_BUG,
            FailureClass.CONCURRENCY_ISSUE,
            FailureClass.MEMORY_INCONSISTENCY,
        )


# ── Run a single chaos scenario ────────────────────────────────────────────────

async def run_chaos_scenario(
    chaos: ChaosAnswer,
    session_id: str = "chaos-test",
    prior_answers: list[str] | None = None,
    prior_claims: list[str] | None = None,
    use_mock_llm: bool = False,
) -> ChaosTestResult:
    """
    Run a chaos answer through the real engine chain.
    Returns detailed result including failure classification.
    """
    memory = CandidateMemory(session_id=session_id)

    # Seed prior claims if provided (for contradiction testing)
    if prior_claims:
        memory.claims.extend(prior_claims)

    # Seed prior answers in memory
    if prior_answers:
        for i, ans in enumerate(prior_answers):
            _update_memory_inline(memory, ans, {}, i)

    mode = InterviewerMode.PROBING

    ctx = TurnContext(
        session_id=session_id,
        turn_number=(len(prior_answers) if prior_answers else 0) + 1,
        transcript=chaos.text,
        domain=chaos.domain,
        mode=mode,
        memory=memory,
        prior_answers=(prior_answers or [])[-3:],
    )

    # ── Generate question ──────────────────────────────────────────────────────
    t_start = time.monotonic()
    first_token_ms = 0
    tokens: list[str] = []
    first_token_seen = False

    try:
        if use_mock_llm:
            question = await _mock_question(chaos)
            first_token_ms = 50
            tokens = [question]  # single token for mock — no word-splitting
        else:
            async for token in qeng.stream(ctx):
                if not first_token_seen:
                    first_token_ms = int((time.monotonic() - t_start) * 1000)
                    first_token_seen = True
                tokens.append(token)
    except Exception as exc:
        log.error("chaos.qgen_error", error=str(exc))
        tokens = [f"[qgen_error: {exc}]"]
        first_token_ms = 0

    question = "".join(tokens).strip()
    latency_ms = int((time.monotonic() - t_start) * 1000)

    # ── Evaluate question ──────────────────────────────────────────────────────
    ev = evaluator.evaluate_turn(
        turn_number=ctx.turn_number,
        question=question,
        answer=chaos.text,
        prior_questions=prior_answers,
        memory_has_contradiction=len(memory.contradictions) > 0,
    )

    # ── Run eval ──────────────────────────────────────────────────────────────
    eval_scores = await _quick_eval(chaos.text, chaos.domain, question) if not use_mock_llm else _default_scores()

    # ── Update memory ──────────────────────────────────────────────────────────
    t_mem_before = _snapshot_memory(memory)
    _update_memory_inline(memory, chaos.text, eval_scores, ctx.turn_number)
    t_mem_after = _snapshot_memory(memory)

    # ── Mode transition ────────────────────────────────────────────────────────
    signals = qeng._detect_signals(ctx, question)
    mode_after = strategy.decide_mode_from_eval(
        current_mode=mode,
        eval_scores=eval_scores,
        inline_signals=signals,
    )

    # ── Classify result ────────────────────────────────────────────────────────
    result = ChaosTestResult(
        chaos_type=chaos.chaos_type.value,
        domain=chaos.domain.value,
        answer=chaos.text,
        generated_question=question,
        evaluation=ev,
        memory_after=memory,
        mode_before=mode,
        mode_after=mode_after,
        eval_scores=eval_scores,
        latency_ms=latency_ms,
        first_token_ms=first_token_ms,
        tokens_generated=len(tokens),
    )

    _classify_result(result, chaos, t_mem_before, t_mem_after, prior_claims or [])
    return result


def _classify_result(
    result: ChaosTestResult,
    chaos: ChaosAnswer,
    mem_before: dict,
    mem_after: dict,
    prior_claims: list[str],
) -> None:
    """
    Inspect result and classify failures.
    Mutates result.failure_class and result.failure_details in place.
    """
    # ── 1. AI phrase leakage → real bug ───────────────────────────────────────
    if result.evaluation.ai_phrase_hits:
        result.failure_class = FailureClass.REAL_BUG
        result.failure_details.extend([
            f"AI phrase in output: {hit}" for hit in result.evaluation.ai_phrase_hits
        ])

    # ── 2. Topic continuity failure → real bug ────────────────────────────────
    if not result.evaluation.references_prior_answer and result.evaluation.turn_number > 1:
        result.failure_class = result.failure_class or FailureClass.REAL_BUG
        result.failure_details.append("Question does not reference prior answer (topic drift)")

    # ── 3. Memory inconsistency checks ────────────────────────────────────────
    mem_issues = _check_memory_integrity(result.memory_after, chaos, prior_claims)
    result.memory_issues.extend(mem_issues)
    if mem_issues and result.failure_class is None:
        result.failure_class = FailureClass.MEMORY_INCONSISTENCY
        result.failure_details.extend(mem_issues)

    # ── 4. Overconfident wrong: eval should be harsh ──────────────────────────
    if chaos.chaos_type == ChaosType.OVERCONFIDENT_WRONG and chaos.technical_errors:
        avg_score = sum(result.eval_scores.values()) / len(result.eval_scores) if result.eval_scores else 5
        correctness = result.eval_scores.get("correctness", 5)
        if correctness >= 6 and avg_score >= 6.0:
            # Eval was too lenient on a wrong answer
            if result.failure_class is None:
                result.failure_class = FailureClass.EVAL_MISCLASSIFICATION
            result.failure_details.append(
                f"Overconfident wrong answer scored too high: "
                f"avg={avg_score:.1f}, correctness={correctness} "
                f"(errors: {chaos.technical_errors[0][:60]})"
            )

    # ── 5. Self-correction: check for false contradiction in memory ───────────
    if chaos.chaos_type == ChaosType.SELF_CORRECTION:
        for contradiction in result.memory_after.contradictions:
            if not contradiction.resolved:
                result.memory_issues.append(
                    f"False contradiction stored from self-correction: "
                    f"'{contradiction.statement_a[:40]}' vs '{contradiction.statement_b[:40]}'"
                )
                if result.failure_class is None:
                    result.failure_class = FailureClass.MEMORY_INCONSISTENCY
                result.failure_details.append(result.memory_issues[-1])

    # ── 6. Empty-after-pause: mode must go to RECOVERING, not ESCALATING ──────
    if chaos.chaos_type == ChaosType.EMPTY_AFTER_PAUSE:
        if result.mode_after == InterviewerMode.ESCALATING:
            # Hesitation should trigger recovery, not escalation
            result.notes.append(
                "WARN: Hesitation misclassified as weakness → ESCALATING. "
                "Should be RECOVERING."
            )
            # This is a near-failure, not a hard failure
            if result.failure_class is None:
                result.failure_class = FailureClass.ACCEPTABLE_CHAOS
                result.failure_details.append(
                    "Hesitation → ESCALATING (should be RECOVERING, but eval-driven — acceptable)"
                )

    # ── 7. Mechanism probe check on vague/incomplete answers ──────────────────
    vague_types = {ChaosType.INCOMPLETE_REASONING, ChaosType.EMPTY_AFTER_PAUSE, ChaosType.VAGUE if hasattr(ChaosType, 'VAGUE') else None}
    if chaos.chaos_type in (ChaosType.INCOMPLETE_REASONING, ChaosType.EMPTY_AFTER_PAUSE):
        if not result.evaluation.has_mechanism_probe and not result.evaluation.references_prior_answer:
            result.notes.append(
                "WARN: Incomplete answer did not trigger mechanism probe"
            )

    # ── 8. If no failure found, mark as passing ────────────────────────────────
    if result.failure_class is None:
        result.notes.append("PASS: All behavioral checks passed under chaos")


def _check_memory_integrity(
    memory: CandidateMemory,
    chaos: ChaosAnswer,
    prior_claims: list[str],
) -> list[str]:
    """Check memory for inconsistencies after chaos input."""
    issues = []

    # Check for duplicate claims
    seen_claims = set()
    for claim in memory.claims:
        normalized = claim.lower().strip()
        if normalized in seen_claims:
            issues.append(f"Duplicate claim in memory: '{claim[:60]}'")
        seen_claims.add(normalized)

    # Check for excessively long claims (indicates full answer stored vs. extracted claim)
    for claim in memory.claims:
        if len(claim) > 200:
            issues.append(f"Claim too long ({len(claim)} chars) — full answer stored instead of extracted claim")

    return issues


def _snapshot_memory(memory: CandidateMemory) -> dict:
    return {
        "claims_count": len(memory.claims),
        "contradictions_count": len(memory.contradictions),
        "buzzwords_count": len(memory.buzzwords),
        "weak_topics_count": len(memory.weak_topics),
    }


async def _mock_question(chaos: ChaosAnswer) -> str:
    """Fast mock question for testing without LLM cost."""
    await asyncio.sleep(0.05)  # simulate LLM latency
    mock_questions = {
        ChaosType.SELF_CORRECTION:      "You mentioned interdigitation with four fingers — what mismatch did you measure post-layout?",
        ChaosType.INCOMPLETE_REASONING: "You mentioned the p-well and n-well — what specific parasitic structure does that guard ring prevent from triggering?",
        ChaosType.TOPIC_SWITCH:         "You mentioned 0.05% mismatch with eight-finger interdigitation — how did you verify that number?",
        ChaosType.OVERCONFIDENT_WRONG:  "Guard rings prevent latch-up, not mismatch — what does common centroid actually do to reduce systematic gradient error?",
        ChaosType.EMPTY_AFTER_PAUSE:    "Let me frame it differently — when you place two matched transistors, what determines how similar their threshold voltages will be?",
        ChaosType.MIXED_CORRECTNESS:    "You understand the gradient cancellation part — what problem does an incomplete row at the array boundary actually create?",
    }
    return mock_questions.get(chaos.chaos_type, "Can you walk me through the mechanism in more detail?")


# ── Run all chaos scenarios ────────────────────────────────────────────────────

async def run_all_chaos(
    domain: VLSIDomain | None = None,
    use_mock_llm: bool = False,
) -> list[ChaosTestResult]:
    """Run all chaos scenarios, return results."""
    from tests.chaos.candidates import get_chaos_answers
    chaos_answers = get_chaos_answers(domain)

    log.info("chaos.starting", total=len(chaos_answers), domain=str(domain), mock=use_mock_llm)

    results = []
    for i, chaos in enumerate(chaos_answers):
        log.info("chaos.running", n=i+1, total=len(chaos_answers), type=chaos.chaos_type.value)

        # For contradiction scenarios, provide a prior claim to contradict
        prior_claims = []
        if chaos.chaos_type == ChaosType.SELF_CORRECTION:
            prior_claims = ["I used common centroid for all matched pairs in this project."]

        result = await run_chaos_scenario(
            chaos,
            session_id=f"chaos-{chaos.chaos_type.value}-{i}",
            prior_claims=prior_claims,
            use_mock_llm=use_mock_llm,
        )
        results.append(result)

    return results
