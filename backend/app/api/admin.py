"""
api/admin.py — Admin-only endpoints.

All routes require UserRole.ADMIN.
Design: read-heavy, async queries, paginated.
Never touches the hot path.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenPayload, require_admin
from app.db.models import (
    IntegrityRecord, InterviewSession, InterviewTurn,
    OperationalMetric, PromptVersion, SessionReport,
    SystemEvent, User,
)
from app.db.session import get_db
from app.core import redis as r_core
from app.core import runtime_config as rc

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

AdminUser = Annotated[TokenPayload, Depends(require_admin)]
DB = Annotated[AsyncSession, Depends(get_db)]


# ── Dashboard summary ──────────────────────────────────────────────────────────

class DashboardSummary(BaseModel):
    active_sessions: int
    sessions_today: int
    sessions_week: int
    total_users: int
    avg_score_week: float | None
    total_cost_today_usd: float
    p50_first_token_ms: int | None
    p95_first_token_ms: int | None
    ws_reconnects_today: int
    error_count_today: int


@router.get("/dashboard", response_model=DashboardSummary)
async def admin_dashboard(
    _: AdminUser,
    db: DB,
) -> DashboardSummary:
    """Main admin dashboard summary."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    # Active sessions from Redis
    active_keys = await r_core.scan_sessions()
    active_count = len(active_keys)

    # DB queries
    sessions_today = await db.scalar(
        select(func.count(InterviewSession.id)).where(
            InterviewSession.started_at >= today_start
        )
    ) or 0

    sessions_week = await db.scalar(
        select(func.count(InterviewSession.id)).where(
            InterviewSession.started_at >= week_start
        )
    ) or 0

    total_users = await db.scalar(
        select(func.count(User.id)).where(User.deleted_at.is_(None))
    ) or 0

    avg_score = await db.scalar(
        select(func.avg(SessionReport.overall_score)).where(
            SessionReport.created_at >= week_start
        )
    )

    total_cost = await db.scalar(
        select(func.sum(InterviewSession.total_cost_usd)).where(
            InterviewSession.started_at >= today_start
        )
    ) or 0.0

    # Latency metrics
    latency_rows = await db.execute(
        select(OperationalMetric.value_ms).where(
            OperationalMetric.metric_type == "first_token_ms",
            OperationalMetric.recorded_at >= today_start,
        ).order_by(OperationalMetric.value_ms)
    )
    latencies = [r[0] for r in latency_rows if r[0] is not None]
    p50 = latencies[len(latencies) // 2] if latencies else None
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else None

    # WS reconnects
    ws_reconnects = await db.scalar(
        select(func.count(SystemEvent.id)).where(
            SystemEvent.event_type == "ws_reconnect",
            SystemEvent.recorded_at >= today_start,
        )
    ) or 0

    error_count = await db.scalar(
        select(func.count(SystemEvent.id)).where(
            SystemEvent.severity == "error",
            SystemEvent.recorded_at >= today_start,
        )
    ) or 0

    return DashboardSummary(
        active_sessions=active_count,
        sessions_today=sessions_today,
        sessions_week=sessions_week,
        total_users=total_users,
        avg_score_week=round(avg_score, 2) if avg_score else None,
        total_cost_today_usd=round(total_cost, 4),
        p50_first_token_ms=p50,
        p95_first_token_ms=p95,
        ws_reconnects_today=ws_reconnects,
        error_count_today=error_count,
    )


# ── Active session monitoring ─────────────────────────────────────────────────

@router.get("/sessions/active")
async def active_sessions(
    _: AdminUser,
) -> list[dict]:
    """Live view of all active WebSocket sessions (from Redis)."""
    sessions = await r_core.get_all_active_sessions()
    return sessions


@router.get("/sessions")
async def list_sessions(
    _: AdminUser,
    db: DB,
    status: str | None = Query(None),
    domain: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> dict:
    """Paginated session list with optional filters."""
    q = select(InterviewSession).order_by(desc(InterviewSession.started_at))

    if status:
        q = q.where(InterviewSession.status == status)
    if domain:
        q = q.where(InterviewSession.domain == domain)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.limit(limit).offset(offset))
    sessions = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "sessions": [
            {
                "id": s.id,
                "candidate_id": s.candidate_id,
                "domain": s.domain,
                "status": s.status,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "total_turns": s.total_turns,
                "avg_score": s.avg_score,
                "total_cost_usd": s.total_cost_usd,
                "ws_reconnects": s.ws_reconnects,
            }
            for s in sessions
        ],
    }


