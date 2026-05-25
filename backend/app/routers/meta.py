"""Meta endpoints — things the UI needs that don't fit elsewhere."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import data_freshness_service, experiment_service
from ..utils.seasons import (
    available_seasons,
    current_or_upcoming_season,
    latest_completed_season,
    season_info,
)

router = APIRouter()


@router.get("/seasons")
def get_seasons():
    seasons = available_seasons()
    return {
        "available": seasons,
        "default": latest_completed_season(),
        "current_or_upcoming": current_or_upcoming_season(),
        "info": {s: season_info(s) for s in seasons},
    }


@router.get("/freshness")
def get_freshness(db: Session = Depends(get_db)):
    """SLA freshness status across core data modules."""
    return data_freshness_service.freshness_snapshot(db)


@router.get("/experiments/assign")
def experiment_assign(experiment_key: str, session_id: str):
    return experiment_service.assign_variant(experiment_key=experiment_key, session_id=session_id)


@router.post("/experiments/events")
def experiment_events(
    body: dict,
    db: Session = Depends(get_db),
):
    events = body.get("events") if isinstance(body, dict) else []
    if not isinstance(events, list):
        events = []
    return experiment_service.record_events(db, events)
