"""
gateway.py — REST API routes.

Route contract (matches frontend api.ts exactly):
  POST   /sessions                      → create session
  GET    /sessions                      → list candidate's sessions
  GET    /sessions/{id}                 → get session state (live, from Redis)
  GET    /sessions/{id}/report          → get full report (from Postgres)
  DELETE /sessions/{id}                 → end session (admin only)
  GET    /health                        → health check

Security: the insecure open token issuance endpoint has been removed.
All auth goes through /auth/login, /auth/register.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.auth import TokenPayload, get_current_user, require_admin
from app.config import get_settings
from app.core.session import create_session, get_session, end_session, SessionNotFoundError
from app.models.session import SessionState, VLSIDomain, EndReason, SessionSummary

settings = get_settings()

# No /api/v1 prefix — frontend calls /sessions directly
router = APIRouter()


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.APP_NAME}


# ── Session management ────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    domain: VLSIDomain
    resume_text: str = ""  # raw resume text from frontend upload


class CreateSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    domain: str


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session_endpoint(
    request: Request,
    body: CreateSessionRequest,
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> CreateSessionResponse:
    """
    Create a new interview session for the authenticated candidate.
    Returns session_id and the WebSocket URL to connect to.
    """
    # Parse resume if provided
    resume_data = None
    if body.resume_text:
        from app.engines.resume_parser import parse_resume
        resume_data = await parse_resume(body.resume_text, body.domain.value)

    state = await create_session(
        candidate_id=user.sub,
        domain=body.domain,
        resume_data=resume_data,
    )

    # Construct WS URL — client appends ?token= from their stored access token
    base_url = str(request.base_url).rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{base_url}/ws/{state.session_id}"

    return CreateSessionResponse(
        session_id=state.session_id,
        ws_url=ws_url,
        domain=state.active_domain.value,
    )


@router.get("/sessions")
async def list_sessions_endpoint(
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> dict:
    """
    Return completed session summaries for the authenticated candidate.
    Reads from Postgres InterviewSession table (not Redis — only persisted sessions).
    """
    try:
        from app.db.session import db_session as db_ctx
        from app.db.models import InterviewSession, SessionReport
        from sqlalchemy import select, desc

        async with db_ctx() as db:
            result = await db.execute(
                select(InterviewSession, SessionReport)
                .outerjoin(SessionReport, SessionReport.session_id == InterviewSession.id)
                .where(InterviewSession.candidate_id == user.sub)
                .order_by(desc(InterviewSession.started_at))
                .limit(50)
            )
            rows = result.all()

        sessions = []
        for session, report in rows:
            sessions.append({
                "id": session.id,
                "domain": session.domain,
                "status": session.status,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "total_turns": session.total_turns,
                "avg_score": session.avg_score,
                "total_cost_usd": session.total_cost_usd,
                "report": {
                    "hiring_signal": report.hiring_signal,
                    "overall_score": report.overall_score,
                    "review_status": report.review_status,
                } if report else None,
            })

        return {"sessions": sessions, "total": len(sessions)}

    except Exception as exc:
        import structlog
        log = structlog.get_logger(__name__)
        log.warning("gateway.list_sessions_failed", user=user.sub, error=str(exc))
        return {"sessions": [], "total": 0}


@router.get("/sessions/{session_id}")
async def get_session_endpoint(
    session_id: str,
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> dict:
    """
    Return live session state from Redis.
    Used by InterviewPage to get domain and connection info.
    """
    state = await get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": state.session_id,
        "domain": state.active_domain.value,
        "mode": state.mode.value,
        "turn_count": state.turn_count,
        "is_active": state.is_active,
        "started_at": state.started_at.isoformat() if state.started_at else None,
    }


@router.get("/sessions/{session_id}/report")
async def get_session_report_endpoint(
    session_id: str,
    user: Annotated[TokenPayload, Depends(get_current_user)],
) -> dict:
    """
    Return the full session report from Postgres.
    Includes session metadata, dimension scores, and full turn transcript.
    Called by ReportPage.

    Returns 404 if the session hasn't been persisted yet (still live or flush pending).
    """
    try:
        from app.db.session import db_session as db_ctx
        from app.db.models import InterviewSession, InterviewTurn, SessionReport

        async with db_ctx() as db:
            session = await db.get(InterviewSession, session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found or not yet archived")

            # Enforce ownership — candidates can only see their own reports
            if user.role.value == "candidate" and session.candidate_id != user.sub:
                raise HTTPException(status_code=403, detail="Access denied")

            report = await db.scalar(
                select(SessionReport).where(SessionReport.session_id == session_id)
            )

            turns_result = await db.execute(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.turn_number)
            )
            turns = turns_result.scalars().all()

        return {
            "session": {
                "id": session.id,
                "domain": session.domain,
                "status": session.status,
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "duration_secs": session.duration_secs,
                "total_turns": session.total_turns,
                "avg_score": session.avg_score,
            },
            "report": {
                "overall_score": report.overall_score if report else None,
                "hiring_signal": report.hiring_signal if report else None,
                "strength_summary": report.strength_summary if report else None,
                "weakness_summary": report.weakness_summary if report else None,
                "review_status": report.review_status if report else "pending",
                "dimension_scores": {
                    "accuracy":     report.avg_accuracy,
                    "depth":        report.avg_depth,
                    "completeness": report.avg_completeness,
                    "clarity":      report.avg_clarity,
                    "maturity":     report.avg_maturity,
                    "ownership":    report.avg_ownership,
                    "correctness":  report.avg_correctness,
                } if report else None,
            } if report else None,
            "turns": [
                {
                    "turn_number": t.turn_number,
                    "question": t.question_text,
                    "answer": t.answer_text,
                    "mode": t.mode_at_start,
                    "eval_scores": t.eval_scores,
                    "avg_score": t.avg_eval_score,
                    "signals": t.signals,
                }
                for t in turns
            ],
        }

    except HTTPException:
        raise
    except Exception as exc:
        import structlog
        structlog.get_logger(__name__).error(
            "gateway.report_failed", session_id=session_id, error=str(exc)
        )
        raise HTTPException(status_code=500, detail="Report unavailable")


@router.delete("/sessions/{session_id}", response_model=SessionSummary)
async def end_session_endpoint(
    session_id: str,
    user: Annotated[TokenPayload, Depends(require_admin)],
) -> SessionSummary:
    try:
        return await end_session(session_id, EndReason.ADMIN_TERMINATE)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
