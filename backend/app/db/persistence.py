"""
db/persistence.py — Async database write helpers.

Design rules:
  1. Every function is async and uses its own session scope.
  2. Every function is safe to call as asyncio.create_task() — never blocking.
  3. Every function handles its own exceptions — never propagates to caller.
  4. Writes are idempotent where possible (upsert over insert).
  5. No function is ever awaited on the hot path.

Called by:
  - session.py: _flush_session_to_db, periodic_memory_flush
  - eval.py: persist_turn_eval
  - metrics.py: persist_metric_batch
  - anti_cheat.py: persist_integrity_record

These helpers are the ONLY code that writes to Postgres.
All other modules write to Redis and schedule these via create_task().
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import (
    IntegrityRecord,
    InterviewSession,
    InterviewTurn,
    OperationalMetric,
    SessionReport,
    SystemEvent,
    User,
)
from app.db.session import db_session
from app.models.session import SessionSummary

log = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
# SESSION ARCHIVAL
# ══════════════════════════════════════════════════════════════════════

async def upsert_session(summary: SessionSummary, extra: dict | None = None) -> None:
    """
    Write (or update) the InterviewSession row.
    Called from _flush_session_to_db after session ends.

    Upsert semantics: safe to call multiple times (reconnect race conditions).
    """
    try:
        async with db_session() as db:
            # Check if row already exists (reconnect scenario)
            existing = await db.get(InterviewSession, summary.session_id)

            if existing:
                # Update existing row with final values
                existing.status = "completed" if not extra else extra.get("status", "completed")
                existing.end_reason = summary.end_reason.value
                existing.ended_at = summary.ended_at
                existing.duration_secs = int(
                    (summary.ended_at - summary.started_at).total_seconds()
                ) if summary.started_at else None
                existing.total_turns = summary.total_turns
                existing.final_mode = summary.final_mode.value if summary.final_mode else None
                if extra:
                    existing.ws_reconnects = extra.get("ws_reconnects", 0)
                    existing.total_tokens_in = extra.get("total_tokens_in", 0)
                    existing.total_tokens_out = extra.get("total_tokens_out", 0)
                    existing.total_cost_usd = extra.get("total_cost_usd", 0.0)
            else:
                # Create new row
                session_row = InterviewSession(
                    id=summary.session_id,
                    candidate_id=summary.candidate_id,
                    domain=summary.domains_covered[0].value if summary.domains_covered else "UNKNOWN",
                    status="completed",
                    end_reason=summary.end_reason.value,
                    started_at=summary.started_at,
                    ended_at=summary.ended_at,
                    duration_secs=int(
                        (summary.ended_at - summary.started_at).total_seconds()
                    ) if summary.started_at else None,
                    total_turns=summary.total_turns,
                    final_mode=summary.final_mode.value if summary.final_mode else None,
                    ws_reconnects=(extra or {}).get("ws_reconnects", 0),
                    total_tokens_in=(extra or {}).get("total_tokens_in", 0),
                    total_tokens_out=(extra or {}).get("total_tokens_out", 0),
                    total_cost_usd=(extra or {}).get("total_cost_usd", 0.0),
                )
                db.add(session_row)

        log.info("db.session_archived", session_id=summary.session_id)

    except Exception as exc:
        log.error("db.upsert_session_failed",
                  session_id=summary.session_id, error=str(exc))


async def upsert_session_pending(
    session_id: str,
    candidate_id: str,
    domain: str,
) -> None:
    """
    Create a 'pending' session row when the session is first created.
    This ensures the FK constraint is satisfied before any turns are written.
    Called from create_session() in session.py.
    """
    try:
        async with db_session() as db:
            existing = await db.get(InterviewSession, session_id)
            if not existing:
                db.add(InterviewSession(
                    id=session_id,
                    candidate_id=candidate_id,
                    domain=domain,
                    status="pending",
                ))
        log.debug("db.session_pending_created", session_id=session_id)
    except Exception as exc:
        log.error("db.session_pending_failed", session_id=session_id, error=str(exc))


async def mark_session_active(session_id: str, started_at: datetime) -> None:
    """Update session status to 'active' when first turn begins."""
    try:
        async with db_session() as db:
            row = await db.get(InterviewSession, session_id)
            if row:
                row.status = "active"
                row.started_at = started_at
    except Exception as exc:
        log.error("db.mark_active_failed", session_id=session_id, error=str(exc))


# ══════════════════════════════════════════════════════════════════════
# TURN PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

async def upsert_turn(
    session_id: str,
    turn_number: int,
    question_text: str,
    answer_text: str,
    domain: str,
    mode_at_start: str,
    mode_at_end: str,
) -> None:
    """
    Write (or update) an InterviewTurn row.
    Called after each turn completes — before eval scores are available.
    Eval scores are merged in via update_turn_eval() once eval finishes.

    Upsert: if the turn already exists (reconnect scenario), update it.
    """
    try:
        async with db_session() as db:
            existing = await db.scalar(
                select(InterviewTurn).where(
                    InterviewTurn.session_id == session_id,
                    InterviewTurn.turn_number == turn_number,
                )
            )
            if existing:
                existing.question_text = question_text
                existing.answer_text = answer_text
                existing.mode_at_start = mode_at_start
                existing.mode_at_end = mode_at_end
            else:
                db.add(InterviewTurn(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    turn_number=turn_number,
                    question_text=question_text,
                    answer_text=answer_text,
                    domain=domain,
                    mode_at_start=mode_at_start,
                    mode_at_end=mode_at_end,
                    created_at=datetime.utcnow(),
                ))

        log.debug("db.turn_persisted", session_id=session_id, turn=turn_number)

    except Exception as exc:
        log.error("db.upsert_turn_failed",
                  session_id=session_id, turn=turn_number, error=str(exc))


async def update_turn_eval(
    session_id: str,
    turn_number: int,
    eval_scores: dict[str, int],
    signals: dict | None = None,
    latency: dict | None = None,
    cost_usd: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    question_text: str = "",
    answer_text: str = "",
) -> None:
    """
    Merge eval scores, signals, and latency into a turn row.
    Creates the row if it doesn't exist (eval may finish before pipeline persist).
    """
    async def _write(create_if_missing: bool = False) -> bool:
        try:
            async with db_session() as db:
                row = await db.scalar(
                    select(InterviewTurn).where(
                        InterviewTurn.session_id == session_id,
                        InterviewTurn.turn_number == turn_number,
                    )
                )
                if not row:
                    if not create_if_missing:
                        return False
                    row = InterviewTurn(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        turn_number=turn_number,
                        question_text=question_text or "",
                        answer_text=answer_text or "",
                        domain="",
                        mode_at_start="",
                        mode_at_end="",
                        created_at=datetime.utcnow(),
                    )
                    db.add(row)

                row.eval_scores = eval_scores
                row.avg_eval_score = (
                    sum(eval_scores.values()) / len(eval_scores)
                    if eval_scores else None
                )
                row.correctness_score = eval_scores.get("correctness")
                row.depth_score = eval_scores.get("depth")
                if signals:
                    row.signals = signals
                if latency:
                    row.stt_latency_ms = latency.get("stt_ms")
                    row.first_token_ms = latency.get("first_token_ms")
                    row.first_audio_ms = latency.get("first_audio_ms")
                    row.turn_total_ms = latency.get("total_ms")
                row.cost_usd = cost_usd
                row.tokens_in = tokens_in
                row.tokens_out = tokens_out
                # Backfill Q&A if row existed but was empty
                if question_text and not row.question_text:
                    row.question_text = question_text
                if answer_text and not row.answer_text:
                    row.answer_text = answer_text
                return True
        except Exception as exc:
            log.error("db.update_turn_eval_failed",
                      session_id=session_id, turn=turn_number, error=str(exc))
            return False

    success = await _write()
    if not success:
        # Turn row may not exist yet (eval beat the turn write) — wait and retry
        await asyncio.sleep(0.5)
        success = await _write()
        if not success:
            # Still missing — create it ourselves
            await _write(create_if_missing=True)


async def update_session_aggregate_scores(session_id: str) -> None:
    """
    Recompute and store aggregate scores on the InterviewSession row.
    Called from _flush_session_to_db after all turns are written.
    """
    try:
        async with db_session() as db:
            # Fetch all turn scores
            result = await db.execute(
                select(
                    InterviewTurn.avg_eval_score,
                    InterviewTurn.correctness_score,
                    InterviewTurn.depth_score,
                ).where(
                    InterviewTurn.session_id == session_id,
                    InterviewTurn.avg_eval_score.isnot(None),
                )
            )
            rows = result.all()
            if not rows:
                return

            scores = [r[0] for r in rows if r[0] is not None]
            correctness = [r[1] for r in rows if r[1] is not None]
            depth = [r[2] for r in rows if r[2] is not None]

            session_row = await db.get(InterviewSession, session_id)
            if session_row:
                session_row.avg_score = sum(scores) / len(scores) if scores else None
                session_row.avg_correctness = sum(correctness) / len(correctness) if correctness else None
                session_row.avg_depth = sum(depth) / len(depth) if depth else None

    except Exception as exc:
        log.error("db.aggregate_scores_failed", session_id=session_id, error=str(exc))


# ══════════════════════════════════════════════════════════════════════
# MEMORY SNAPSHOT PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

async def upsert_memory_snapshot(
    session_id: str,
    memory_dict: dict,
    is_final: bool = False,
) -> None:
    """
    Write the current memory snapshot to SessionReport.memory_snapshot.
    Called every 5 turns as a checkpoint, and finally at session end.

    Creates the SessionReport row if it doesn't exist yet.
    """
    try:
        # Serialize memory_dict to JSON-safe format (handle datetime objects)
        import json as _json
        def _default(obj):
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            return str(obj)
        # Round-trip through JSON to strip datetime objects
        safe_dict = _json.loads(_json.dumps(memory_dict, default=_default))

        async with db_session() as db:
            report = await db.scalar(
                select(SessionReport).where(SessionReport.session_id == session_id)
            )
            if report:
                report.memory_snapshot = safe_dict
                if is_final:
                    report.generated_at = datetime.utcnow()
            else:
                # Create the report row (scores populated later)
                db.add(SessionReport(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    memory_snapshot=safe_dict,
                    review_status="pending",
                    generated_at=datetime.utcnow() if is_final else None,
                ))

        log.debug("db.memory_snapshot_saved",
                  session_id=session_id, is_final=is_final)

    except Exception as exc:
        log.error("db.memory_snapshot_failed",
                  session_id=session_id, error=str(exc))


async def finalize_session_report(session_id: str) -> None:
    """
    Compute and store the final dimension averages in SessionReport.
    Called once from _flush_session_to_db after all turns are written.
    """
    try:
        async with db_session() as db:
            # Fetch all eval scores per turn
            result = await db.execute(
                select(InterviewTurn.eval_scores, InterviewTurn.mode_at_start).where(
                    InterviewTurn.session_id == session_id,
                    InterviewTurn.eval_scores.isnot(None),
                )
            )
            rows = result.all()
            if not rows:
                return

            # Compute per-dimension averages
            dims = ["accuracy", "depth", "completeness", "clarity",
                    "maturity", "ownership", "correctness"]
            totals: dict[str, list[float]] = {d: [] for d in dims}
            pressure_turns = 0

            for scores_dict, mode in rows:
                if not isinstance(scores_dict, dict):
                    continue
                for dim in dims:
                    if dim in scores_dict:
                        totals[dim].append(float(scores_dict[dim]))
                if mode in ("PRESSURE", "ESCALATING"):
                    pressure_turns += 1

            def avg(lst: list[float]) -> float | None:
                return round(sum(lst) / len(lst), 2) if lst else None

            all_scores = [v for lst in totals.values() for v in lst]
            overall = avg(all_scores)

            # Determine hiring signal
            hiring_signal = None
            if overall is not None:
                if overall >= 7.5:
                    hiring_signal = "strong"
                elif overall >= 6.0:
                    hiring_signal = "moderate"
                elif overall >= 4.5:
                    hiring_signal = "weak"
                else:
                    hiring_signal = "no"

            pressure_pct = (pressure_turns / len(rows) * 100) if rows else 0

            report = await db.scalar(
                select(SessionReport).where(SessionReport.session_id == session_id)
            )
            if not report:
                report = SessionReport(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    review_status="pending",
                )
                db.add(report)

            report.avg_accuracy    = avg(totals["accuracy"])
            report.avg_depth       = avg(totals["depth"])
            report.avg_completeness= avg(totals["completeness"])
            report.avg_clarity     = avg(totals["clarity"])
            report.avg_maturity    = avg(totals["maturity"])
            report.avg_ownership   = avg(totals["ownership"])
            report.avg_correctness = avg(totals["correctness"])
            report.overall_score   = overall
            report.hiring_signal   = hiring_signal
            report.pressure_turns_pct = round(pressure_pct, 1)
            report.generated_at    = datetime.utcnow()

        log.info("db.report_finalized",
                 session_id=session_id, overall_score=overall,
                 hiring_signal=hiring_signal)

    except Exception as exc:
        log.error("db.finalize_report_failed",
                  session_id=session_id, error=str(exc))


# ══════════════════════════════════════════════════════════════════════
# INTEGRITY RECORD PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

async def persist_integrity(
    session_id: str,
    result: Any,  # IntegrityResult from anti_cheat.py
) -> None:
    """
    Write the computed integrity result to Postgres.
    Called from session end as a background task.
    """
    try:
        async with db_session() as db:
            existing = await db.scalar(
                select(IntegrityRecord).where(
                    IntegrityRecord.session_id == session_id
                )
            )
            if existing:
                # Update existing record (shouldn't happen normally)
                record = existing
            else:
                record = IntegrityRecord(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                )
                db.add(record)

            record.integrity_score = result.integrity_score
            record.confidence = result.confidence
            record.tab_switch_count = result.tab_switch_count
            record.clipboard_event_count = result.clipboard_event_count
            record.devtools_detected = result.devtools_detected
            record.focus_loss_count = result.focus_loss_count
            record.long_pause_count = result.long_pause_count
            record.ai_pattern_score = result.ai_pattern_score
            record.vocabulary_diversity = result.vocabulary_diversity
            record.answer_speed_anomaly = result.answer_speed_anomaly
            record.event_log = result.event_log
            record.requires_review = result.requires_review

        log.info("db.integrity_persisted",
                 session_id=session_id,
                 score=result.integrity_score,
                 requires_review=result.requires_review)

    except Exception as exc:
        log.error("db.integrity_failed", session_id=session_id, error=str(exc))


# ══════════════════════════════════════════════════════════════════════
# OPERATIONAL METRICS PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

async def persist_metric_batch(metrics: list[dict]) -> None:
    """
    Bulk-write a batch of metrics to OperationalMetric.
    Called from the metrics flush loop every 5 seconds.

    Designed for high throughput: bulk insert, no upsert needed
    (metrics are append-only and idempotent by design).
    """
    if not metrics:
        return

    try:
        async with db_session() as db:
            rows = []
            for m in metrics:
                metric_type = m.get("name") or m.get("type") or "unknown"
                rows.append(OperationalMetric(
                    id=str(uuid.uuid4()),
                    session_id=m.get("session_id"),
                    turn_number=m.get("turn_number") or m.get("turn"),
                    metric_type=metric_type,
                    value_ms=m.get("stt_latency_ms") or m.get("latency_ms") or m.get("value_ms"),
                    value_float=m.get("avg_score") or m.get("value_float"),
                    value_int=m.get("tts_chunk_count") or m.get("value_int"),
                    value_json={k: v for k, v in m.items()
                                if k not in ("session_id", "turn_number", "type", "name",
                                            "stt_latency_ms", "latency_ms", "value_ms",
                                            "avg_score", "value_float", "tts_chunk_count")
                                and v is not None},
                    provider=m.get("provider"),
                ))
            db.add_all(rows)

        log.debug("db.metrics_flushed", count=len(rows))

    except Exception as exc:
        log.error("db.metrics_batch_failed", count=len(metrics), error=str(exc))


async def persist_system_event(
    event_type: str,
    severity: str = "info",
    message: str | None = None,
    session_id: str | None = None,
    context: dict | None = None,
) -> None:
    """
    Write a system event to SystemEvent.
    For ws reconnects, provider failures, SLA violations, etc.
    """
    try:
        async with db_session() as db:
            db.add(SystemEvent(
                id=str(uuid.uuid4()),
                session_id=session_id,
                event_type=event_type,
                severity=severity,
                message=message,
                context=context,
            ))
    except Exception as exc:
        log.error("db.system_event_failed",
                  event_type=event_type, error=str(exc))


async def get_active_system_prompt(prompt_type: str) -> str | None:
    """
    Fetch the active prompt content for a given type from Postgres.
    Returns None if no active version exists (caller uses hardcoded default).

    Called by the Redis-cached wrapper in core/prompt_cache.py — not directly
    from the hot path.
    """
    try:
        from app.db.models import PromptVersion
        async with db_session() as db:
            pv = await db.scalar(
                select(PromptVersion).where(
                    PromptVersion.prompt_type == prompt_type,
                    PromptVersion.is_active == True,
                )
            )
            return pv.content if pv else None
    except Exception as exc:
        log.error("db.get_active_prompt_failed",
                  prompt_type=prompt_type, error=str(exc))
        return None
