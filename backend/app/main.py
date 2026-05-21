"""
main.py — FastAPI application entry point.

Startup order:
1. Redis connection pools (required before any request)
2. Metrics background flush task
3. Router registration
4. Middleware

Shutdown order (reverse):
1. Cancel background tasks
2. Close Redis pools
3. Close database pool
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import gateway, websocket
from app.api.auth_routes import router as auth_router
from app.api.admin import router as admin_router
from app.api.reviewer import router as reviewer_router
from app.api.observability import router as obs_router
from app.core.anti_cheat import integrity_router
from app.config import get_settings
from app.core import redis as r
from app.observability import metrics

log = structlog.get_logger(__name__)
settings = get_settings()

_background_tasks: list[asyncio.Task] = []


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    from app.observability.logging import configure_logging
    configure_logging()

    log.info("startup.redis_init")
    await r.init_redis()

    log.info("startup.runtime_config")
    from app.core.runtime_config import load_from_redis
    await load_from_redis()

    log.info("startup.metrics_task")
    flush_task = asyncio.create_task(
        metrics._flush_metrics_loop(),
        name="metrics_flush",
    )
    _background_tasks.append(flush_task)
    
    log.info("startup.ready", env=settings.ENVIRONMENT)
    
    yield
    
    # ── Shutdown ─────────────────────────────────────────────────────────────
    log.info("shutdown.cancelling_tasks")
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    
    log.info("shutdown.redis_close")
    await r.close_redis()
    
    log.info("shutdown.complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else [
        f"https://{settings.DOMAIN}",
        f"https://www.{settings.DOMAIN}",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(gateway.router)
app.include_router(websocket.router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(reviewer_router)
app.include_router(integrity_router)
app.include_router(obs_router)

# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled_exception", path=request.url.path, error=str(exc), exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# ── Request logging middleware ────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    import time
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    
    if not request.url.path.startswith("/health"):
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=elapsed_ms,
        )
    return response


# ── Dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        loop="uvloop",           # uvloop for ~2x asyncio throughput
        http="httptools",        # httptools for faster HTTP parsing
        ws="websockets",
        reload=settings.DEBUG,
        log_level="info",
        access_log=False,        # using structured middleware logging instead
    )
