"""Model parameter store — DB-backed values for registry-declared tunables.

Three tables power the global tuning layer (see services/param_registry.py):

* ``model_params`` — one row per tunable whose value currently differs from
  its code default. The registry resolves ``overlay → DB row → code default``,
  so deleting a row instantly reverts the parameter. Values are floats
  (int-kind params are stored as floats and rounded on read).
* ``admin_audit_log`` — append-only record of every tuning action in the app:
  parameter sets/reverts, preset saves/applies, and entity overrides
  (game/player/team) written by overrides_service. This is the single source
  for the admin Change Log timeline; rows are never updated or deleted.
* ``model_param_presets`` — named snapshots of a full parameter configuration
  ("preseason", "sharp-market weeks", …). ``params_json`` maps param key →
  value for every key that deviated from default at save time; applying a
  preset sets exactly those keys and reverts all others.
"""
from __future__ import annotations

import json

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class ModelParam(Base, TimestampMixin):
    __tablename__ = "model_params"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "note": self.note,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Audit actions (target_type: "param" | "override" | "preset").
AUDIT_ACTIONS = (
    "param_set", "param_revert", "params_revert_all",
    "preset_save", "preset_apply", "preset_delete",
    "override_set", "override_delete",
)


class AdminAuditLog(Base, TimestampMixin):
    """Append-only. One row per tuning action, param or override alike."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # param key, "entity_type:entity_id:field" for overrides, preset name.
    target_key: Mapped[str] = mapped_column(String(160), nullable=False)
    old_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # JSON blob for anything scope-shaped: season/week/entity ids, preset diff size.
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_admin_audit_target", "target_type", "target_key"),
        Index("ix_admin_audit_created", "created_at"),
    )

    def to_dict(self) -> dict:
        try:
            ctx = json.loads(self.context_json or "{}")
        except ValueError:
            ctx = {}
        return {
            "id": self.id,
            "actor": self.actor,
            "action": self.action,
            "target_type": self.target_type,
            "target_key": self.target_key,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "note": self.note,
            "context": ctx,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ModelParamPreset(Base, TimestampMixin):
    __tablename__ = "model_param_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    def params(self) -> dict[str, float]:
        try:
            raw = json.loads(self.params_json or "{}")
        except ValueError:
            return {}
        return {str(k): float(v) for k, v in raw.items()}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params": self.params(),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
