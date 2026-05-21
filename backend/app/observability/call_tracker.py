"""
call_tracker.py — Per-call LLM/STT/TTS tracking with in-memory log.

Ported from monolith approach: thread-safe in-memory call log with TTL,
queryable via REST endpoints. Complements the DB-persisted metrics.

Why in-memory AND DB?
  - In-memory: instant queries, no DB latency, good for live dashboard
  - DB (via metrics buffer): permanent record, aggregation queries, reports

Cost model (GPT-4o-mini defaults):
  - Input:  $0.15 / 1M tokens
  - Output: $0.60 / 1M tokens
  - STT:    $0.006 / minute
  - TTS:    $0.015 / 1K characters (OpenAI), varies by provider
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── In-memory call log (thread-safe) ────────────────────────────────────────

_call_logs: list[dict] = []
_log_lock = threading.Lock()
_MAX_LOGS = 2000
_LOG_TTL_SECONDS = 600  # 10 minutes

# ── Cost models ──────────────────────────────────────────────────────────────

_LLM_COST_PER_TOKEN = {
    "gpt-4o-mini":       {"input": 0.15e-6, "output": 0.60e-6},
    "gpt-4o":            {"input": 2.50e-6, "output": 10.0e-6},
    "gpt-4":             {"input": 30.0e-6, "output": 60.0e-6},
    "grok-3-mini":       {"input": 0.30e-6, "output": 0.50e-6},
    "grok-3":            {"input": 3.00e-6, "output": 15.0e-6},
    "_default":          {"input": 0.15e-6, "output": 0.60e-6},
}

_STT_COST_PER_MINUTE = 0.006   # whisper
_TTS_COST_PER_1K_CHARS = {
    "openai":   0.015,
    "deepgram": 0.0043,
    "inworld":  0.010,
    "_default": 0.015,
}


# ── Track functions ──────────────────────────────────────────────────────────

def track_llm_call(
    session_id: str,
    step: str,
    model: str,
    latency_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    status: str = "success",
    error: str = "",
) -> None:
    """Track an LLM call with latency, tokens, and cost."""
    cost_rates = _LLM_COST_PER_TOKEN.get(model, _LLM_COST_PER_TOKEN["_default"])
    cost_usd = (input_tokens * cost_rates["input"]) + (output_tokens * cost_rates["output"])

    entry = {
        "timestamp": time.time(),
        "formatted_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "step": step,
        "model": model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(cost_usd, 8),
        "status": status,
        "error": error,
    }
    _append_log(entry)

    if status == "success":
        log.debug("track.llm", session_id=session_id, step=step, model=model,
                  latency_ms=latency_ms, tokens=input_tokens + output_tokens)


def track_stt_call(
    session_id: str,
    latency_ms: int,
    audio_duration_sec: float = 0,
    status: str = "success",
    error: str = "",
) -> None:
    """Track a speech-to-text call."""
    cost_usd = (audio_duration_sec / 60) * _STT_COST_PER_MINUTE

    entry = {
        "timestamp": time.time(),
        "formatted_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "step": "STT",
        "model": "whisper",
        "latency_ms": latency_ms,
        "audio_duration_sec": round(audio_duration_sec, 2),
        "cost_usd": round(cost_usd, 8),
        "status": status,
        "error": error,
    }
    _append_log(entry)


def track_tts_call(
    session_id: str,
    latency_ms: int,
    char_count: int = 0,
    provider: str = "openai",
    status: str = "success",
    error: str = "",
) -> None:
    """Track a text-to-speech call."""
    rate = _TTS_COST_PER_1K_CHARS.get(provider, _TTS_COST_PER_1K_CHARS["_default"])
    cost_usd = (char_count / 1000) * rate

    entry = {
        "timestamp": time.time(),
        "formatted_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "step": "TTS",
        "model": provider,
        "latency_ms": latency_ms,
        "char_count": char_count,
        "cost_usd": round(cost_usd, 8),
        "status": status,
        "error": error,
    }
    _append_log(entry)


def track_resume_parse(
    session_id: str,
    latency_ms: int,
    model: str = "gpt-4o-mini",
    status: str = "success",
    error: str = "",
) -> None:
    """Track a resume parsing call."""
    entry = {
        "timestamp": time.time(),
        "formatted_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "step": "resume_parsing",
        "model": model,
        "latency_ms": latency_ms,
        "cost_usd": round(0.15e-6 * 500 + 0.60e-6 * 200, 8),  # ~500 in, ~200 out
        "status": status,
        "error": error,
    }
    _append_log(entry)


# ── Query functions ──────────────────────────────────────────────────────────

def get_platform_summary(window_seconds: int = 86400) -> dict:
    """
    Aggregate metrics over a time window. Returns P50/P95 latency,
    success rates, total cost per step, and call counts.
    """
    cutoff = time.time() - window_seconds
    with _log_lock:
        logs = [e for e in _call_logs if e["timestamp"] >= cutoff]

    if not logs:
        return {
            "window_seconds": window_seconds,
            "total_calls": 0,
            "by_step": {},
            "total_cost_usd": 0.0,
        }

    by_step: dict[str, dict] = {}
    total_cost = 0.0

    # Group by step
    step_groups: dict[str, list] = defaultdict(list)
    for entry in logs:
        step_groups[entry.get("step", "unknown")].append(entry)

    for step, entries in step_groups.items():
        latencies = sorted([e["latency_ms"] for e in entries if e.get("status") == "success"])
        success_count = sum(1 for e in entries if e.get("status") == "success")
        fail_count = sum(1 for e in entries if e.get("status") != "success")
        step_cost = sum(e.get("cost_usd", 0) for e in entries)
        total_cost += step_cost

        by_step[step] = {
            "total_calls": len(entries),
            "success": success_count,
            "failures": fail_count,
            "success_rate": round(success_count / len(entries), 3) if entries else 0,
            "cost_usd": round(step_cost, 6),
            "latency": _percentiles(latencies) if latencies else {},
        }

    return {
        "window_seconds": window_seconds,
        "total_calls": len(logs),
        "by_step": by_step,
        "total_cost_usd": round(total_cost, 6),
    }


def get_session_summary(session_id: str) -> dict:
    """Per-session cost and performance breakdown."""
    with _log_lock:
        logs = [e for e in _call_logs if e.get("session_id") == session_id]

    if not logs:
        return {"session_id": session_id, "total_calls": 0, "by_step": {}, "total_cost_usd": 0.0}

    by_step: dict[str, dict] = {}
    total_cost = 0.0

    step_groups: dict[str, list] = defaultdict(list)
    for entry in logs:
        step_groups[entry.get("step", "unknown")].append(entry)

    for step, entries in step_groups.items():
        latencies = sorted([e["latency_ms"] for e in entries if e.get("status") == "success"])
        step_cost = sum(e.get("cost_usd", 0) for e in entries)
        total_cost += step_cost
        total_tokens = sum(e.get("total_tokens", 0) for e in entries)

        by_step[step] = {
            "calls": len(entries),
            "cost_usd": round(step_cost, 6),
            "total_tokens": total_tokens,
            "latency": _percentiles(latencies) if latencies else {},
        }

    return {
        "session_id": session_id,
        "total_calls": len(logs),
        "by_step": by_step,
        "total_cost_usd": round(total_cost, 6),
    }


def get_logs(
    session_id: str = "",
    step: str = "",
    status: str = "",
    limit: int = 200,
) -> list[dict]:
    """Query raw call logs with optional filters."""
    with _log_lock:
        filtered = list(_call_logs)

    if session_id:
        filtered = [e for e in filtered if e.get("session_id") == session_id]
    if step:
        filtered = [e for e in filtered if e.get("step") == step]
    if status:
        filtered = [e for e in filtered if e.get("status") == status]

    # Most recent first, capped
    return list(reversed(filtered[-limit:]))


# ── Internal ─────────────────────────────────────────────────────────────────

def _append_log(entry: dict) -> None:
    """Thread-safe append with size cap and TTL cleanup."""
    cutoff = time.time() - _LOG_TTL_SECONDS
    with _log_lock:
        # Evict expired entries
        while _call_logs and _call_logs[0]["timestamp"] < cutoff:
            _call_logs.pop(0)
        # Evict oldest if over cap
        while len(_call_logs) >= _MAX_LOGS:
            _call_logs.pop(0)
        _call_logs.append(entry)


def _percentiles(sorted_values: list[int]) -> dict:
    """Compute P50, P95, avg, min, max from sorted latencies."""
    if not sorted_values:
        return {}
    n = len(sorted_values)
    return {
        "p50": sorted_values[n // 2],
        "p95": sorted_values[int(n * 0.95)] if n >= 20 else sorted_values[-1],
        "avg": round(sum(sorted_values) / n),
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "count": n,
    }
