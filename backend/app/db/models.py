"""
db/models.py — SQLAlchemy async models for VLSI Interview Platform V2.

Design rules:
- All PKs are UUIDs (not integers) — no sequential ID leakage
- All timestamps are UTC
- Soft deletes only (deleted_at) — no hard deletes in production
- JSON columns for flexible structured data (eval_scores, signals, memory_snapshot)
- FK constraints enforced — no orphaned sessions
- Indexes on every column used in WHERE or ORDER BY
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, Column, DateTime, Enum as SAEnum, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint,
)
# UUID stored as String(36) for PostgreSQL compatibility
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


def _uuid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ══════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"

    id              = Column(String(36), primary_key=True, default=_uuid)
    email           = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name       = Column(String(255), nullable=True)
    role            = Column(SAEnum("candidate", "reviewer", "admin", name="user_role"),
                             nullable=False, default="candidate")
    is_active       = Column(Boolean, nullable=False, default=True)
    is_verified     = Column(Boolean, nullable=False, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(),
                             onupdate=func.now(), nullable=False)
    last_login_at   = Column(DateTime(timezone=True), nullable=True)
    deleted_at      = Column(DateTime(timezone=True), nullable=True)

    # Relations
    sessions        = relationship("InterviewSession", back_populates="candidate",
                                   foreign_keys="InterviewSession.candidate_id")
    refresh_tokens  = relationship("RefreshToken", back_populates="user",
                                   cascade="all, delete-orphan")
    reviewer_notes  = relationship("ReviewerNote", back_populates="reviewer")

    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id          = Column(String(36), primary_key=True, default=_uuid)
    user_id     = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    token_hash  = Column(String(128), nullable=False, unique=True, index=True)
    device_hint = Column(String(255), nullable=True)   # "Chrome/macOS" etc.
    ip_address  = Column(String(64), nullable=True)
    issued_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    revoked_at  = Column(DateTime(timezone=True), nullable=True)
    used_at     = Column(DateTime(timezone=True), nullable=True)

    user        = relationship("User", back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_rt_user_active", "user_id", "revoked_at"),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id          = Column(String(36), primary_key=True, default=_uuid)
    user_id     = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    token_hash  = Column(String(128), nullable=False, unique=True, index=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    used_at     = Column(DateTime(timezone=True), nullable=True)


# ══════════════════════════════════════════════════════════════════
# INTERVIEW SESSIONS
# ══════════════════════════════════════════════════════════════════

class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id              = Column(String(36), primary_key=True, default=_uuid)
    candidate_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    domain          = Column(String(64), nullable=False)
    status          = Column(
        SAEnum("pending", "active", "completed", "terminated", "error",
               name="session_status"),
        nullable=False, default="pending")
    end_reason      = Column(String(64), nullable=True)

    # Timing
    scheduled_at    = Column(DateTime(timezone=True), nullable=True)
    started_at      = Column(DateTime(timezone=True), nullable=True)
    ended_at        = Column(DateTime(timezone=True), nullable=True)
    duration_secs   = Column(Integer, nullable=True)

    # Turn summary
    total_turns     = Column(Integer, nullable=False, default=0)
    final_mode      = Column(String(32), nullable=True)

    # Aggregate scores (computed on session end, stored for fast queries)
    avg_score       = Column(Float, nullable=True)
    avg_correctness = Column(Float, nullable=True)
    avg_depth       = Column(Float, nullable=True)

    # Session metadata
    ws_reconnects   = Column(Integer, nullable=False, default=0)
    provider_used   = Column(String(64), nullable=True)
    total_tokens_in = Column(Integer, nullable=False, default=0)
    total_tokens_out= Column(Integer, nullable=False, default=0)
    total_cost_usd  = Column(Float, nullable=False, default=0.0)

    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(),
                             onupdate=func.now(), nullable=False)

    # Relations
    candidate       = relationship("User", back_populates="sessions",
                                   foreign_keys=[candidate_id])
    turns           = relationship("InterviewTurn", back_populates="session",
                                   order_by="InterviewTurn.turn_number",
                                   cascade="all, delete-orphan")
    report          = relationship("SessionReport", back_populates="session",
                                   uselist=False, cascade="all, delete-orphan")
    integrity       = relationship("IntegrityRecord", back_populates="session",
                                   uselist=False, cascade="all, delete-orphan")
    reviewer_notes  = relationship("ReviewerNote", back_populates="session")

    __table_args__ = (
        Index("ix_sessions_candidate_status", "candidate_id", "status"),
        Index("ix_sessions_started_at", "started_at"),
        Index("ix_sessions_domain", "domain"),
    )


class InterviewTurn(Base):
    __tablename__ = "interview_turns"

    id                  = Column(String(36), primary_key=True, default=_uuid)
    session_id          = Column(String(36),
                                 ForeignKey("interview_sessions.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    turn_number         = Column(Integer, nullable=False)

    # Content
    question_text       = Column(Text, nullable=False)
    answer_text         = Column(Text, nullable=False)
    domain              = Column(String(64), nullable=False)
    mode_at_start       = Column(String(32), nullable=False)
    mode_at_end         = Column(String(32), nullable=False)

    # Eval scores (7 dimensions + derived)
    eval_scores         = Column(JSON, nullable=True)     # {accuracy, depth, ...}
    avg_eval_score      = Column(Float, nullable=True)
    correctness_score   = Column(Integer, nullable=True)
    depth_score         = Column(Integer, nullable=True)

    # Inline signals snapshot
    signals             = Column(JSON, nullable=True)     # InlineSignals as dict

    # Latency (milliseconds)
    stt_latency_ms      = Column(Integer, nullable=True)
    first_token_ms      = Column(Integer, nullable=True)
    first_audio_ms      = Column(Integer, nullable=True)
    turn_total_ms       = Column(Integer, nullable=True)

    # Token costs
    tokens_in           = Column(Integer, nullable=True)
    tokens_out          = Column(Integer, nullable=True)
    cost_usd            = Column(Float, nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now(),
                                 nullable=False)

    session             = relationship("InterviewSession", back_populates="turns")

    __table_args__ = (
        UniqueConstraint("session_id", "turn_number", name="uq_turn_per_session"),
        Index("ix_turns_session_turn", "session_id", "turn_number"),
    )


# ══════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════

class SessionReport(Base):
    __tablename__ = "session_reports"

    id              = Column(String(36), primary_key=True, default=_uuid)
    session_id      = Column(String(36),
                             ForeignKey("interview_sessions.id", ondelete="CASCADE"),
                             nullable=False, unique=True, index=True)

    # Dimension averages across all turns
    avg_accuracy    = Column(Float, nullable=True)
    avg_depth       = Column(Float, nullable=True)
    avg_completeness= Column(Float, nullable=True)
    avg_clarity     = Column(Float, nullable=True)
    avg_maturity    = Column(Float, nullable=True)
    avg_ownership   = Column(Float, nullable=True)
    avg_correctness = Column(Float, nullable=True)
    overall_score   = Column(Float, nullable=True)

    # Intelligence layer metrics
    avg_naturalness     = Column(Float, nullable=True)
    pressure_turns_pct  = Column(Float, nullable=True)   # % of turns in PRESSURE mode

    # Summary (LLM-generated, async, may be null initially)
    strength_summary    = Column(Text, nullable=True)
    weakness_summary    = Column(Text, nullable=True)
    overall_summary     = Column(Text, nullable=True)
    hiring_signal       = Column(String(32), nullable=True)  # strong/moderate/weak/no

    # Full memory snapshot at session end
    memory_snapshot     = Column(JSON, nullable=True)

    # Expert review status
    review_status       = Column(
        SAEnum("pending", "in_review", "reviewed", "approved", name="review_status"),
        nullable=False, default="pending")
    reviewed_by_id      = Column(String(36), ForeignKey("users.id"), nullable=True)
    reviewed_at         = Column(DateTime(timezone=True), nullable=True)
    reviewer_override   = Column(JSON, nullable=True)  # override scores if any

    generated_at        = Column(DateTime(timezone=True), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now(),
                                 nullable=False)

    session             = relationship("InterviewSession", back_populates="report")

    __table_args__ = (
        Index("ix_reports_review_status", "review_status"),
        Index("ix_reports_hiring_signal", "hiring_signal"),
    )


# ══════════════════════════════════════════════════════════════════
# REVIEWER TOOLS
# ══════════════════════════════════════════════════════════════════

class ReviewerNote(Base):
    __tablename__ = "reviewer_notes"

    id              = Column(String(36), primary_key=True, default=_uuid)
    session_id      = Column(String(36),
                             ForeignKey("interview_sessions.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    reviewer_id     = Column(String(36), ForeignKey("users.id"), nullable=False)
    turn_number     = Column(Integer, nullable=True)   # null = session-level note
    note_text       = Column(Text, nullable=False)
    note_type       = Column(
        SAEnum("general", "flag", "correction", "commendation", name="note_type"),
        nullable=False, default="general")
    is_visible_to_candidate = Column(Boolean, nullable=False, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(),
                             onupdate=func.now(), nullable=False)

    session         = relationship("InterviewSession", back_populates="reviewer_notes")
    reviewer        = relationship("User", back_populates="reviewer_notes")

    __table_args__ = (
        Index("ix_notes_session_reviewer", "session_id", "reviewer_id"),
    )


class SessionFlag(Base):
    __tablename__ = "session_flags"

    id              = Column(String(36), primary_key=True, default=_uuid)
    session_id      = Column(String(36),
                             ForeignKey("interview_sessions.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    flagged_by_id   = Column(String(36), ForeignKey("users.id"), nullable=False)
    flag_type       = Column(String(64), nullable=False)  # "integrity", "quality", "technical"
    reason          = Column(Text, nullable=False)
    resolved        = Column(Boolean, nullable=False, default=False)
    resolved_at     = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ══════════════════════════════════════════════════════════════════
# INTEGRITY / ANTI-CHEAT
# ══════════════════════════════════════════════════════════════════

class IntegrityRecord(Base):
    __tablename__ = "integrity_records"

    id                      = Column(String(36), primary_key=True, default=_uuid)
    session_id              = Column(String(36),
                                     ForeignKey("interview_sessions.id", ondelete="CASCADE"),
                                     nullable=False, unique=True, index=True)

    # Integrity score 0-100 (100 = no anomalies)
    integrity_score         = Column(Integer, nullable=True)
    confidence              = Column(String(16), nullable=True)  # low/medium/high

    # Event counts
    tab_switch_count        = Column(Integer, nullable=False, default=0)
    clipboard_event_count   = Column(Integer, nullable=False, default=0)
    devtools_detected       = Column(Boolean, nullable=False, default=False)
    focus_loss_count        = Column(Integer, nullable=False, default=0)
    long_pause_count        = Column(Integer, nullable=False, default=0)

    # AI pattern signals
    ai_pattern_score        = Column(Float, nullable=True)   # 0-1, higher = more AI-like
    vocabulary_diversity    = Column(Float, nullable=True)   # 0-1
    answer_speed_anomaly    = Column(Boolean, nullable=False, default=False)

    # Raw event log (JSON array of timestamped events)
    event_log               = Column(JSON, nullable=True)

    # Flags
    requires_review         = Column(Boolean, nullable=False, default=False)
    reviewed_by_id          = Column(String(36), ForeignKey("users.id"), nullable=True)
    reviewer_verdict        = Column(String(32), nullable=True)  # clean/suspicious/inconclusive

    created_at              = Column(DateTime(timezone=True), server_default=func.now(),
                                     nullable=False)
    updated_at              = Column(DateTime(timezone=True), server_default=func.now(),
                                     onupdate=func.now(), nullable=False)

    session                 = relationship("InterviewSession", back_populates="integrity")


# ══════════════════════════════════════════════════════════════════
# OBSERVABILITY / METRICS
# ══════════════════════════════════════════════════════════════════

class OperationalMetric(Base):
    """
    Append-only metric store. Written by fire-and-forget async tasks.
    Never read on the hot path.
    """
    __tablename__ = "operational_metrics"

    id              = Column(String(36), primary_key=True, default=_uuid)
    session_id      = Column(String(36), nullable=True, index=True)  # null = system metric
    turn_number     = Column(Integer, nullable=True)
    metric_type     = Column(String(64), nullable=False, index=True)
    value_ms        = Column(Integer, nullable=True)   # for latency
    value_float     = Column(Float, nullable=True)     # for costs, scores
    value_int       = Column(Integer, nullable=True)   # for counts
    value_json      = Column(JSON, nullable=True)      # for structured data
    provider        = Column(String(32), nullable=True)
    recorded_at     = Column(DateTime(timezone=True), server_default=func.now(),
                             nullable=False, index=True)

    __table_args__ = (
        Index("ix_metrics_type_time", "metric_type", "recorded_at"),
        Index("ix_metrics_session_type", "session_id", "metric_type"),
    )


class SystemEvent(Base):
    """
    System-level events: reconnects, fallbacks, errors, provider switches.
    """
    __tablename__ = "system_events"

    id          = Column(String(36), primary_key=True, default=_uuid)
    session_id  = Column(String(36), nullable=True, index=True)
    event_type  = Column(String(64), nullable=False, index=True)
    severity    = Column(String(16), nullable=False, default="info")  # debug/info/warn/error
    message     = Column(Text, nullable=True)
    context     = Column(JSON, nullable=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(),
                         nullable=False, index=True)

    __table_args__ = (
        Index("ix_events_type_time", "event_type", "recorded_at"),
        Index("ix_events_severity", "severity", "recorded_at"),
    )


# ══════════════════════════════════════════════════════════════════
# PROMPT VERSIONING
# ══════════════════════════════════════════════════════════════════

class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id              = Column(String(36), primary_key=True, default=_uuid)
    name            = Column(String(128), nullable=False)
    prompt_type     = Column(String(64), nullable=False)  # "question_system", "eval_system"
    content         = Column(Text, nullable=False)
    version_number  = Column(Integer, nullable=False)
    is_active       = Column(Boolean, nullable=False, default=False)
    created_by_id   = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes           = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("prompt_type", "version_number", name="uq_prompt_type_version"),
        Index("ix_prompts_type_active", "prompt_type", "is_active"),
    )
