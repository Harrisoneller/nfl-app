"""Meta endpoints — things the UI needs that don't fit elsewhere."""
from __future__ import annotations

from fastapi import APIRouter

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