@router.get("/sessions/{session_id}/detail")
async def session_detail(
    session_id: str,
    _: AdminUser,
    db: DB,
) -> dict:
    """Full session detail including all turns."""
    session = await db.get(InterviewSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    turns_result = await db.execute(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == session_id)
        .order_by(InterviewTurn.turn_number)
    )
    turns = turns_result.scalars().all()

    return {
        "session": {
            "id": session.id,
            "candidate_id": session.candidate_id,
            "domain": session.domain,
            "status": session.status,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "duration_secs": session.duration_secs,
            "total_turns": session.total_turns,
            "avg_score": session.avg_score,
            "total_tokens_in": session.total_tokens_in,
            "total_tokens_out": session.total_tokens_out,
            "total_cost_usd": session.total_cost_usd,
            "ws_reconnects": session.ws_reconnects,
        },
        "turns": [
            {
                "turn_number": t.turn_number,
                "question_text": t.question_text,
                "answer_text": t.answer_text,
                "mode_at_start": t.mode_at_start,
                "mode_at_end": t.mode_at_end,
                "eval_scores": t.eval_scores,
                "avg_eval_score": t.avg_eval_score,
                "latency": {
                    "stt_ms": t.stt_latency_ms,
                    "first_token_ms": t.first_token_ms,
                    "first_audio_ms": t.first_audio_ms,
                    "total_ms": t.turn_total_ms,
                },
                "cost_usd": t.cost_usd,
            }
            for t in turns
        ],
    }


# ── Metrics & Analytics ───────────────────────────────────────────────────────

