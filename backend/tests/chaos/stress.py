"""
stress.py — Concurrent session stress testing.

Tests: session isolation, latency under load, state corruption, cross-talk.

Without real Redis (simulation mode), concurrency risks are:
  - Shared module-level mutable state
  - asyncio task interleaving causing memory corruption
  - LLM provider rate limiting under concurrent load
  - question_engine signal detection using wrong session's context

Each session uses:
  - A unique session_id
  - A unique "fingerprint" answer (topic that appears only in that session)
  - After all sessions complete: verify no cross-contamination
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from statistics import median, quantiles

import structlog

from app.engines import question as qeng
from app.models.session import (
    CandidateMemory, InterviewerMode, TurnContext, VLSIDomain,
)
from tests.chaos.candidates import CHAOS_LIBRARY
from tests.simulation.evaluator import RealistmEvaluator
from tests.simulation.simulator import _update_memory_inline

log = structlog.get_logger(__name__)
evaluator = RealistmEvaluator()


# ── Session fingerprints — unique topic per session ───────────────────────────
# Each session's answer contains a UNIQUE identifier.
# After concurrency test: verify no session's memory contains another's fingerprint.

SESSION_FINGERPRINTS = {
    "sess-A": {
        "topic": "tantalum capacitor decoupling",
        "answer": (
            "I worked specifically on tantalum capacitor decoupling for the analog supply. "
            "The key constraint was the ESL at high frequency — we measured 12nH ESR."
        ),
        "unique_keyword": "tantalum",
    },
    "sess-B": {
        "topic": "gallium arsenide substrate",
        "answer": (
            "My project used a gallium arsenide substrate which had very different "
            "latch-up characteristics than silicon. The guard ring design had to be rethought."
        ),
        "unique_keyword": "gallium",
    },
    "sess-C": {
        "topic": "palladium contacts metallization",
        "answer": (
            "We used palladium contacts as the metallization layer for the ohmic contacts. "
            "This was unusual but required by the foundry for that specific process."
        ),
        "unique_keyword": "palladium",
    },
    "sess-D": {
        "topic": "xenon flash photodiode array",
        "answer": (
            "I designed the readout circuit for a xenon flash photodiode array. "
            "The matching requirement was much tighter than typical — under 0.01%."
        ),
        "unique_keyword": "xenon",
    },
    "sess-E": {
        "topic": "cobalt silicide interconnect failure",
        "answer": (
            "We had a cobalt silicide interconnect failure in silicon that traced back "
            "to a via landing issue. Required a complete ECO on the affected metal layers."
        ),
        "unique_keyword": "cobalt",
    },
    "sess-F": {
        "topic": "titanium nitride barrier layer",
        "answer": (
            "The titanium nitride barrier layer in our process had much higher resistance "
            "than expected, causing IR drop violations we hadn't modeled pre-layout."
        ),
        "unique_keyword": "titanium",
    },
    "sess-G": {
        "topic": "bismuth telluride thermoelectric",
        "answer": (
            "I designed analog blocks for a bismuth telluride thermoelectric harvester. "
            "The supply voltage was only 0.3V which made matching critical."
        ),
        "unique_keyword": "bismuth",
    },
    "sess-H": {
        "topic": "selenium rectifier diode stack",
        "answer": (
            "We inherited a selenium rectifier diode stack layout that had mismatch issues. "
            "I redesigned it with interdigitation and cut the mismatch from 2% to 0.3%."
        ),
        "unique_keyword": "selenium",
    },
    "sess-I": {
        "topic": "lithium niobate modulator",
        "answer": (
            "My last project was a lithium niobate optical modulator driver. "
            "The matching tolerance was 0.02% which pushed us to 12-finger common centroid."
        ),
        "unique_keyword": "lithium",
    },
    "sess-J": {
        "topic": "hafnium oxide gate dielectric",
        "answer": (
            "We moved to hafnium oxide gate dielectric in the 7nm process. "
            "This changed the threshold voltage mismatch model significantly."
        ),
        "unique_keyword": "hafnium",
    },
}


@dataclass
class StressSessionResult:
    session_id: str
    latency_ms: int
    first_token_ms: int
    question: str
    memory_after: CandidateMemory
    error: str | None = None


@dataclass
class ConcurrencyTestResult:
    n_sessions: int
    session_results: list[StressSessionResult]
    total_wall_clock_ms: int
    cross_contamination: list[str]       # list of detected contaminations
    errors: list[str]
    latency_p50_ms: int
    latency_p95_ms: int
    latency_max_ms: int
    sessions_completed: int
    sessions_failed: int

    @property
    def passed(self) -> bool:
        return (
            len(self.cross_contamination) == 0
            and self.sessions_failed == 0
            and len(self.errors) == 0
        )


# ── Single session task ────────────────────────────────────────────────────────

async def _run_stress_session(
    session_id: str,
    fingerprint: dict,
    use_mock_llm: bool = False,
) -> StressSessionResult:
    """
    Run a single session turn with a unique fingerprint answer.
    Returns timing, generated question, and final memory state.
    """
    memory = CandidateMemory(session_id=session_id)
    ctx = TurnContext(
        session_id=session_id,
        turn_number=1,
        transcript=fingerprint["answer"],
        domain=VLSIDomain.ANALOG_LAYOUT,
        mode=InterviewerMode.PROBING,
        memory=memory,
        prior_answers=[],
    )

    t_start = time.monotonic()
    first_token_ms = 0
    tokens: list[str] = []
    first_token_seen = False

    try:
        if use_mock_llm:
            await asyncio.sleep(0.05 + (hash(session_id) % 100) / 1000)  # 50-150ms variance
            question = f"You mentioned {fingerprint['unique_keyword']} — can you explain the mechanism?"
            first_token_ms = 50
        else:
            async for token in qeng.stream(ctx):
                if not first_token_seen:
                    first_token_ms = int((time.monotonic() - t_start) * 1000)
                    first_token_seen = True
                tokens.append(token)
            question = "".join(tokens).strip()

        latency_ms = int((time.monotonic() - t_start) * 1000)

        # Update memory
        _update_memory_inline(memory, fingerprint["answer"], {}, 1)

        return StressSessionResult(
            session_id=session_id,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
            question=question,
            memory_after=memory,
        )

    except Exception as exc:
        return StressSessionResult(
            session_id=session_id,
            latency_ms=int((time.monotonic() - t_start) * 1000),
            first_token_ms=first_token_ms,
            question="",
            memory_after=memory,
            error=str(exc),
        )


# ── Concurrency test ──────────────────────────────────────────────────────────

async def run_concurrent_sessions(
    n_sessions: int,
    use_mock_llm: bool = False,
) -> ConcurrencyTestResult:
    """
    Run N sessions simultaneously. Measures latency and verifies isolation.

    Session isolation test:
    Each session's answer contains a unique technical keyword.
    After all sessions complete, verify each session's memory ONLY contains
    its own keyword — not any other session's.
    """
    session_ids = list(SESSION_FINGERPRINTS.keys())[:n_sessions]
    fingerprints = {sid: SESSION_FINGERPRINTS[sid] for sid in session_ids}

    log.info("stress.starting", n_sessions=n_sessions, mock=use_mock_llm)
    t_wall_start = time.monotonic()

    # Run all sessions concurrently
    tasks = [
        asyncio.create_task(
            _run_stress_session(sid, fp, use_mock_llm=use_mock_llm),
            name=f"stress_{sid}",
        )
        for sid, fp in fingerprints.items()
    ]

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    total_wall_ms = int((time.monotonic() - t_wall_start) * 1000)

    # Process results
    session_results: list[StressSessionResult] = []
    errors: list[str] = []

    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            errors.append(f"{session_ids[i]}: {result}")
            # Create error placeholder
            session_results.append(StressSessionResult(
                session_id=session_ids[i],
                latency_ms=0, first_token_ms=0, question="",
                memory_after=CandidateMemory(session_id=session_ids[i]),
                error=str(result),
            ))
        else:
            session_results.append(result)

    # ── Cross-contamination check ──────────────────────────────────────────────
    cross_contamination = _check_cross_contamination(session_results, fingerprints)

    # ── Latency stats ──────────────────────────────────────────────────────────
    latencies = [r.latency_ms for r in session_results if not r.error]
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95_idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
        p95 = latencies[p95_idx]
        p_max = latencies[-1]
    else:
        p50 = p95 = p_max = 0

    completed = sum(1 for r in session_results if not r.error)
    failed = sum(1 for r in session_results if r.error)

    log.info(
        "stress.complete",
        n_sessions=n_sessions,
        completed=completed,
        failed=failed,
        p50_ms=p50,
        p95_ms=p95,
        wall_ms=total_wall_ms,
        contaminations=len(cross_contamination),
    )

    return ConcurrencyTestResult(
        n_sessions=n_sessions,
        session_results=session_results,
        total_wall_clock_ms=total_wall_ms,
        cross_contamination=cross_contamination,
        errors=errors,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_max_ms=p_max,
        sessions_completed=completed,
        sessions_failed=failed,
    )


def _check_cross_contamination(
    results: list[StressSessionResult],
    fingerprints: dict,
) -> list[str]:
    """
    Check each session's memory for contamination from other sessions.

    Method: each session's answer contains a unique keyword.
    If session A's memory contains session B's keyword, that's contamination.
    """
    contaminations = []

    for result in results:
        if result.error:
            continue

        my_keyword = fingerprints[result.session_id]["unique_keyword"].lower()
        my_memory_text = " ".join(result.memory_after.claims).lower()

        for other_sid, other_fp in fingerprints.items():
            if other_sid == result.session_id:
                continue
            other_keyword = other_fp["unique_keyword"].lower()

            if other_keyword in my_memory_text:
                contaminations.append(
                    f"CONTAMINATION: {result.session_id} memory contains "
                    f"'{other_keyword}' from {other_sid}"
                )

    return contaminations


# ── Baseline latency measurement ───────────────────────────────────────────────

async def measure_baseline_latency(use_mock_llm: bool = False, runs: int = 3) -> dict:
    """Measure single-session latency for comparison with concurrent load."""
    results = []
    fp = SESSION_FINGERPRINTS["sess-A"]
    for i in range(runs):
        r = await _run_stress_session(f"baseline-{i}", fp, use_mock_llm=use_mock_llm)
        if not r.error:
            results.append(r.latency_ms)

    if not results:
        return {"p50": 0, "p95": 0, "max": 0}

    results.sort()
    return {
        "p50": results[len(results) // 2],
        "p95": results[min(int(len(results) * 0.95), len(results) - 1)],
        "max": results[-1],
    }
