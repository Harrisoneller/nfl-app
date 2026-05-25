"""endpoint SLO snapshots + experiment events

Revision ID: 0008_endpoint_slo_experiments
Revises: 0007_feature_store_and_lifecycle
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_endpoint_slo_experiments"
down_revision: Union[str, None] = "0007_feature_store_and_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "endpoint_slo_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("endpoint_key", sa.String(length=160), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("status_bucket", sa.String(length=16), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("p50_ms", sa.Float(), nullable=False),
        sa.Column("p95_ms", sa.Float(), nullable=False),
        sa.Column("p99_ms", sa.Float(), nullable=False),
        sa.Column("cache_hit_rate", sa.Float(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_endpoint_slo_snapshots_endpoint_window",
        "endpoint_slo_snapshots",
        ["endpoint_key", "window_started_at"],
    )
    op.create_index(
        "ix_endpoint_slo_snapshots_created_at",
        "endpoint_slo_snapshots",
        ["created_at"],
    )

    op.create_table(
        "experiment_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment_key", sa.String(length=120), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("page", sa.String(length=80), nullable=False),
        sa.Column("card_key", sa.String(length=80), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_events_experiment_created",
        "experiment_events",
        ["experiment_key", "created_at"],
    )
    op.create_index(
        "ix_experiment_events_variant_created",
        "experiment_events",
        ["variant", "created_at"],
    )
    op.create_index(
        "ix_experiment_events_session_created",
        "experiment_events",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("experiment_events")
    op.drop_table("endpoint_slo_snapshots")
