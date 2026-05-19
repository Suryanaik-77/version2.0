"""
session.py — Session lifecycle management.

Owned by: interview_engine (state mutations)
This module: lifecycle only (create, get, end, flush).
No question generation, no eval logic here.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import structlog

from app.config import get_settings
from app.core import redis as r
from app.models.session import (
    SessionState,
    SessionContext,
    CandidateMemory,
    SessionSummary,
    EndReason,
    InterviewerMode,
    VLSIDomain,
    SessionPhase,
)
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()


async def create_session(
    candidate_id: str,
    domain: VLSIDomain,
    resume_data: dict | None = None,
) -> SessionState:
    """
    Creates a new session in Redis AND creates a pending row in Postgres.

    Does NOT start the interview — that happens when the first audio arrives.
    Returns the initial SessionState.

    Latency: ~2 Redis writes + 1 async DB write = ~5-10ms
    """
    from app.models.session import ResumeData
    session_id = str(uuid.uuid4())

    resume = ResumeData(**(resume_data or {}))

    state = SessionState(
        session_id=session_id,
        candidate_id=candidate_id,
        active_domain=domain,
        mode=InterviewerMode.PROBING,
        phase=SessionPhase.WARMUP,
        resume=resume,
    )

    memory = CandidateMemory(session_id=session_id)

    ctx = SessionContext(
        session_id=session_id,
        mode=InterviewerMode.PROBING,
        active_domain=domain,
        turn_count=0,
    )

    # Atomic pipeline — all three keys written together
    await asyncio.gather(
        r.set_session_state(state),
        r.set_session_context(ctx),
        r.set_memory(memory),
    )

    # Create pending DB row in background — FK must exist before turns are written
    asyncio.create_task(
        _create_db_session_pending(session_id, candidate_id, domain.value),
        name=f"db_session_pending_{session_id}",
    )

    record_event("session.created", session_id=session_id, domain=domain.value)
    log.info("session.created", session_id=session_id, domain=domain.value)

    return state


async def _create_db_session_pending(
    session_id: str,
    candidate_id: str,
    domain: str,
) -> None:
    """Background: create the pending InterviewSession row in Postgres."""
    try:
        from app.db.persistence import upsert_session_pending
        await upsert_session_pending(session_id, candidate_id, domain)
    except Exception as exc:
        log.warning("session.db_pending_failed", session_id=session_id, error=str(exc))


async def get_session(session_id: str) -> SessionState | None:
    """Returns current session state from Redis."""
    return await r.get_session_state(session_id)


async def session_exists(session_id: str) -> bool:
    state = await r.get_session_state(session_id)
    return state is not None and state.is_active


async def increment_turn(session_id: str) -> int:
    """
    Atomically increments turn counter and refreshes TTL.
    Returns new turn count.

    Called at the START of each turn before question generation.
    Marks session as 'active' in DB on turn 1.
    """
    state = await r.get_session_state(session_id)
    if not state:
        raise SessionNotFoundError(session_id)

    state.turn_count += 1
    state.last_turn_at = datetime.utcnow()

    # Advance phase based on turn count
    if state.turn_count == 2:
        state.phase = SessionPhase.CORE
    elif state.turn_count >= 6:
        state.phase = SessionPhase.PRESSURE

    await r.set_session_state(state)
    await r.touch_session(session_id)

    # On first turn: mark session active in DB
    if state.turn_count == 1:
        asyncio.create_task(
            _mark_db_session_active(session_id, state.started_at),
            name=f"db_active_{session_id}",
        )

    return state.turn_count


async def _mark_db_session_active(session_id: str, started_at: datetime) -> None:
    try:
        from app.db.persistence import mark_session_active
        await mark_session_active(session_id, started_at)
    except Exception as exc:
        log.warning("session.db_mark_active_failed",
                    session_id=session_id, error=str(exc))


async def update_mode(
    session_id: str,
    mode: InterviewerMode,
    turn_count: int | None = None,
) -> None:
    """
    Called by strategy_engine ASYNCHRONOUSLY after eval completes.
    Takes effect on the NEXT turn — never the current one.

    This is the only permitted source of mode mutation.
    """
    await r.update_session_mode(session_id, mode.value, turn_count)
    log.info("session.mode_updated", session_id=session_id, new_mode=mode.value)


async def end_session(
    session_id: str,
    reason: EndReason,
    ws_reconnects: int = 0,
    total_tokens_in: int = 0,
    total_tokens_out: int = 0,
    total_cost_usd: float = 0.0,
) -> SessionSummary:
    """
    Ends a session:
    1. Marks as inactive in Redis
    2. Flushes final state to Postgres (async task, non-blocking)
    3. Schedules Redis cleanup after flush completes (5s grace period)

    Returns SessionSummary synchronously — Postgres write is async.
    """
    state = await r.get_session_state(session_id)
    if not state:
        raise SessionNotFoundError(session_id)

    state.is_active = False
    await r.set_session_state(state)

    summary = SessionSummary(
        session_id=session_id,
        candidate_id=state.candidate_id,
        total_turns=state.turn_count,
        domains_covered=[state.active_domain],
        final_mode=state.mode,
        started_at=state.started_at,
        ended_at=datetime.utcnow(),
        end_reason=reason,
    )

    extra = {
        "ws_reconnects": ws_reconnects,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_cost_usd": total_cost_usd,
    }

    # Flush to Postgres in background — never blocks session teardown
    asyncio.create_task(
        _flush_session_to_db(session_id, summary, extra),
        name=f"flush_session_{session_id}",
    )

    record_event("session.ended", session_id=session_id, reason=reason.value)
    log.info("session.ended", session_id=session_id, reason=reason.value)

    return summary


async def _flush_session_to_db(
    session_id: str,
    summary: SessionSummary,
    extra: dict,
) -> None:
    """
    Background task: complete session archival to Postgres.

    Order matters:
    1. Upsert session row (establishes FK for turns)
    2. Upsert final memory snapshot
    3. Compute aggregate scores on the session row
    4. Finalize the session report (dimension averages, hiring signal)
    5. Run integrity scoring
    6. Clean up Redis (last — only after successful Postgres writes)
    """
    log.info("session.flush_started", session_id=session_id)

    try:
        from app.db.persistence import (
            upsert_session,
            upsert_memory_snapshot,
            update_session_aggregate_scores,
            finalize_session_report,
            persist_integrity,
        )
        from app.core.anti_cheat import compute_integrity

        # Step 1: Write session row
        await upsert_session(summary, extra)

        # Step 2: Fetch and write final memory snapshot
        memory = await r.get_memory(session_id)
        if memory:
            memory_dict = memory.model_dump()
            await upsert_memory_snapshot(session_id, memory_dict, is_final=True)

        # Step 3: Update aggregate scores on session row
        await update_session_aggregate_scores(session_id)

        # Step 4: Finalize session report
        await finalize_session_report(session_id)

        # Step 5: Compute and persist integrity
        turn_latencies: list[int] = []
        answers: list[str] = []
        recent = await r.get_recent_turns(session_id, n=summary.total_turns or 0)
        for t in recent:
            if t.get("answer"):
                answers.append(t["answer"])
            if t.get("turn_total_ms"):
                turn_latencies.append(int(t["turn_total_ms"]))

        integrity_result = await compute_integrity(session_id, answers, turn_latencies)
        await persist_integrity(session_id, integrity_result)

        log.info("session.flush_complete", session_id=session_id)

    except Exception as exc:
        log.error("session.flush_failed",
                  session_id=session_id, error=str(exc), exc_info=exc)
        # Do NOT skip Redis cleanup — partial data is better than stale Redis
    finally:
        # Always clean Redis after flush attempt (success or partial failure)
        # TTL (4h) is the final safety net
        await asyncio.sleep(5)  # grace period for any in-flight reads
        await r.delete_session(session_id)
        log.info("session.redis_cleaned", session_id=session_id)


async def periodic_memory_flush(session_id: str) -> None:
    """
    Triggered every 5 turns by interview_engine.
    Writes current memory snapshot to Postgres as a checkpoint.
    Non-blocking — called as a background task.
    """
    try:
        from app.db.persistence import upsert_memory_snapshot
        memory = await r.get_memory(session_id)
        if memory is None:
            return
        await upsert_memory_snapshot(
            session_id=session_id,
            memory_dict=memory.model_dump(),
            is_final=False,
        )
        log.debug("session.memory_checkpoint", session_id=session_id)
    except Exception as exc:
        log.warning("session.memory_flush_failed",
                    session_id=session_id, error=str(exc))


# ── Exceptions ────────────────────────────────────────────────────────────────

class SessionNotFoundError(Exception):
    def __init__(self, session_id: str):
        super().__init__(f"Session not found or expired: {session_id}")
        self.session_id = session_id


class SessionEndedError(Exception):
    def __init__(self, session_id: str):
        super().__init__(f"Session already ended: {session_id}")
        self.session_id = session_id

import asyncio
import uuid
from datetime import datetime

import structlog

from app.config import get_settings
from app.core import redis as r
from app.models.session import (
    SessionState,
    SessionContext,
    CandidateMemory,
    SessionSummary,
    EndReason,
    InterviewerMode,
    VLSIDomain,
    SessionPhase,
)
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()
