"""
db/session.py — Async SQLAlchemy session factory.

Design:
- Single engine per process (not per request)
- AsyncSession with expire_on_commit=False for safe async usage
- Session injected via FastAPI dependency
- Transactions auto-committed at end of request; rolled back on exception
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────

_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DEBUG,
)

_SessionLocal = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency. Provides a request-scoped AsyncSession.
    Commits on clean exit, rolls back on exception.

    Usage:
        @router.get("/")
        async def handler(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with _SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Background task helper ────────────────────────────────────────────────────

@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for background tasks that need a DB session
    outside of a FastAPI request lifecycle.

    Usage:
        async with db_session() as db:
            db.add(record)
    """
    async with _SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Health check ──────────────────────────────────────────────────────────────

async def check_db_health() -> bool:
    """Simple ping for health endpoint."""
    try:
        async with _engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False
