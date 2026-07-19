"""Admin-only projection override endpoints.

Backs the /admin page: list active overrides, upsert one, revert one.
Every route requires ``require_admin`` (ADMIN_EMAILS allowlist when set,
else the DB is_admin flag) — same gate as Sparky's admin tab.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models.user import User
from ..services import overrides_service

router = APIRouter(dependencies=[Depends(require_admin)])


class OverrideUpsert(BaseModel):
    entity_type: str = Field(pattern="^(game|player)$")
    entity_id: str = Field(min_length=1, max_length=64)
    field: str = Field(min_length=1, max_length=48)
    value: float
    season: int | None = None
    week: int | None = Field(default=None, ge=1, le=23)
    original_value: float | None = None
    note: str = Field(default="", max_length=500)


@router.get("")
def list_overrides(
    entity_type: str | None = None,
    entity_id: str | None = None,
    season: int | None = None,
    week: int | None = None,
    db: Session = Depends(get_db),
):
    return {
        "overrides": overrides_service.list_overrides(
            db, entity_type=entity_type, entity_id=entity_id,
            season=season, week=week,
        )
    }


@router.post("")
def upsert_override(
    body: OverrideUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return overrides_service.upsert_override(
            db,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            field=body.field,
            value=body.value,
            season=body.season,
            week=body.week,
            original_value=body.original_value,
            note=body.note,
            created_by=admin.email or "",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.delete("/{override_id}")
def delete_override(override_id: int, db: Session = Depends(get_db)):
    if not overrides_service.delete_override(db, override_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found")
    return {"deleted": override_id}
