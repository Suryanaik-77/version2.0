"""
api/reviewer.py — Expert reviewer endpoints.

Access: reviewer + admin roles.
Focus: session review workflow, notes, score overrides, flagging.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenPayload, require_reviewer
from app.db.models import (
    IntegrityRecord, InterviewSession, InterviewTurn,
    ReviewerNote, SessionFlag, SessionReport,
)
from app.db.session import get_db

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/reviewer", tags=["reviewer"])

ReviewerUser = Annotated[TokenPayload, Depends(require_reviewer)]
DB = Annotated[AsyncSession, Depends(get_db)]


# ── Review queue ──────────────────────────────────────────────────────────────

@router.get("/queue")
async def review_queue(
    current_user: ReviewerUser,
    db: DB,
    status: str = Query("pending"),
    domain: str | None = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
) -> dict:
    """
    Sessions awaiting expert review. Returns sessions with completed
    AI evaluation that haven't been human-reviewed yet.
    """
    q = (
        select(InterviewSession, SessionReport)
        .join(SessionReport, SessionReport.session_id == InterviewSession.id)
        .where(
            InterviewSession.status == "completed",
            SessionReport.review_status == status,
        )
        .order_by(desc(InterviewSession.ended_at))
    )

    if domain:
        q = q.where(InterviewSession.domain == domain)

    from sqlalchemy import func
    total = await db.scalar(
        select(func.count()).select_from(
            select(InterviewSession.id)
            .join(SessionReport)
            .where(InterviewSession.status == "completed",
                   SessionReport.review_status == status)
            .subquery()
        )
    )

    result = await db.execute(q.limit(limit).offset(offset))
    rows = result.all()

    return {
        "total": total,
        "offset": offset,
        "queue": [
            {
                "session_id": session.id,
                "candidate_id": session.candidate_id,
                "domain": session.domain,
                "ended_at": session.ended_at,
                "total_turns": session.total_turns,
                "overall_score": report.overall_score,
                "hiring_signal": report.hiring_signal,
                "review_status": report.review_status,
            }
            for session, report in rows
        ],
    }


# ── Full session transcript ───────────────────────────────────────────────────

@router.get("/sessions/{session_id}/transcript")
async def get_transcript(
    session_id: str,
    _: ReviewerUser,
    db: DB,
) -> dict:
    """Full Q&A transcript with eval scores per turn."""
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    turns_result = await db.execute(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == session_id)
        .order_by(InterviewTurn.turn_number)
    )
    turns = turns_result.scalars().all()

    report = await db.scalar(
        select(SessionReport).where(SessionReport.session_id == session_id)
    )

    notes_result = await db.execute(
        select(ReviewerNote).where(ReviewerNote.session_id == session_id)
        .order_by(ReviewerNote.created_at)
    )
    notes = notes_result.scalars().all()

    return {
        "session": {
            "id": session.id,
            "domain": session.domain,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "duration_secs": session.duration_secs,
            "total_turns": session.total_turns,
        },
        "report": {
            "overall_score": report.overall_score if report else None,
            "hiring_signal": report.hiring_signal if report else None,
            "strength_summary": report.strength_summary if report else None,
            "weakness_summary": report.weakness_summary if report else None,
            "dimension_scores": {
                "accuracy": report.avg_accuracy,
                "depth": report.avg_depth,
                "completeness": report.avg_completeness,
                "clarity": report.avg_clarity,
                "maturity": report.avg_maturity,
                "ownership": report.avg_ownership,
                "correctness": report.avg_correctness,
            } if report else None,
            "review_status": report.review_status if report else "pending",
        },
        "turns": [
            {
                "turn_number": t.turn_number,
                "question": t.question_text,
                "answer": t.answer_text,
                "domain": t.domain,
                "mode": t.mode_at_start,
                "eval_scores": t.eval_scores,
                "avg_score": t.avg_eval_score,
                "signals": t.signals,
            }
            for t in turns
        ],
        "notes": [
            {
                "id": n.id,
                "turn_number": n.turn_number,
                "note_text": n.note_text,
                "note_type": n.note_type,
                "reviewer_id": n.reviewer_id,
                "created_at": n.created_at,
            }
            for n in notes
        ],
    }


# ── Integrity view ────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/integrity")
async def get_integrity(
    session_id: str,
    _: ReviewerUser,
    db: DB,
) -> dict:
    """Anti-cheat signals for reviewer visibility."""
    record = await db.scalar(
        select(IntegrityRecord).where(IntegrityRecord.session_id == session_id)
    )
    if not record:
        return {"session_id": session_id, "no_data": True}

    return {
        "session_id": session_id,
        "integrity_score": record.integrity_score,
        "confidence": record.confidence,
        "summary": {
            "tab_switches": record.tab_switch_count,
            "clipboard_events": record.clipboard_event_count,
            "devtools_detected": record.devtools_detected,
            "focus_losses": record.focus_loss_count,
            "long_pauses": record.long_pause_count,
        },
        "ai_signals": {
            "pattern_score": record.ai_pattern_score,
            "vocabulary_diversity": record.vocabulary_diversity,
            "answer_speed_anomaly": record.answer_speed_anomaly,
        },
        "event_log": record.event_log or [],
        "verdict": record.reviewer_verdict,
    }


# ── Notes ─────────────────────────────────────────────────────────────────────

class AddNoteRequest(BaseModel):
    note_text: str
    note_type: str = "general"
    turn_number: int | None = None
    is_visible_to_candidate: bool = False


@router.post("/sessions/{session_id}/notes", status_code=201)
async def add_note(
    session_id: str,
    body: AddNoteRequest,
    current_user: ReviewerUser,
    db: DB,
) -> dict:
    """Add a reviewer note to a session or specific turn."""
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    note = ReviewerNote(
        session_id=session_id,
        reviewer_id=current_user.sub,
        turn_number=body.turn_number,
        note_text=body.note_text,
        note_type=body.note_type,
        is_visible_to_candidate=body.is_visible_to_candidate,
    )
    db.add(note)
    await db.flush()

    return {
        "id": note.id,
        "session_id": session_id,
        "turn_number": note.turn_number,
        "note_type": note.note_type,
        "created_at": note.created_at,
    }


@router.delete("/notes/{note_id}", status_code=204, response_class=Response)
async def delete_note(
    note_id: str,
    current_user: ReviewerUser,
    db: DB,
) -> Response:
    note = await db.get(ReviewerNote, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.reviewer_id != current_user.sub:
        raise HTTPException(status_code=403, detail="Can only delete your own notes")
    await db.delete(note)
    return Response(status_code=204)


# ── Score override ────────────────────────────────────────────────────────────

class ScoreOverrideRequest(BaseModel):
    accuracy: float | None = None
    depth: float | None = None
    correctness: float | None = None
    maturity: float | None = None
    overall: float | None = None
    hiring_signal: str | None = None
    justification: str


@router.post("/sessions/{session_id}/override")
async def override_scores(
    session_id: str,
    body: ScoreOverrideRequest,
    current_user: ReviewerUser,
    db: DB,
) -> dict:
    """Expert reviewer override of AI-generated evaluation scores."""
    report = await db.scalar(
        select(SessionReport).where(SessionReport.session_id == session_id)
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    override_data = body.model_dump(exclude_none=True)
    override_data["overridden_by"] = current_user.sub
    override_data["overridden_at"] = datetime.utcnow().isoformat()

    report.reviewer_override = override_data
    report.reviewed_by_id = current_user.sub
    report.reviewed_at = datetime.utcnow()
    report.review_status = "reviewed"

    if body.overall is not None:
        report.overall_score = body.overall
    if body.hiring_signal:
        report.hiring_signal = body.hiring_signal

    log.info("reviewer.override", session_id=session_id, reviewer=current_user.sub)

    return {"session_id": session_id, "status": "reviewed", "override": override_data}


# ── Flagging ──────────────────────────────────────────────────────────────────

class FlagRequest(BaseModel):
    flag_type: str  # "integrity", "quality", "technical"
    reason: str


@router.post("/sessions/{session_id}/flag", status_code=201)
async def flag_session(
    session_id: str,
    body: FlagRequest,
    current_user: ReviewerUser,
    db: DB,
) -> dict:
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    flag = SessionFlag(
        session_id=session_id,
        flagged_by_id=current_user.sub,
        flag_type=body.flag_type,
        reason=body.reason,
    )
    db.add(flag)
    await db.flush()

    log.info("reviewer.flag", session_id=session_id, flag_type=body.flag_type,
             reviewer=current_user.sub)

    return {"id": flag.id, "flag_type": flag.flag_type, "session_id": session_id}


# ── Mark review complete ──────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/approve")
async def approve_review(
    session_id: str,
    current_user: ReviewerUser,
    db: DB,
) -> dict:
    """Mark the session review as approved (no changes needed)."""
    report = await db.scalar(
        select(SessionReport).where(SessionReport.session_id == session_id)
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report.review_status = "approved"
    report.reviewed_by_id = current_user.sub
    report.reviewed_at = datetime.utcnow()

    return {"session_id": session_id, "review_status": "approved"}
