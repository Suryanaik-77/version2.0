"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────────
    op.execute("CREATE TYPE user_role AS ENUM ('candidate', 'reviewer', 'admin')")
    op.execute("CREATE TYPE session_status AS ENUM ('pending', 'active', 'completed', 'terminated', 'error')")
    op.execute("CREATE TYPE review_status AS ENUM ('pending', 'in_review', 'reviewed', 'approved')")
    op.execute("CREATE TYPE note_type AS ENUM ('general', 'flag', 'correction', 'commendation')")

    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(255)),
        sa.Column('role', postgresql.ENUM('candidate', 'reviewer', 'admin',
                                          name='user_role', create_type=False),
                  nullable=False, server_default='candidate'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('is_verified', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_login_at', sa.DateTime(timezone=True)),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
    )
    op.create_unique_constraint('uq_users_email', 'users', ['email'])
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_role_active', 'users', ['role', 'is_active'])

    # ── refresh_tokens ─────────────────────────────────────────────────────────
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(128), nullable=False),
        sa.Column('device_hint', sa.String(255)),
        sa.Column('ip_address', sa.String(64)),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('used_at', sa.DateTime(timezone=True)),
    )
    op.create_unique_constraint('uq_rt_token_hash', 'refresh_tokens', ['token_hash'])
    op.create_index('ix_rt_token_hash', 'refresh_tokens', ['token_hash'])
    op.create_index('ix_rt_user_active', 'refresh_tokens', ['user_id', 'revoked_at'])

    # ── password_reset_tokens ──────────────────────────────────────────────────
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True)),
    )
    op.create_unique_constraint('uq_prt_token_hash', 'password_reset_tokens', ['token_hash'])

    # ── interview_sessions ─────────────────────────────────────────────────────
    op.create_table(
        'interview_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('candidate_id', sa.String(36),
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('domain', sa.String(64), nullable=False),
        sa.Column('status', postgresql.ENUM('pending', 'active', 'completed',
                                             'terminated', 'error',
                                             name='session_status', create_type=False),
                  nullable=False, server_default='pending'),
        sa.Column('end_reason', sa.String(64)),
        sa.Column('scheduled_at', sa.DateTime(timezone=True)),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('duration_secs', sa.Integer),
        sa.Column('total_turns', sa.Integer, nullable=False, server_default='0'),
        sa.Column('final_mode', sa.String(32)),
        sa.Column('avg_score', sa.Float),
        sa.Column('avg_correctness', sa.Float),
        sa.Column('avg_depth', sa.Float),
        sa.Column('ws_reconnects', sa.Integer, nullable=False, server_default='0'),
        sa.Column('provider_used', sa.String(64)),
        sa.Column('total_tokens_in', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_tokens_out', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_cost_usd', sa.Float, nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_sessions_candidate_status', 'interview_sessions',
                    ['candidate_id', 'status'])
    op.create_index('ix_sessions_started_at', 'interview_sessions', ['started_at'])
    op.create_index('ix_sessions_domain', 'interview_sessions', ['domain'])

    # ── interview_turns ────────────────────────────────────────────────────────
    op.create_table(
        'interview_turns',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('interview_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('turn_number', sa.Integer, nullable=False),
        sa.Column('question_text', sa.Text, nullable=False),
        sa.Column('answer_text', sa.Text, nullable=False),
        sa.Column('domain', sa.String(64), nullable=False),
        sa.Column('mode_at_start', sa.String(32), nullable=False),
        sa.Column('mode_at_end', sa.String(32), nullable=False),
        sa.Column('eval_scores', postgresql.JSON),
        sa.Column('avg_eval_score', sa.Float),
        sa.Column('correctness_score', sa.Integer),
        sa.Column('depth_score', sa.Integer),
        sa.Column('signals', postgresql.JSON),
        sa.Column('stt_latency_ms', sa.Integer),
        sa.Column('first_token_ms', sa.Integer),
        sa.Column('first_audio_ms', sa.Integer),
        sa.Column('turn_total_ms', sa.Integer),
        sa.Column('tokens_in', sa.Integer),
        sa.Column('tokens_out', sa.Integer),
        sa.Column('cost_usd', sa.Float),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint('uq_turn_per_session', 'interview_turns',
                                ['session_id', 'turn_number'])
    op.create_index('ix_turns_session_turn', 'interview_turns',
                    ['session_id', 'turn_number'])

    # ── session_reports ────────────────────────────────────────────────────────
    op.create_table(
        'session_reports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('interview_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('avg_accuracy', sa.Float),
        sa.Column('avg_depth', sa.Float),
        sa.Column('avg_completeness', sa.Float),
        sa.Column('avg_clarity', sa.Float),
        sa.Column('avg_maturity', sa.Float),
        sa.Column('avg_ownership', sa.Float),
        sa.Column('avg_correctness', sa.Float),
        sa.Column('overall_score', sa.Float),
        sa.Column('avg_naturalness', sa.Float),
        sa.Column('pressure_turns_pct', sa.Float),
        sa.Column('strength_summary', sa.Text),
        sa.Column('weakness_summary', sa.Text),
        sa.Column('overall_summary', sa.Text),
        sa.Column('hiring_signal', sa.String(32)),
        sa.Column('memory_snapshot', postgresql.JSON),
        sa.Column('review_status', postgresql.ENUM('pending', 'in_review',
                                                    'reviewed', 'approved',
                                                    name='review_status', create_type=False),
                  nullable=False, server_default='pending'),
        sa.Column('reviewed_by_id', sa.String(36), sa.ForeignKey('users.id')),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('reviewer_override', postgresql.JSON),
        sa.Column('generated_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint('uq_report_session', 'session_reports', ['session_id'])
    op.create_index('ix_reports_review_status', 'session_reports', ['review_status'])
    op.create_index('ix_reports_hiring_signal', 'session_reports', ['hiring_signal'])

    # ── reviewer_notes ─────────────────────────────────────────────────────────
    op.create_table(
        'reviewer_notes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('interview_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('reviewer_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('turn_number', sa.Integer),
        sa.Column('note_text', sa.Text, nullable=False),
        sa.Column('note_type', postgresql.ENUM('general', 'flag', 'correction',
                                                'commendation', name='note_type',
                                                create_type=False),
                  nullable=False, server_default='general'),
        sa.Column('is_visible_to_candidate', sa.Boolean, nullable=False,
                  server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_notes_session_reviewer', 'reviewer_notes',
                    ['session_id', 'reviewer_id'])

    # ── session_flags ──────────────────────────────────────────────────────────
    op.create_table(
        'session_flags',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('interview_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('flagged_by_id', sa.String(36), sa.ForeignKey('users.id'),
                  nullable=False),
        sa.Column('flag_type', sa.String(64), nullable=False),
        sa.Column('reason', sa.Text, nullable=False),
        sa.Column('resolved', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── integrity_records ──────────────────────────────────────────────────────
    op.create_table(
        'integrity_records',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36),
                  sa.ForeignKey('interview_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('integrity_score', sa.Integer),
        sa.Column('confidence', sa.String(16)),
        sa.Column('tab_switch_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('clipboard_event_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('devtools_detected', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('focus_loss_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('long_pause_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('ai_pattern_score', sa.Float),
        sa.Column('vocabulary_diversity', sa.Float),
        sa.Column('answer_speed_anomaly', sa.Boolean, nullable=False,
                  server_default='false'),
        sa.Column('event_log', postgresql.JSON),
        sa.Column('requires_review', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('reviewed_by_id', sa.String(36), sa.ForeignKey('users.id')),
        sa.Column('reviewer_verdict', sa.String(32)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint('uq_integrity_session', 'integrity_records', ['session_id'])
    op.create_index('ix_integrity_session', 'integrity_records', ['session_id'])

    # ── operational_metrics ────────────────────────────────────────────────────
    op.create_table(
        'operational_metrics',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36)),
        sa.Column('turn_number', sa.Integer),
        sa.Column('metric_type', sa.String(64), nullable=False),
        sa.Column('value_ms', sa.Integer),
        sa.Column('value_float', sa.Float),
        sa.Column('value_int', sa.Integer),
        sa.Column('value_json', postgresql.JSON),
        sa.Column('provider', sa.String(32)),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_metrics_type_time', 'operational_metrics',
                    ['metric_type', 'recorded_at'])
    op.create_index('ix_metrics_session_type', 'operational_metrics',
                    ['session_id', 'metric_type'])

    # ── system_events ──────────────────────────────────────────────────────────
    op.create_table(
        'system_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36)),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False, server_default='info'),
        sa.Column('message', sa.Text),
        sa.Column('context', postgresql.JSON),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_events_type_time', 'system_events', ['event_type', 'recorded_at'])
    op.create_index('ix_events_severity', 'system_events', ['severity', 'recorded_at'])

    # ── prompt_versions ────────────────────────────────────────────────────────
    op.create_table(
        'prompt_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('prompt_type', sa.String(64), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('version_number', sa.Integer, nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_by_id', sa.String(36), sa.ForeignKey('users.id'),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('notes', sa.Text),
    )
    op.create_unique_constraint('uq_prompt_type_version', 'prompt_versions',
                                ['prompt_type', 'version_number'])
    op.create_index('ix_prompts_type_active', 'prompt_versions',
                    ['prompt_type', 'is_active'])


def downgrade() -> None:
    op.drop_table('prompt_versions')
    op.drop_table('system_events')
    op.drop_table('operational_metrics')
    op.drop_table('integrity_records')
    op.drop_table('session_flags')
    op.drop_table('reviewer_notes')
    op.drop_table('session_reports')
    op.drop_table('interview_turns')
    op.drop_table('interview_sessions')
    op.drop_table('password_reset_tokens')
    op.drop_table('refresh_tokens')
    op.drop_table('users')
    op.execute("DROP TYPE IF EXISTS note_type")
    op.execute("DROP TYPE IF EXISTS review_status")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.execute("DROP TYPE IF EXISTS user_role")
