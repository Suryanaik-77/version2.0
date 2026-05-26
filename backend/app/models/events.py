"""
events.py — All WebSocket event types and payloads.

Every event that crosses the WebSocket boundary is typed here.
No free-form dicts in WS handlers.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class WSEventType(str, Enum):
    # Session lifecycle
    SESSION_START      = "SESSION_START"
    SESSION_END        = "SESSION_END"
    SESSION_PAUSED     = "SESSION_PAUSED"
    SESSION_RESUMED    = "SESSION_RESUMED"

    # Turn lifecycle
    TURN_START         = "TURN_START"
    TURN_COMPLETE      = "TURN_COMPLETE"

    # Audio — candidate → server
    AUDIO_CHUNK        = "AUDIO_CHUNK"          # raw audio bytes (binary frame)
    BARGE_IN           = "BARGE_IN"             # candidate interrupted

    # STT — server → candidate
    STT_PARTIAL        = "STT_PARTIAL"
    STT_FINAL          = "STT_FINAL"

    # Interviewer speech — server → candidate
    INTERVIEWER_CHUNK  = "INTERVIEWER_CHUNK"    # audio bytes for a sentence
    INTERVIEWER_DONE   = "INTERVIEWER_DONE"     # full response complete

    # State
    STATE_CHANGE       = "STATE_CHANGE"         # admin only — never to candidate

    # Anti-cheat — admin pipeline only
    ANTICHEAT_ALERT    = "ANTICHEAT_ALERT"

    # Infrastructure
    HEARTBEAT          = "HEARTBEAT"
    HEARTBEAT_ACK      = "HEARTBEAT_ACK"
    ERROR              = "ERROR"
    RECONNECTED        = "RECONNECTED"


class DisconnectReason(str, Enum):
    CLEAN      = "CLEAN"
    TIMEOUT    = "TIMEOUT"
    STALE      = "STALE"
    AUTH_ERROR = "AUTH_ERROR"
    ERROR      = "ERROR"


# ── Base event ────────────────────────────────────────────────────────────────

class WSEvent(BaseModel):
    type: WSEventType
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        return self.model_dump_json()


# ── Specific event constructors (used by modules to emit typed events) ────────

def session_start_event(session_id: str, domain: str, candidate_id: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.SESSION_START,
        session_id=session_id,
        payload={"domain": domain, "candidate_id": candidate_id},
    )


def session_end_event(session_id: str, reason: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.SESSION_END,
        session_id=session_id,
        payload={"reason": reason},
    )


def turn_start_event(session_id: str, turn_number: int) -> WSEvent:
    return WSEvent(
        type=WSEventType.TURN_START,
        session_id=session_id,
        payload={"turn_number": turn_number},
    )


def turn_complete_event(
    session_id: str,
    turn_number: int,
    latency_ms: int,
    *,
    stt_ms: int | None = None,
    first_token_ms: int | None = None,
    first_audio_ms: int | None = None,
    tts_chunks: int = 0,
) -> WSEvent:
    payload: dict = {"turn_number": turn_number, "latency_ms": latency_ms}
    if stt_ms is not None:
        payload["stt_ms"] = stt_ms
    if first_token_ms is not None:
        payload["first_token_ms"] = first_token_ms
    if first_audio_ms is not None:
        payload["first_audio_ms"] = first_audio_ms
    if tts_chunks:
        payload["tts_chunks"] = tts_chunks
    return WSEvent(
        type=WSEventType.TURN_COMPLETE,
        session_id=session_id,
        payload=payload,
    )


def stt_partial_event(session_id: str, fragment: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.STT_PARTIAL,
        session_id=session_id,
        payload={"fragment": fragment, "is_final": False},
    )


def stt_final_event(session_id: str, transcript: str, stt_latency_ms: int, turn_number: int) -> WSEvent:
    return WSEvent(
        type=WSEventType.STT_FINAL,
        session_id=session_id,
        payload={
            "transcript": transcript,
            "stt_latency_ms": stt_latency_ms,
            "turn_number": turn_number,
            "is_final": True,
        },
    )


def interviewer_chunk_event(
    session_id: str,
    sentence_index: int,
    is_final: bool,
) -> WSEvent:
    """Audio bytes are sent as binary WS frames, not JSON. This event is the metadata frame."""
    return WSEvent(
        type=WSEventType.INTERVIEWER_CHUNK,
        session_id=session_id,
        payload={"sentence_index": sentence_index, "is_final": is_final},
    )


def interviewer_done_event(session_id: str, sentence_count: int) -> WSEvent:
    return WSEvent(
        type=WSEventType.INTERVIEWER_DONE,
        session_id=session_id,
        payload={"sentence_count": sentence_count},
    )


def barge_in_event(session_id: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.BARGE_IN,
        session_id=session_id,
        payload={"action": "interrupting"},
    )


def state_change_event(session_id: str, old_mode: str, new_mode: str, turn_number: int) -> WSEvent:
    """Admin-only event. MUST NOT be sent to candidate connections."""
    return WSEvent(
        type=WSEventType.STATE_CHANGE,
        session_id=session_id,
        payload={
            "old_mode": old_mode,
            "new_mode": new_mode,
            "turn_number": turn_number,
        },
    )


def error_event(session_id: str, code: str, message: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.ERROR,
        session_id=session_id,
        payload={"code": code, "message": message},
    )


def heartbeat_ack_event(session_id: str) -> WSEvent:
    return WSEvent(
        type=WSEventType.HEARTBEAT_ACK,
        session_id=session_id,
    )
