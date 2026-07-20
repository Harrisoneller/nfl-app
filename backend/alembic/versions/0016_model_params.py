"""Global tuning layer: model_params, admin_audit_log, model_param_presets.

Revision ID: 0016_model_params
Revises: 0015_admin_overrides
Create Date: 2026-07-19

model_params        — DB overrides for registry-declared tunables (delete = revert).
admin_audit_log     — append-only record of every param/override/preset action.
model_param_presets — named full-configuration snapshots.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_model_params"
down_revision: Union[str, Sequence[str], None] = "0015_admin_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "model_params",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default=""),
        *_timestamps(),
    )
    op.create_index("ix_model_params_key", "model_params", ["key"], unique=True)

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_key", sa.String(length=160), nullable=False),
        sa.Column("old_value", sa.Float(), nullable=True),
        sa.Column("new_value", sa.Float(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_admin_audit_target", "admin_audit_log", ["target_type", "target_key"])
    op.create_index("ix_admin_audit_created", "admin_audit_log", ["created_at"])

    op.create_table(
        "model_param_presets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        *_timestamps(),
    )
    op.create_index("ix_model_param_presets_name", "model_param_presets", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_model_param_presets_name", table_name="model_param_presets")
    op.drop_table("model_param_presets")
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_target", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index("ix_model_params_key", table_name="model_params")
    op.drop_table("model_params")
