from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import comparison_service, metrics_index_service
from ..utils.seasons import latest_completed_season

router = APIRouter()


@router.get("/compare/teams")
async def compare_teams(
    teams: str = Query(..., description="Comma-separated team ids"),
    season: int = 2024,
):
    ids = [t.strip().upper() for t in teams.split(",") if t.strip()]
    if not (2 <= len(ids) <= 8):
        raise HTTPException(400, "Provide 2–8 team ids")
    return await comparison_service.compare_teams(ids, season)


@router.get("/compare/team-vs-league")
async def compare_team_vs_league(team: str, season: int = 2024):
    return await comparison_service.compare_team_to_league(team.upper(), season)


@router.get("/compare/players")
async def compare_players(
    names: str = Query(..., description="Comma-separated full names"),
    season: int = 2024,
):
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    if not (2 <= len(name_list) <= 8):
        raise HTTPException(400, "Provide 2–8 player names")
    return await comparison_service.compare_players(name_list, season)


@router.get("/metrics/catalog")
def metrics_catalog(position: str | None = None):
    """Valid metric names for leader queries."""
    return {
        "team_metrics": metrics_index_service.team_metric_names(),
        "player_metrics": metrics_index_service.player_metric_names(position),
    }


@router.get("/leaders")
def metric_leaders(
    db: Session = Depends(get_db),
    season: int | None = None,
    metric: str = Query(..., description="Metric key, e.g. off_epa_per_play"),
    entity: Literal["team", "player"] = "team",
    position: str | None = Query(None, description="QB, RB, WR, TE for player entity"),
    team_id: str | None = Query(None, description="Filter players by team"),
    sort_by: Literal["percentile", "value"] = "percentile",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(25, ge=1, le=100),
    min_value: float | None = None,
):
    """SQL-backed leaderboard — sort/filter without loading pandas."""
    season = season or latest_completed_season()
    out = metrics_index_service.query_leaders(
        db,
        season=season,
        metric=metric,
        entity=entity,
        position=position,
        team_id=team_id,
        sort_by=sort_by,
        order=order,
        limit=limit,
        min_value=min_value,
    )
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.get("/metrics/index-status")
def metrics_index_status(db: Session = Depends(get_db), season: int | None = None):
    return {"seasons": metrics_index_service.index_status(db, season)}
