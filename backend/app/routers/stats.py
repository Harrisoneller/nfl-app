from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services import comparison_service

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
