"""
metrics.py — Latency tracking and metric recording.

Design rules:
  - TurnLatencyTracker wraps every hot-path operation.
  - Metric writes are fire-and-forget (asyncio.create_task).
  - Flush loop runs every 5 seconds — batches writes to Postgres.
  - No synchronous I/O — never adds latency to the hot path.
  - SLA violations are both logged (structlog) and persisted as SystemEvents.
  - Buffer holds at most 500 metrics before forced flush (memory safety).
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_metric_buffer: list[dict] = []
_buffer_lock = asyncio.Lock()
_MAX_BUFFER = 500


async def _flush_metrics_loop() -> None:
    """Background task: flush buffered metrics to Postgres every 5 seconds."""
    while True:
        await asyncio.sleep(5)
        await _do_flush()


async def _do_flush() -> None:
    """Execute one flush cycle. Separated for testability."""
    async with _buffer_lock:
        if not _metric_buffer:
            return
        batch = _metric_buffer.copy()
        _metric_buffer.clear()

    try:
        from app.db.persistence import persist_metric_batch, persist_system_event

        await persist_metric_batch(batch)

        # Promote SLA violations to SystemEvents
        for m in batch:
            sla = m.get("sla_violations")
            if sla:
                await persist_system_event(
                    event_type="sla_violation",
                    severity="warn",
                    message=f"Turn {m.get('turn_number')}: {'; '.join(sla)}",
                    session_id=m.get("session_id"),
                    context={"violations": sla},
                )

        if batch:
            log.debug("metrics.flushed", count=len(batch))

    except Exception as exc:
        log.error("metrics.flush_failed", count=len(batch), error=str(exc))


@dataclass
class TurnLatencyTracker:
    session_id: str
    turn_number: int
    _start: float = field(default_factory=time.monotonic, init=False)
    _marks: dict[str, float] = field(default_factory=dict, init=False)
    tokens_in: int = 0
    tokens_out: int = 0
    tts_chunk_count: int = 0
    eval_async_ms: int | None = None

    def mark(self, name: str) -> float:
        elapsed = (time.monotonic() - self._start) * 1000
        self._marks[name] = elapsed
        return elapsed

    def elapsed_ms(self, name: str) -> int | None:
        v = self._marks.get(name)
        return int(v) if v is not None else None

    def check_sla(self) -> list[str]:
        violations = []
        checks = {
            "stt_complete": 700,
            "first_token":  400,
            "first_audio":  1200,
            "turn_complete": 5000,
        }
        for name, limit_ms in checks.items():
            ms = self._marks.get(name)
            if ms is not None and ms > limit_ms:
                violations.append(f"{name}={int(ms)}ms > {limit_ms}ms SLA")
        return violations

    async def emit(self) -> None:
        violations = self.check_sla()
        if violations:
            log.warning(
                "turn.sla_violation",
                session_id=self.session_id,
                turn_number=self.turn_number,
                violations=violations,
            )
        metric = {
            "type":           "turn",
            "session_id":     self.session_id,
            "turn_number":    self.turn_number,
            "timestamp":      datetime.utcnow().isoformat(),
            "stt_latency_ms": self.elapsed_ms("stt_complete"),
            "first_token_ms": self.elapsed_ms("first_token"),
            "first_audio_ms": self.elapsed_ms("first_audio"),
            "turn_total_ms":  self.elapsed_ms("turn_complete"),
            "tts_chunk_count": self.tts_chunk_count,
            "tokens_in":      self.tokens_in,
            "tokens_out":     self.tokens_out,
            "eval_async_ms":  self.eval_async_ms,
            "sla_violations": violations,
        }
        asyncio.create_task(_buffer_metric(metric))


async def _buffer_metric(metric: dict) -> None:
    async with _buffer_lock:
        if len(_metric_buffer) >= _MAX_BUFFER:
            _metric_buffer.pop(0)
            log.warning("metrics.buffer_overflow", max=_MAX_BUFFER)
        _metric_buffer.append(metric)


def record_event(event_name: str, **kwargs: Any) -> None:
    metric = {
        "type":      "event",
        "name":      event_name,
        "timestamp": datetime.utcnow().isoformat(),
        **kwargs,
    }
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_buffer_metric(metric))
    except RuntimeError:
        pass


def record_ws_reconnect(session_id: str) -> None:
    """Record a WS reconnect — also writes immediately as a SystemEvent."""
    record_event("ws.reconnect", session_id=session_id)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            async def _persist():
                try:
                    from app.db.persistence import persist_system_event
                    await persist_system_event(
                        event_type="ws_reconnect",
                        severity="warn",
                        session_id=session_id,
                    )
                except Exception:
                    pass
            loop.create_task(_persist())
    except RuntimeError:
        pass


@asynccontextmanager
async def measure(name: str, session_id: str = ""):
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        record_event(f"provider.{name}", session_id=session_id, latency_ms=elapsed_ms)
