"""Unified admin audit trail — one append-only log for every tuning action.

Both the parameter layer (model_params_service) and the entity-override layer
(overrides_service) record here, so the admin Change Log renders one
chronological timeline: "who changed what, from what, to what, and why" for
params, game/player/team overrides, and preset operations alike.

Logging is best-effort by design: an audit failure must never abort the
underlying write. Rows are never mutated.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.model_param import AdminAuditLog

log = get_logger(__name__)


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    target_type: str,
    target_key: str,
    old_value: float | None = None,
    new_value: float | None = None,
    note: str = "",
    context: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    """Append one audit row. ``commit=False`` joins the caller's transaction."""
    try:
        db.add(AdminAuditLog(
            actor=actor or "",
            action=action,
            target_type=target_type,
            target_key=target_key[:160],
            old_value=old_value,
            new_value=new_value,
            note=(note or "")[:500],
            context_json=json.dumps(context or {}, default=str)[:4000],
        ))
        if commit:
            db.commit()
    except Exception:  # noqa: BLE001 — audit must never break the write path
        log.warning("audit record failed (%s %s)", action, target_key, exc_info=True)


def timeline(
    db: Session,
    *,
    target_type: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    search: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> dict[str, Any]:
    """Filterable, id-cursor-paginated change log (newest first)."""
    q = db.query(AdminAuditLog)
    if target_type:
        q = q.filter(AdminAuditLog.target_type == target_type)
    if action:
        q = q.filter(AdminAuditLog.action == action)
    if actor:
        q = q.filter(AdminAuditLog.actor.ilike(f"%{actor}%"))
    if search:
        q = q.filter(AdminAuditLog.target_key.ilike(f"%{search}%"))
    if before_id is not None:
        q = q.filter(AdminAuditLog.id < before_id)
    limit = max(1, min(limit, 500))
    rows = q.order_by(AdminAuditLog.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "entries": [r.to_dict() for r in rows],
        "has_more": has_more,
        "next_before_id": rows[-1].id if rows and has_more else None,
    }
