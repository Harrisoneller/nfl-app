"""Admin-only custom fantasy ranking endpoints.

Backs the admin "Fantasy Rankings" board: named ranking sets, full-list
reorders, tier breaks, seeding from projections, and draft → publish.
Public consumption lives in the fantasy router (published snapshots only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models.ranking import RANKING_FORMATS
from ..models.user import User
from ..services import rankings_service

router = APIRouter(dependencies=[Depends(require_admin)])


class SetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    season: int | None = None
    format: str = Field(default="custom")
    description: str = Field(default="", max_length=500)


class SetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    format: str | None = None
    description: str | None = Field(default=None, max_length=500)


class EntryIn(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    tier: int = Field(default=1, ge=1, le=50)
    note: str = Field(default="", max_length=200)


class EntriesReplace(BaseModel):
    entries: list[EntryIn]


class SeedRequest(BaseModel):
    source: str = Field(default="ros_vorp", pattern="^(ros_vorp|season_total)$")
    scoring: str | None = Field(default=None, pattern="^(ppr|half_ppr|standard)$")
    position: str | None = None
    limit: int = Field(default=200, ge=1, le=500)


@router.get("")
def list_sets(season: int | None = None, db: Session = Depends(get_db)):
    return {
        "formats": list(RANKING_FORMATS),
        "sets": rankings_service.list_sets(db, season=season),
    }


@router.post("")
def create_set(
    body: SetCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        s = rankings_service.create_set(
            db, name=body.name, season=body.season, format=body.format,
            description=body.description, created_by=admin.email or "",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    return rankings_service.get_set_detail(db, s.id)


@router.get("/{set_id}")
def get_set(set_id: int, db: Session = Depends(get_db)):
    d = rankings_service.get_set_detail(db, set_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found")
    return d


@router.patch("/{set_id}")
def update_set(
    set_id: int,
    body: SetUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        s = rankings_service.update_set(
            db, set_id, name=body.name, format=body.format,
            description=body.description, actor=admin.email or "",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found")
    return rankings_service.get_set_detail(db, set_id)


@router.delete("/{set_id}")
def delete_set(
    set_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not rankings_service.delete_set(db, set_id, actor=admin.email or ""):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found")
    return {"deleted": set_id}


@router.put("/{set_id}/entries")
def replace_entries(
    set_id: int,
    body: EntriesReplace,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return rankings_service.replace_entries(
            db, set_id,
            [e.model_dump() for e in body.entries],
            actor=admin.email or "",
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found") from None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.post("/{set_id}/seed")
async def seed(
    set_id: int,
    body: SeedRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return await rankings_service.seed_from_projections(
            db, set_id, source=body.source, scoring=body.scoring,
            position=body.position, limit=body.limit, actor=admin.email or "",
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found") from None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.post("/{set_id}/publish")
def publish(
    set_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return rankings_service.publish(db, set_id, actor=admin.email or "")
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found") from None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.post("/{set_id}/unpublish")
def unpublish(
    set_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return rankings_service.unpublish(db, set_id, actor=admin.email or "")
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ranking set not found") from None
