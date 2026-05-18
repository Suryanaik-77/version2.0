"""
simulator.py — Interview simulation loop.

Runs the real question_engine + eval_engine with in-memory state.
No Redis, no WebSocket, no audio — pure behavioral testing.

Two modes:
  run_scenario(scenario)  — deterministic single-turn test
  run_full_simulation(profile, turns)  — multi-turn interview with a candidate profile

Both use real LLM calls for question generation.
Candidate answers use profile.fast_answers by default (no LLM cost),
or LLM-generated answers with use_llm=True.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from app.engines import question as qeng
from app.engines import eval as eeng
from app.engines import strategy
from app.engines import memory as memeng
from app.models.session import (
    CandidateMemory,
    InterviewerMode,
    TurnContext,
    VLSIDomain,
    SessionPhase,
)
from tests.simulation.evaluator import (
    RealistmEvaluator,
    SimulationMetrics,
    TurnEvaluation,
    evaluate_humanness,
)
from tests.simulation.profiles import CandidateProfile
from tests.simulation.scenarios import Scenario

log = structlog.get_logger(__name__)

evaluator = RealistmEvaluator()


# ── Turn result ───────────────────────────────────────────────────────────────

@dataclass
class TurnResult:
    turn_number: int
    answer: str
    question: str
    mode_before: InterviewerMode
    mode_after: InterviewerMode
    eval_scores: dict
    evaluation: TurnEvaluation
    latency_ms: int
    first_token_ms: int
    tokens_generated: int


@dataclass
class SimulationResult:
    profile_type: str
    domain: str
    turns: list[TurnResult]
    metrics: SimulationMetrics
    overall_passed: bool


@dataclass
class ScenarioResult:
    scenario_name: str
    domain: str
    question: str
    evaluation: TurnEvaluation
    latency_ms: int
    passed: bool
    failure_reasons: list[str]


# ── Full simulation ───────────────────────────────────────────────────────────

async def run_full_simulation(
    profile: CandidateProfile,
    turns: int = 6,
    use_llm_for_answers: bool = False,
    evaluate_humanness_score: bool = False,
) -> SimulationResult:
    """
    Run a complete interview simulation with a candidate profile.

    State is in-memory (no Redis).
    question_engine uses real LLM.
    Candidate answers use fast templates (or LLM if use_llm_for_answers=True).
    """
    memory = CandidateMemory(session_id="sim-session")
    mode = InterviewerMode.PROBING
    history: list[TurnResult] = []
    prior_questions: list[str] = []
    metrics = SimulationMetrics(
        profile_type=profile.type.value,
        domain=profile.domain.value,
    )

    # Opening question — no LLM needed
    from app.engines.interview import generate_opening
    opening = _get_opening(profile.domain)
    current_question = opening

    log.info(
        "simulation.start",
        profile=profile.type.value,
        domain=profile.domain.value,
        turns=turns,
    )

    for turn_num in range(1, turns + 1):
        # ── Generate candidate answer ─────────────────────────────────────────
        answer = await profile.generate_answer(
            question=current_question,
            history=[{"question": r.question, "answer": r.answer} for r in history],
            use_llm=use_llm_for_answers,
        )

        # ── Build TurnContext (in-memory, no Redis) ───────────────────────────
        ctx = TurnContext(
            session_id="sim-session",
            turn_number=turn_num,
            transcript=answer,
            domain=profile.domain,
            mode=mode,
            memory=memory,
            prior_answers=[r.answer for r in history[-3:]],
        )

        # ── Generate interviewer question (real LLM) ──────────────────────────
        t_start = time.monotonic()
        first_token_ms = 0
        tokens: list[str] = []
        first_token_time: float | None = None

        try:
            async for token in qeng.stream(ctx):
                if first_token_time is None:
                    first_token_time = time.monotonic()
                    first_token_ms = int((first_token_time - t_start) * 1000)
                tokens.append(token)
        except Exception as exc:
            log.error("simulation.qgen_failed", turn=turn_num, error=str(exc))
            tokens = [f"[question_engine error: {exc}]"]

        question = "".join(tokens).strip()
        latency_ms = int((time.monotonic() - t_start) * 1000)

        # ── Evaluate question ─────────────────────────────────────────────────
        memory_has_contradiction = any(not c.resolved for c in memory.contradictions)
        ev = evaluator.evaluate_turn(
            turn_number=turn_num,
            question=question,
            answer=answer,
            prior_questions=prior_questions,
            memory_has_contradiction=memory_has_contradiction,
        )

        # Optional: humanness score via LLM judge
        if evaluate_humanness_score:
            h = await evaluate_humanness(question, answer, profile.domain.value)
            ev.humanness_score = h["score"]

        metrics.record(ev)

        # ── Async eval (inline for simulation, no background task) ────────────
        eval_scores = await _quick_eval(answer, profile.domain, current_question)
        mode_after = strategy.decide_mode_from_eval(
            current_mode=mode,
            eval_scores=eval_scores,
            inline_signals=None,
        )

        # ── Update memory ─────────────────────────────────────────────────────
        # Simplified update (avoid Redis dependency)
        _update_memory_inline(memory, answer, eval_scores, turn_num)

        # ── Record result ─────────────────────────────────────────────────────
        result = TurnResult(
            turn_number=turn_num,
            answer=answer,
            question=question,
            mode_before=mode,
            mode_after=mode_after,
            eval_scores=eval_scores,
            evaluation=ev,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
            tokens_generated=len(tokens),
        )
        history.append(result)
        prior_questions.append(question)
        mode = mode_after
        current_question = question

        log.info(
            "simulation.turn_done",
            turn=turn_num,
            profile=profile.type.value,
            mode_after=mode_after.value,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
            passed=ev.passed,
            ai_hits=len(ev.ai_phrase_hits),
        )

    return SimulationResult(
        profile_type=profile.type.value,
        domain=profile.domain.value,
        turns=history,
        metrics=metrics,
        overall_passed=metrics.overall_pass(),
    )


# ── Scenario runner ───────────────────────────────────────────────────────────

async def run_scenario(scenario: Scenario) -> ScenarioResult:
    """
    Run a single deterministic behavioral test case.
    Uses real LLM for question generation.
    No candidate LLM needed.
    """
    memory = CandidateMemory(session_id=f"scenario-{scenario.name}")

    # Seed memory with setup answers
    for i, setup_answer in enumerate(scenario.setup_answers):
        _update_memory_inline(memory, setup_answer, {}, i)

    ctx = TurnContext(
        session_id=f"scenario-{scenario.name}",
        turn_number=len(scenario.setup_answers) + 1,
        transcript=scenario.test_answer,
        domain=scenario.domain,
        mode=scenario.mode,
        memory=memory,
        prior_answers=scenario.setup_answers[-3:],
    )

    # Override memory_context if specified
    if scenario.memory_context:
        # Inject into memory as a claim
        memory.claims.append(scenario.memory_context)

    t_start = time.monotonic()
    tokens: list[str] = []
    try:
        async for token in qeng.stream(ctx):
            tokens.append(token)
    except Exception as exc:
        tokens = [f"[error: {exc}]"]

    question = "".join(tokens).strip()
    latency_ms = int((time.monotonic() - t_start) * 1000)

    ev = evaluator.evaluate_turn(
        turn_number=len(scenario.setup_answers) + 1,
        question=question,
        answer=scenario.test_answer,
        prior_questions=scenario.setup_answers,
        memory_has_contradiction=len(scenario.setup_answers) > 0,
    )

    # Check expected behaviors
    failure_reasons = []
    behavior_map = {
        "mechanism_probe":       ev.has_mechanism_probe,
        "ownership_probe":       ev.has_ownership_probe,
        "contradiction_surface": ev.has_contradiction_surface,
        "pressure":              ev.has_pressure,
        "topic_continuity":      ev.references_prior_answer,
    }
    for behavior in scenario.expected_behaviors:
        if behavior in behavior_map and not behavior_map[behavior]:
            failure_reasons.append(f"MISSING expected behavior: {behavior}")

    # Check forbidden behaviors
    if "ai_phrase" in scenario.forbidden_behaviors and ev.ai_phrase_hits:
        failure_reasons.extend([f"FORBIDDEN ai_phrase: {h}" for h in ev.ai_phrase_hits])
    if "praise" in scenario.forbidden_behaviors:
        if any("praise" in c.name or "affirm" in c.name for c in ev.failed_checks):
            failure_reasons.append("FORBIDDEN behavior: praise detected")

    passed = len(failure_reasons) == 0

    return ScenarioResult(
        scenario_name=scenario.name,
        domain=scenario.domain.value,
        question=question,
        evaluation=ev,
        latency_ms=latency_ms,
        passed=passed,
        failure_reasons=failure_reasons,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _quick_eval(answer: str, domain: VLSIDomain, question: str) -> dict:
    """
    Simplified eval for simulation. Uses real LLM but simplified prompt.
    Returns dict of dimension scores.
    """
    from app.engines.eval import run_async_eval as _  # side-effect import for checking
    from app.engines.prompts import EVAL_SYSTEM, build_eval_prompt
    from app.providers.llm import generate
    from app.engines.eval import _parse_eval_json

    prompt = build_eval_prompt(domain, question, answer)
    try:
        raw = await asyncio.wait_for(
            generate(system=EVAL_SYSTEM, prompt=prompt, max_tokens=100, temperature=0.1),
            timeout=6.0,
        )
        scores = _parse_eval_json(raw)
        return scores or _default_scores()
    except Exception:
        return _default_scores()


def _default_scores() -> dict:
    return {
        "accuracy": 5, "depth": 5, "completeness": 5,
        "clarity": 5, "maturity": 5, "ownership": 5, "correctness": 5,
    }


def _update_memory_inline(
    memory: CandidateMemory,
    answer: str,
    eval_scores: dict,
    turn_number: int,
) -> None:
    """Update memory without Redis (for simulation only)."""
    # Extract claims
    new_claims = memeng.extract_claims_fast(answer)
    for c in new_claims:
        if c not in memory.claims:
            memory.claims.append(c)
    memory.claims = memory.claims[-20:]

    # Track numbers
    numbers = memeng.extract_numbers_fast(answer)
    for n in numbers:
        n.turn_number = turn_number
    memory.numbers_stated.extend(numbers)
    memory.numbers_stated = memory.numbers_stated[-15:]

    # Buzzword tracking
    buzzwords = memeng.detect_buzzwords_fast(answer)
    for bw in buzzwords:
        existing = next((b for b in memory.buzzwords if b.term == bw), None)
        if existing:
            existing.count += 1
        else:
            from app.models.session import BuzzwordRecord
            memory.buzzwords.append(BuzzwordRecord(term=bw, context=answer[:80], turn_number=turn_number))

    # Contradiction check
    if len(memory.claims) > 1:
        contradiction = memeng._fast_contradiction_check(answer, memory.claims[:-len(new_claims) or None])
        if contradiction:
            contradiction.turn_b = turn_number
            memory.contradictions.append(contradiction)

    # Topic tracking (simplified)
    if eval_scores:
        avg = sum(eval_scores.values()) / len(eval_scores)
        from app.models.session import TopicSummary
        topic = memeng._infer_topic(answer, memory.session_id and VLSIDomain.ANALOG_LAYOUT)
        if avg >= 7.0:
            existing = next((t for t in memory.strong_topics if t.topic == topic), None)
            if existing:
                existing.turn_count += 1
                existing.avg_score = (existing.avg_score * 0.7) + (avg * 0.3)
            else:
                memory.strong_topics.append(TopicSummary(
                    topic=topic, domain=VLSIDomain.ANALOG_LAYOUT, avg_score=avg, turn_count=1
                ))
        elif avg < 5.0:
            existing = next((t for t in memory.weak_topics if t.topic == topic), None)
            if existing:
                existing.turn_count += 1
            else:
                memory.weak_topics.append(TopicSummary(
                    topic=topic, domain=VLSIDomain.ANALOG_LAYOUT, avg_score=avg, turn_count=1
                ))


def _get_opening(domain: VLSIDomain) -> str:
    openings = {
        VLSIDomain.ANALOG_LAYOUT: (
            "Walk me through the most complex analog layout you've personally designed — "
            "the matching strategy you chose and why."
        ),
        VLSIDomain.PHYSICAL_DESIGN: (
            "Describe the last physical design you owned — "
            "the most difficult timing challenge you hit and how you resolved it."
        ),
        VLSIDomain.DESIGN_VERIFICATION: (
            "Tell me about the most complex verification environment you've built — "
            "the coverage model and how you closed it."
        ),
    }
    return openings[domain]
