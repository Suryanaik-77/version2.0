"""
observability.py — Observability and deep health REST endpoints.

Ported from monolith:
  GET /api/observability/summary   — Platform-wide metrics dashboard
  GET /api/observability/logs      — Raw call logs with filtering
  GET /api/observability/session   — Session-level cost & performance
  GET /health/deep                 — Deep dependency health check

Auth: requires reviewer or admin role.
"""
from __future__ import annotations

import asyncio
import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query

from app.api.auth import TokenPayload, require_reviewer
from app.config import get_settings
from app.observability.call_tracker import (
    get_platform_summary,
    get_session_summary,
    get_logs,
)

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(tags=["observability"])

AuthUser = Annotated[TokenPayload, Depends(require_reviewer)]


# ── Observability endpoints ──────────────────────────────────────────────────

@router.get("/api/observability/summary")
async def obs_summary(
    user: AuthUser,
    window: int = Query(default=86400, ge=60, le=604800, description="Time window in seconds"),
) -> dict:
    """
    Platform-wide metrics summary.
    Returns P50/P95 latency, success rates, and cost per step (LLM, STT, TTS).
    """
    return get_platform_summary(window)


@router.get("/api/observability/logs")
async def obs_logs(
    user: AuthUser,
    session_id: str = Query(default="", description="Filter by session"),
    step: str = Query(default="", description="Filter by step (LLM_question, STT, TTS, etc.)"),
    status: str = Query(default="", description="Filter by status (success, failure)"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    """Raw call logs with filtering. Most recent first."""
    logs = get_logs(session_id=session_id, step=step, status=status, limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.get("/api/observability/session/{session_id}")
async def obs_session(
    session_id: str,
    user: AuthUser,
) -> dict:
    """Per-session cost and performance breakdown by step."""
    return get_session_summary(session_id)


# ── Deep health check ────────────────────────────────────────────────────────

@router.get("/health/deep")
async def health_deep() -> dict:
    """
    Deep health check — validates all dependencies:
    - Redis connectivity and latency
    - PostgreSQL connectivity and latency
    - OpenAI API reachability and latency
    - Active session count

    Used for deployment validation and monitoring.
    No auth required — but returns no sensitive data.
    """
    checks: dict[str, dict] = {}
    all_ok = True

    # Redis check
    checks["redis"] = await _check_redis()
    if not checks["redis"]["ok"]:
        all_ok = False

    # PostgreSQL check
    checks["postgres"] = await _check_postgres()
    if not checks["postgres"]["ok"]:
        all_ok = False

    # OpenAI API check
    checks["openai"] = await _check_openai()
    if not checks["openai"]["ok"]:
        all_ok = False

    # Active session count (from Redis)
    active_sessions = 0
    try:
        from app.core import redis as r
        rds = r._get_pool()
        keys = await rds.keys("session:*:state")
        active_sessions = len(keys)
    except Exception:
        pass

    return {
        "status": "healthy" if all_ok else "degraded",
        "all_ok": all_ok,
        "checks": checks,
        "active_sessions": active_sessions,
        "service": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
    }


async def _check_redis() -> dict:
    """Check Redis connectivity with latency measurement."""
    try:
        from app.core import redis as r
        rds = r._get_pool()
        t0 = time.monotonic()
        pong = await asyncio.wait_for(rds.ping(), timeout=3.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": bool(pong), "latency_ms": latency_ms}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_postgres() -> dict:
    """Check PostgreSQL connectivity with latency measurement."""
    try:
        from app.db.session import db_session
        from sqlalchemy import text
        t0 = time.monotonic()
        async with db_session() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=5.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "latency_ms": latency_ms}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_openai() -> dict:
    """Check OpenAI API reachability with a lightweight models.list call."""
    if not settings.OPENAI_API_KEY:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=5.0)
        t0 = time.monotonic()
        models = await asyncio.wait_for(client.models.list(), timeout=5.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "latency_ms": latency_ms}
    except Exception as e:
        return {"ok": False, "error": str(e)}
