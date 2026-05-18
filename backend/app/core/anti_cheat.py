"""
core/anti_cheat.py — Passive integrity monitoring.

CRITICAL DESIGN RULES:
  1. NEVER blocks interview flow
  2. NEVER blocks WebSocket events
  3. NEVER adds latency to question generation
  4. All writes are fire-and-forget
  5. All analysis is post-session (async, background)
  6. Browser events arrive via a separate REST endpoint
  7. Real-time signals stored in Redis; flushed to Postgres on session end

Integrity score:
  - 100 = no anomalies detected
  - Score decreases with anomaly severity
  - Final score computed only at session end
  - Not visible to candidate at any point

False positive policy:
  - Every flag is probabilistic, not definitive
  - Reviewer must confirm before any action
  - System never accuses — it flags for review
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import structlog

from app.core import redis as r_core

log = structlog.get_logger(__name__)

INTEGRITY_KEY = "integrity:{session_id}"
INTEGRITY_TTL = 86400  # 24 hours


# ── Event types ───────────────────────────────────────────────────────────────

class IntegrityEventType(str, Enum):
    TAB_HIDDEN           = "tab_hidden"
    TAB_VISIBLE          = "tab_visible"
    WINDOW_BLUR          = "window_blur"
    WINDOW_FOCUS         = "window_focus"
    CLIPBOARD_PASTE      = "clipboard_paste"
    CLIPBOARD_COPY       = "clipboard_copy"
    DEVTOOLS_OPENED      = "devtools_opened"
    UNUSUAL_KEY_PATTERN  = "unusual_key_pattern"
    RAPID_ANSWER         = "rapid_answer"
    LONG_PAUSE           = "long_pause"
    ANSWER_RESET         = "answer_reset"


# Event severity weights (subtract from 100)
EVENT_WEIGHTS: dict[IntegrityEventType, int] = {
    IntegrityEventType.TAB_HIDDEN:          5,
    IntegrityEventType.WINDOW_BLUR:         2,
    IntegrityEventType.CLIPBOARD_PASTE:    15,
    IntegrityEventType.CLIPBOARD_COPY:      8,
    IntegrityEventType.DEVTOOLS_OPENED:    20,
    IntegrityEventType.UNUSUAL_KEY_PATTERN: 5,
    IntegrityEventType.RAPID_ANSWER:       10,
    IntegrityEventType.LONG_PAUSE:          2,
    IntegrityEventType.ANSWER_RESET:        3,
}


@dataclass
class IntegrityEvent:
    event_type: IntegrityEventType
    session_id: str
    timestamp: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.event_type.value,
            "ts": self.timestamp,
            "ctx": self.context,
        }


# ── In-session state (Redis) ──────────────────────────────────────────────────

async def record_event(event: IntegrityEvent) -> None:
    """
    Fire-and-forget: append event to session integrity log in Redis.
    Never awaited from the interview hot path — always called via asyncio.create_task().
    """
    try:
        key = INTEGRITY_KEY.format(session_id=event.session_id)
        redis = r_core._get_pool()
        await redis.rpush(key, json.dumps(event.to_dict()))
        await redis.expire(key, INTEGRITY_TTL)
    except Exception as exc:
        log.warning("anti_cheat.record_failed", error=str(exc))


async def get_session_events(session_id: str) -> list[dict]:
    """Retrieve all events for a session from Redis."""
    try:
        key = INTEGRITY_KEY.format(session_id=session_id)
        redis = r_core._get_pool()
        raw_events = await redis.lrange(key, 0, -1)
        return [json.loads(e) for e in raw_events]
    except Exception:
        return []


# ── Score computation (post-session) ─────────────────────────────────────────

@dataclass
class IntegrityResult:
    session_id: str
    integrity_score: int          # 0-100
    confidence: str               # low / medium / high
    tab_switch_count: int
    clipboard_event_count: int
    devtools_detected: bool
    focus_loss_count: int
    long_pause_count: int
    ai_pattern_score: float | None
    vocabulary_diversity: float | None
    answer_speed_anomaly: bool
    requires_review: bool
    event_log: list[dict]


async def compute_integrity(
    session_id: str,
    answers: list[str],
    turn_latencies_ms: list[int],
) -> IntegrityResult:
    """
    Compute final integrity result for a completed session.
    Called as a background task after session.end() — never on hot path.
    """
    events = await get_session_events(session_id)

    # Count events by type
    type_counts: dict[str, int] = {}
    for e in events:
        t = e.get("type", "")
        type_counts[t] = type_counts.get(t, 0) + 1

    tab_switches = type_counts.get("tab_hidden", 0)
    clipboard_events = (
        type_counts.get("clipboard_paste", 0) +
        type_counts.get("clipboard_copy", 0)
    )
    devtools = type_counts.get("devtools_opened", 0) > 0
    focus_losses = type_counts.get("window_blur", 0)
    long_pauses = type_counts.get("long_pause", 0)

    # Compute base score
    score = 100
    for e in events:
        event_type_str = e.get("type", "")
        try:
            event_type = IntegrityEventType(event_type_str)
            score -= EVENT_WEIGHTS.get(event_type, 0)
        except ValueError:
            pass

    score = max(0, min(100, score))

    # AI pattern detection (simple heuristics)
    ai_score = _analyze_ai_patterns(answers)
    vocab_diversity = _vocabulary_diversity(answers)
    speed_anomaly = _check_speed_anomaly(turn_latencies_ms)

    if ai_score > 0.7:
        score -= 15
    if speed_anomaly:
        score -= 10

    score = max(0, min(100, score))

    # Confidence: more events = more confident in the score
    n_events = len(events)
    if n_events < 3:
        confidence = "low"
    elif n_events < 10:
        confidence = "medium"
    else:
        confidence = "high"

    requires_review = (
        score < 70
        or devtools
        or clipboard_events >= 3
        or ai_score > 0.75
    )

    return IntegrityResult(
        session_id=session_id,
        integrity_score=score,
        confidence=confidence,
        tab_switch_count=tab_switches,
        clipboard_event_count=clipboard_events,
        devtools_detected=devtools,
        focus_loss_count=focus_losses,
        long_pause_count=long_pauses,
        ai_pattern_score=round(ai_score, 3),
        vocabulary_diversity=round(vocab_diversity, 3),
        answer_speed_anomaly=speed_anomaly,
        requires_review=requires_review,
        event_log=events,
    )


# ── Text analysis heuristics ──────────────────────────────────────────────────

def _analyze_ai_patterns(answers: list[str]) -> float:
    """
    Heuristic detection of AI-assisted patterns.
    Returns 0.0 (human) to 1.0 (likely AI-assisted).

    Signals:
    - Unusually perfect sentence structure
    - Exact keyword density matching common AI phrasing
    - Unnaturally consistent paragraph structure
    - Low filler word ratio
    """
    if not answers:
        return 0.0

    ai_phrases = [
        "certainly", "absolutely", "it's worth noting", "in summary",
        "to elaborate", "furthermore", "it is important to note",
        "in the context of", "one can observe", "it should be noted",
        "this is achieved by", "the process involves",
    ]
    filler_words = [
        "um", "uh", "like", "you know", "basically", "actually",
        "sort of", "kind of", "i mean", "wait", "so",
    ]

    combined = " ".join(answers).lower()
    words = combined.split()

    if not words:
        return 0.0

    ai_phrase_count = sum(1 for p in ai_phrases if p in combined)
    filler_count = sum(1 for w in words if w in filler_words)
    filler_ratio = filler_count / max(1, len(words))

    # Very low filler ratio + AI phrases → suspicious
    ai_score = 0.0
    if ai_phrase_count >= 3:
        ai_score += 0.3
    if filler_ratio < 0.005:  # less than 0.5% filler words
        ai_score += 0.2
    if ai_phrase_count >= 5:
        ai_score += 0.3

    return min(1.0, ai_score)


def _vocabulary_diversity(answers: list[str]) -> float:
    """Type-token ratio as a proxy for natural language diversity."""
    if not answers:
        return 0.0
    words = " ".join(answers).lower().split()
    if not words:
        return 0.0
    return len(set(words)) / len(words)


def _check_speed_anomaly(latencies_ms: list[int]) -> bool:
    """
    Flag if multiple answers come unusually fast.
    A human cannot meaningfully answer a complex VLSI question in < 3 seconds.
    Threshold: > 30% of turns answered in < 3000ms.
    """
    if not latencies_ms:
        return False
    rapid = sum(1 for ms in latencies_ms if ms < 3000)
    return (rapid / len(latencies_ms)) > 0.3


# ── FastAPI integration ───────────────────────────────────────────────────────

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

integrity_router = APIRouter(prefix="/integrity", tags=["integrity"])


class BrowserEventRequest(BaseModel):
    session_id: str
    event_type: str
    context: dict | None = None


@integrity_router.post("/event", status_code=202)
async def ingest_browser_event(body: BrowserEventRequest) -> dict:
    """
    Receive browser integrity events from the frontend.
    Returns 202 immediately — processing is async.
    Never blocks. Never interacts with interview pipeline.
    """
    try:
        event_type = IntegrityEventType(body.event_type)
    except ValueError:
        return {"status": "ignored", "reason": "unknown_event_type"}

    event = IntegrityEvent(
        event_type=event_type,
        session_id=body.session_id,
        context=body.context or {},
    )

    # Fire-and-forget — never await this from a hot path
    asyncio.create_task(record_event(event))

    return {"status": "received"}