@router.get("/metrics/latency")
async def latency_metrics(
    _: AdminUser,
    db: DB,
    metric_type: str = Query("first_token_ms"),
    hours: int = Query(24, le=168),
) -> dict:
    """Latency percentiles over the last N hours."""
    since = datetime.utcnow() - timedelta(hours=hours)

    result = await db.execute(
        select(OperationalMetric.value_ms, OperationalMetric.recorded_at)
        .where(
            OperationalMetric.metric_type == metric_type,
            OperationalMetric.recorded_at >= since,
            OperationalMetric.value_ms.isnot(None),
        )
        .order_by(OperationalMetric.value_ms)
    )
    rows = result.all()
    values = [r[0] for r in rows]

    if not values:
        return {"metric_type": metric_type, "count": 0, "p50": None, "p95": None, "p99": None}

    n = len(values)
    return {
        "metric_type": metric_type,
        "hours": hours,
        "count": n,
        "p50": values[n // 2],
        "p95": values[int(n * 0.95)],
        "p99": values[int(n * 0.99)],
        "min": values[0],
        "max": values[-1],
        "avg": int(sum(values) / n),
    }


@router.get("/metrics/cost")
async def cost_metrics(
    _: AdminUser,
    db: DB,
    days: int = Query(7, le=30),
) -> dict:
    """Token cost breakdown by day."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            func.date_trunc("day", InterviewSession.started_at).label("day"),
            func.sum(InterviewSession.total_cost_usd).label("cost"),
            func.sum(InterviewSession.total_tokens_in).label("tokens_in"),
            func.sum(InterviewSession.total_tokens_out).label("tokens_out"),
            func.count(InterviewSession.id).label("sessions"),
        )
        .where(InterviewSession.started_at >= since)
        .group_by("day")
        .order_by("day")
    )
    rows = result.all()

    return {
        "days": days,
        "daily": [
            {
                "date": str(r.day)[:10] if r.day else None,
                "cost_usd": round(r.cost or 0, 4),
                "tokens_in": r.tokens_in or 0,
                "tokens_out": r.tokens_out or 0,
                "sessions": r.sessions or 0,
            }
            for r in rows
        ],
    }


@router.get("/metrics/scores")
async def score_metrics(
    _: AdminUser,
    db: DB,
    days: int = Query(30, le=90),
    domain: str | None = Query(None),
) -> dict:
    """Score distribution across sessions."""
    since = datetime.utcnow() - timedelta(days=days)

    q = select(
        func.avg(SessionReport.avg_accuracy).label("avg_accuracy"),
        func.avg(SessionReport.avg_depth).label("avg_depth"),
        func.avg(SessionReport.avg_correctness).label("avg_correctness"),
        func.avg(SessionReport.avg_maturity).label("avg_maturity"),
        func.avg(SessionReport.overall_score).label("overall"),
        func.count(SessionReport.id).label("count"),
    ).where(SessionReport.created_at >= since)

    if domain:
        q = q.join(InterviewSession).where(InterviewSession.domain == domain)

    result = await db.execute(q)
    row = result.one_or_none()

    return {
        "days": days,
        "domain": domain,
        "count": row.count if row else 0,
        "averages": {
            "accuracy": round(row.avg_accuracy or 0, 2),
            "depth": round(row.avg_depth or 0, 2),
            "correctness": round(row.avg_correctness or 0, 2),
            "maturity": round(row.avg_maturity or 0, 2),
            "overall": round(row.overall or 0, 2),
        } if row else {},
    }


# ── User Management ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    _: AdminUser,
    db: DB,
    role: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
) -> dict:
    q = select(User).where(User.deleted_at.is_(None))
    if role:
        q = q.where(User.role == role)
    q = q.order_by(desc(User.created_at))

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.limit(limit).offset(offset))
    users = result.scalars().all()

    return {
        "total": total,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
            }
            for u in users
        ],
    }


@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: str,
    current_user: AdminUser,
    db: DB,
) -> dict:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.sub:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user.is_active = not user.is_active
    log.info("admin.toggle_user", admin=current_user.sub, target=user_id,
             new_state=user.is_active)

    return {"id": user.id, "is_active": user.is_active}


# ── System Events ─────────────────────────────────────────────────────────────

@router.get("/events")
async def system_events(
    _: AdminUser,
    db: DB,
    severity: str | None = Query(None),
    event_type: str | None = Query(None),
    hours: int = Query(24, le=168),
    limit: int = Query(100, le=500),
) -> dict:
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (
        select(SystemEvent)
        .where(SystemEvent.recorded_at >= since)
        .order_by(desc(SystemEvent.recorded_at))
    )
    if severity:
        q = q.where(SystemEvent.severity == severity)
    if event_type:
        q = q.where(SystemEvent.event_type == event_type)

    result = await db.execute(q.limit(limit))
    events = result.scalars().all()

    return {
        "events": [
            {
                "id": e.id,
                "session_id": e.session_id,
                "event_type": e.event_type,
                "severity": e.severity,
                "message": e.message,
                "context": e.context,
                "recorded_at": e.recorded_at,
            }
            for e in events
        ]
    }


# ── Prompt Versioning ─────────────────────────────────────────────────────────

@router.get("/prompts")
async def list_prompts(
    _: AdminUser,
    db: DB,
    prompt_type: str | None = Query(None),
) -> list[dict]:
    q = select(PromptVersion).order_by(
        PromptVersion.prompt_type,
        desc(PromptVersion.version_number),
    )
    if prompt_type:
        q = q.where(PromptVersion.prompt_type == prompt_type)

    result = await db.execute(q)
    prompts = result.scalars().all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "prompt_type": p.prompt_type,
            "version_number": p.version_number,
            "is_active": p.is_active,
            "created_at": p.created_at,
            "notes": p.notes,
        }
        for p in prompts
    ]


class PromptCreateRequest(BaseModel):
    name: str
    prompt_type: str
    content: str
    notes: str | None = None


@router.post("/prompts", status_code=201)
async def create_prompt_version(
    body: PromptCreateRequest,
    current_user: AdminUser,
    db: DB,
) -> dict:
    """Create a new prompt version (not yet active)."""
    max_version = await db.scalar(
        select(func.max(PromptVersion.version_number)).where(
            PromptVersion.prompt_type == body.prompt_type
        )
    ) or 0

    pv = PromptVersion(
        name=body.name,
        prompt_type=body.prompt_type,
        content=body.content,
        version_number=max_version + 1,
        is_active=False,
        created_by_id=current_user.sub,
        notes=body.notes,
    )
    db.add(pv)
    await db.flush()

    log.info("admin.prompt_created", prompt_type=body.prompt_type,
             version=pv.version_number, admin=current_user.sub)

    return {"id": pv.id, "version_number": pv.version_number, "is_active": False}


@router.post("/prompts/{prompt_id}/activate")
async def activate_prompt(
    prompt_id: str,
    current_user: AdminUser,
    db: DB,
) -> dict:
    """Activate a prompt version (deactivates current active version)."""
    target = await db.get(PromptVersion, prompt_id)
    if not target:
        raise HTTPException(status_code=404, detail="Prompt version not found")

    # Deactivate currently active version of this type
    current_active_result = await db.execute(
        select(PromptVersion).where(
            PromptVersion.prompt_type == target.prompt_type,
            PromptVersion.is_active == True,
        )
    )
    for pv in current_active_result.scalars().all():
        pv.is_active = False

    target.is_active = True

    # Invalidate Redis cache so live interviews pick up the new prompt
    # within one cache TTL (30s). The invalidation makes it immediate.
    from app.core.prompt_cache import invalidate_prompt_cache
    await invalidate_prompt_cache(target.prompt_type)

    log.info("admin.prompt_activated", prompt_id=prompt_id,
             prompt_type=target.prompt_type, admin=current_user.sub)

    return {"id": target.id, "version_number": target.version_number, "is_active": True}


@router.get("/prompts/{prompt_id}/content")
async def get_prompt_content(
    prompt_id: str,
    _: AdminUser,
    db: DB,
) -> dict:
    pv = await db.get(PromptVersion, prompt_id)
    if not pv:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"id": pv.id, "content": pv.content, "version_number": pv.version_number}


# ── Anti-cheat admin view ─────────────────────────────────────────────────────

@router.get("/integrity")
async def integrity_overview(
    _: AdminUser,
    db: DB,
    requires_review: bool = Query(True),
    limit: int = Query(50, le=200),
) -> list[dict]:
    q = (
        select(IntegrityRecord)
        .order_by(IntegrityRecord.created_at.desc())
    )
    if requires_review:
        q = q.where(IntegrityRecord.requires_review == True)

    result = await db.execute(q.limit(limit))
    records = result.scalars().all()

    return [
        {
            "session_id": r.session_id,
            "integrity_score": r.integrity_score,
            "confidence": r.confidence,
            "tab_switch_count": r.tab_switch_count,
            "clipboard_events": r.clipboard_event_count,
            "devtools_detected": r.devtools_detected,
            "ai_pattern_score": r.ai_pattern_score,
            "requires_review": r.requires_review,
            "reviewer_verdict": r.reviewer_verdict,
        }
        for r in records
    ]


# ══════════════════════════════════════════════════════════════════════════════
# LLM Configuration
# ══════════════════════════════════════════════════════════════════════════════

AVAILABLE_MODELS = [
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "tier": "fast", "cost": "$0.15/$0.60 per 1M"},
    {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "name": "Claude Haiku 4.5", "tier": "fast", "cost": "$1.00/$5.00 per 1M"},
    {"id": "us.anthropic.claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "tier": "balanced", "cost": "$3.00/$15.00 per 1M"},
    {"id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "Claude Sonnet 4.5", "tier": "balanced", "cost": "$3.00/$15.00 per 1M"},
    {"id": "us.anthropic.claude-opus-4-6-v1", "name": "Claude Opus 4.6", "tier": "premium", "cost": "$15.00/$75.00 per 1M"},
    {"id": "grok-4.3", "name": "Grok 4.3", "tier": "balanced", "cost": "$1.25/$2.50 per 1M"},
    {"id": "grok-4-1-fast-non-reasoning", "name": "Grok 4.1 Fast", "tier": "fast", "cost": "$0.20/$0.50 per 1M"},
    {"id": "us.meta.llama4-maverick-17b-instruct-v1:0", "name": "Llama 4 Maverick 17B", "tier": "fast", "cost": "$0.17/$0.17 per 1M"},
    {"id": "us.amazon.nova-lite-v1:0", "name": "Amazon Nova Lite", "tier": "fast", "cost": "$0.06/$0.24 per 1M"},
]


@router.get("/llm-config")
async def get_llm_config(_: AdminUser) -> dict:
    return {
        "qgen_model": rc.get("qgen_model"),
        "eval_model": rc.get("eval_model"),
        "available_models": AVAILABLE_MODELS,
    }


class LLMConfigUpdate(BaseModel):
    qgen_model: str | None = None
    eval_model: str | None = None


@router.post("/llm-config")
async def set_llm_config(body: LLMConfigUpdate, _: AdminUser) -> dict:
    updates = {}
    if body.qgen_model is not None:
        updates["qgen_model"] = body.qgen_model
    if body.eval_model is not None:
        updates["eval_model"] = body.eval_model
    if updates:
        await rc.set_many(updates)
        log.info("admin.llm_config_updated", **updates)
    return {"status": "success", **rc.get_all()}


# ══════════════════════════════════════════════════════════════════════════════
# Voice Configuration
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/voice-config")
async def get_voice_config(_: AdminUser) -> dict:
    return {
        "tts_enabled": rc.get("tts_enabled"),
        "tts_provider": rc.get("tts_provider"),
        "tts_voice": rc.get("tts_voice"),
        "stt_provider": rc.get("stt_provider"),
    }


class VoiceConfigUpdate(BaseModel):
    tts_enabled: bool | None = None
    tts_provider: str | None = None
    tts_voice: str | None = None
    stt_provider: str | None = None  # "openai" (batch) or "deepgram" (streaming)


@router.post("/voice-config")
async def set_voice_config(body: VoiceConfigUpdate, _: AdminUser) -> dict:
    updates = {}
    if body.tts_enabled is not None:
        updates["tts_enabled"] = body.tts_enabled
    if body.tts_provider is not None:
        updates["tts_provider"] = body.tts_provider
    if body.tts_voice is not None:
        updates["tts_voice"] = body.tts_voice
    if body.stt_provider is not None:
        updates["stt_provider"] = body.stt_provider
    if updates:
        await rc.set_many(updates)
        log.info("admin.voice_config_updated", **updates)
    return {"status": "success", **rc.get_all()}


# ══════════════════════════════════════════════════════════════════════════════
# TTS Test
# ══════════════════════════════════════════════════════════════════════════════

class TTSTestRequest(BaseModel):
    text: str
    provider: str = "openai"
    voice: str = "nova"


@router.post("/test-tts")
async def test_tts(body: TTSTestRequest, _: AdminUser) -> dict:
    """Test TTS: synthesize text and return base64 audio."""
    import base64
    import time

    # Temporarily set the provider/voice in runtime config
    await rc.set_many({"tts_provider": body.provider, "tts_voice": body.voice})

    t0 = time.time()
    try:
        from app.providers.tts import get_tts_provider
        tts = get_tts_provider()
        audio_bytes = await tts.synthesize(body.text, session_id="admin-test")
        latency_ms = int((time.time() - t0) * 1000)

        if not audio_bytes or len(audio_bytes) < 100:
            return {"status": "error", "error": "No audio generated", "latency_ms": latency_ms}

        return {
            "status": "success",
            "audio": base64.b64encode(audio_bytes).decode(),
            "format": tts.audio_format,
            "latency_ms": latency_ms,
            "provider": body.provider,
            "voice": body.voice,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "latency_ms": int((time.time() - t0) * 1000),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Playground
# ══════════════════════════════════════════════════════════════════════════════

class PlaygroundRequest(BaseModel):
    prompt: str
    model_id: str | None = None
    temperature: float = 0.3
    max_tokens: int = 600
    system_prompt: str | None = None


@router.post("/playground")
async def prompt_playground(body: PlaygroundRequest, _: AdminUser) -> dict:
    """Test a prompt against any configured LLM. Returns raw response."""
    import time
    model_id = body.model_id or rc.get("eval_model", "gpt-4o-mini")

    messages = []
    if body.system_prompt:
        messages.append({"role": "system", "content": body.system_prompt})
    messages.append({"role": "user", "content": body.prompt})

    t0 = time.time()
    try:
        from app.providers.llm import call_llm
        raw = await call_llm(
            messages=messages,
            model=model_id,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "status": "success",
            "response": raw,
            "model": model_id,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        log.error("playground.failed", model=model_id, error=str(e))
        return {
            "status": "error",
            "error": str(e),
            "model": model_id,
            "latency_ms": latency_ms,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Anti-Cheat Configuration
# ══════════════════════════════════════════════════════════════════════════════

# Default anti-cheat features
_ANTICHEAT_DEFAULTS = {
    "tab_switch_detection": True,
    "clipboard_monitoring": True,
    "devtools_detection": True,
    "split_screen_detection": True,
    "ai_extension_detection": True,
    "dom_overlay_detection": True,
    "window_blur_tracking": True,
    "behavioral_analysis": True,
    "pause_consistency_check": True,
    "answer_sophistication_check": True,
}


@router.get("/anticheat-config")
async def get_anticheat_config(_: AdminUser) -> dict:
    """Get current anti-cheat feature toggles."""
    stored = rc.get("anticheat_config")
    if stored and isinstance(stored, dict):
        config = {**_ANTICHEAT_DEFAULTS, **stored}
    else:
        config = dict(_ANTICHEAT_DEFAULTS)
    return {"features": config}


class AnticheatConfigUpdate(BaseModel):
    features: dict


@router.post("/anticheat-config")
async def set_anticheat_config(body: AnticheatConfigUpdate, _: AdminUser) -> dict:
    """Update anti-cheat feature toggles."""
    # Merge with defaults to ensure all keys exist
    current = rc.get("anticheat_config")
    if not isinstance(current, dict):
        current = dict(_ANTICHEAT_DEFAULTS)
    current.update(body.features)
    await rc.set_key("anticheat_config", current)
    log.info("admin.anticheat_config_updated", features=current)
    return {"status": "success", "features": current}
