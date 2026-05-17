"""Betting analytics API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import betting_service

router = APIRouter()


@router.get("/teams/{team_id}/history")
async def team_history(
    team_id: str,
    seasons: str | None = Query(default=None, description="Comma-separated, e.g. 2020,2021,2022"),
):
    season_list = None
    if seasons:
        try:
            season_list = [int(s) for s in seasons.split(",") if s.strip()]
        except ValueError:
            season_list = None
    return await betting_service.team_betting_history(team_id.upper(), season_list)


@router.get("/edge")
async def edge(
    season: int | None = None,
    week: int | None = None,
    db: Session = Depends(get_db),
):
    """Current-week games with market line + edge vs our prediction."""
    return await betting_service.games_with_edge(db, season, week)


@router.get("/best-bets")
async def best_bets(season: int | None = None, db: Session = Depends(get_db)):
    """League-wide top-edge games sorted by |edge_spread|."""
    return await betting_service.best_bets(db, season)
