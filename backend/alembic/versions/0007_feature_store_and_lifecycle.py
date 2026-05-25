"""feature_snapshots + model_lifecycle_runs

Revision ID: 0007_feature_store_and_lifecycle
Revises: 0006_metric_index
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_feature_store_and_lifecycle"
down_revision: Union[str, None] = "0006_metric_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("game_id", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("feature_set_version", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season",
            "week",
            "game_id",
            "entity_id",
            "feature_set_version",
            "model_version",
            name="uq_feature_snapshot_identity",
        ),
    )
    op.create_index(
        "ix_feature_snapshots_lookup",
        "feature_snapshots",
        ["season", "week", "game_id", "entity_id"],
    )
    op.create_index("ix_feature_snapshots_created_at", "feature_snapshots", ["created_at"])

    op.create_table(
        "model_lifecycle_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_key", sa.String(length=64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("is_promoted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("champion_model_version", sa.String(length=64), nullable=True),
        sa.Column("train_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("backtest_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calibration_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compare_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gate_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_key"),
    )
    op.create_index("ix_model_lifecycle_runs_started_at", "model_lifecycle_runs", ["started_at"])
    op.create_index("ix_model_lifecycle_runs_model_version", "model_lifecycle_runs", ["model_version"])
    op.create_index("ix_model_lifecycle_runs_promoted", "model_lifecycle_runs", ["is_promoted"])


def downgrade() -> None:
    op.drop_table("model_lifecycle_runs")
    op.drop_table("feature_snapshots")
