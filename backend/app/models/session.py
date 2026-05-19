"""
session.py — All session and interview state types.

Single source of truth for types shared across modules.
No business logic here. Pure data definitions.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class InterviewerMode(str, Enum):
    PROBING       = "PROBING"
    DEEPENING     = "DEEPENING"
    ESCALATING    = "ESCALATING"
    PRESSURE      = "PRESSURE"
    RECOVERING    = "RECOVERING"
    TRANSITIONING = "TRANSITIONING"


class VLSIDomain(str, Enum):
    ANALOG_LAYOUT      = "ANALOG_LAYOUT"
    PHYSICAL_DESIGN    = "PHYSICAL_DESIGN"
    DESIGN_VERIFICATION = "DESIGN_VERIFICATION"


class SessionPhase(str, Enum):
    WARMUP   = "WARMUP"
    CORE     = "CORE"
    PRESSURE = "PRESSURE"
    CLOSING  = "CLOSING"


class EndReason(str, Enum):
    COMPLETED            = "COMPLETED"
    CANDIDATE_DISCONNECT = "CANDIDATE_DISCONNECT"
    TIMEOUT              = "TIMEOUT"
    ADMIN_TERMINATE      = "ADMIN_TERMINATE"
    ERROR                = "ERROR"


class UserRole(str, Enum):
    CANDIDATE = "candidate"
    REVIEWER  = "reviewer"
    ADMIN     = "admin"


# ── Session state (lives in Redis) ────────────────────────────────────────────

class ResumeData(BaseModel):
    """Parsed resume data — extracted at session creation."""
    model_config = {"extra": "ignore"}

    candidate_name: str = "Candidate"
    domain: str = "physical_design"
    level: str = "trained_fresher"
    years_experience: float = 0
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    key_projects: list[str] = Field(default_factory=list)
    education: str = ""

    @classmethod
    def _coerce_list(cls, v):
        if isinstance(v, dict):
            return list(v.values()) if v else []
        if not isinstance(v, list):
            return []
        return v

    def __init__(self, **data):
        for f in ("skills", "tools", "key_projects"):
            if f in data and not isinstance(data[f], list):
                data[f] = self._coerce_list(data[f])
        super().__init__(**data)


class SessionState(BaseModel):
    """Live session state. Owned exclusively by interview_engine via Redis."""
    session_id: str
    mode: InterviewerMode = InterviewerMode.PROBING
    turn_count: int = 0
    active_domain: VLSIDomain = VLSIDomain.ANALOG_LAYOUT
    phase: SessionPhase = SessionPhase.WARMUP
    candidate_id: str
    resume: ResumeData = Field(default_factory=ResumeData)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_turn_at: datetime | None = None
    is_active: bool = True


class SessionContext(BaseModel):
    """Hot-path context injected into question generation each turn."""
    session_id: str
    mode: InterviewerMode
    active_domain: VLSIDomain
    turn_count: int
    last_transcript: str = ""
    last_question: str = ""
    current_topic: str = ""


# ── Candidate memory (lives in Redis, flushed to Postgres every 5 turns) ─────

class NumberRecord(BaseModel):
    value: str       # e.g. "50ps"
    context: str     # e.g. "stated as timing margin on 28nm block"
    turn_number: int


class MistakeRecord(BaseModel):
    description: str
    turn_number: int
    was_corrected: bool = False


class Contradiction(BaseModel):
    statement_a: str
    statement_b: str
    turn_a: int
    turn_b: int
    resolved: bool = False


class TopicSummary(BaseModel):
    topic: str
    domain: VLSIDomain
    avg_score: float = 0.0
    turn_count: int = 0
    pattern: str = ""   # e.g. "mechanism never explained"


class ConfidenceShift(BaseModel):
    topic: str
    from_level: str   # HIGH | MEDIUM | LOW
    to_level: str
    turn_number: int


class BuzzwordRecord(BaseModel):
    term: str
    context: str
    turn_number: int
    count: int = 1


class CandidateMemory(BaseModel):
    """
    Per-session candidate model. Owned exclusively by memory_engine.
    Injected into question generation context each turn.
    """
    session_id: str
    claims: list[str] = Field(default_factory=list)
    tools_mentioned: list[str] = Field(default_factory=list)
    numbers_stated: list[NumberRecord] = Field(default_factory=list)
    architectures: list[str] = Field(default_factory=list)
    mistakes: list[MistakeRecord] = Field(default_factory=list)
    weak_topics: list[TopicSummary] = Field(default_factory=list)
    strong_topics: list[TopicSummary] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    buzzwords: list[BuzzwordRecord] = Field(default_factory=list)
    confidence_shifts: list[ConfidenceShift] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ── Inline signals emitted during question generation ─────────────────────────

class Correctness(str, Enum):
    CORRECT  = "CORRECT"
    PARTIAL  = "PARTIAL"
    WRONG    = "WRONG"
    UNKNOWN  = "UNKNOWN"


class SignalLevel(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class InlineSignals(BaseModel):
    """
    Detected by question_engine DURING generation — not after.
    These drive immediate mode decisions before async eval completes.
    """
    session_id: str
    turn_number: int
    correctness: Correctness = Correctness.UNKNOWN
    vagueness: SignalLevel = SignalLevel.LOW
    confidence: SignalLevel = SignalLevel.HIGH
    memorization_suspected: bool = False
    missing_mechanism: str | None = None
    contradiction_with: str | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Turn context ──────────────────────────────────────────────────────────────

class TurnContext(BaseModel):
    """Assembled by interview_engine before each question generation call."""
    session_id: str
    turn_number: int
    transcript: str
    domain: VLSIDomain
    mode: InterviewerMode
    memory: CandidateMemory
    resume: ResumeData = Field(default_factory=ResumeData)
    prior_answers: list[str] = Field(default_factory=list)   # last 3
    inline_signals: InlineSignals | None = None


# ── Session summary (written to Postgres on session end) ─────────────────────

class SessionSummary(BaseModel):
    session_id: str
    candidate_id: str
    total_turns: int
    domains_covered: list[VLSIDomain]
    final_mode: InterviewerMode
    started_at: datetime
    ended_at: datetime
    end_reason: EndReason
    avg_turn_latency_ms: float | None = None
