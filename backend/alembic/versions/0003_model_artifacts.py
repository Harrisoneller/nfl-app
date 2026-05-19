"""model_artifacts table for persistent cache

Revision ID: 0003_model_artifacts
Revises: 0002_elo
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_model_artifacts"
down_revision: Union[str, None] = "0002_elo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_artifact_kind_key", "model_artifacts", ["kind", "key"], unique=True,
    )
    op.create_index("ix_model_artifact_valid_until", "model_artifacts", ["valid_until"])


def downgrade() -> None:
    op.drop_table("model_artifacts")
