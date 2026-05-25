from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.news import NewsItemOut
from ..schemas.player import PlayerOut
from ..services import analytics_service, news_service, players_service
from ..utils.seasons import latest_completed_season

router = APIRouter()


@router.get("", response_model=list[PlayerOut])
def list_players(
    response: Response,
    q: str | None = Query(default=None, description="Name search"),
    position: str | None = None,
    team_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    response.headers["X-Cache-Status"] = "hit"
    return players_service.list_players(
        db, search=q, position=position, team_id=team_id, limit=limit, offset=offset
    )


@router.get("/{player_id}", response_model=PlayerOut)
def get_player(player_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["X-Cache-Status"] = "hit"
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    return p


@router.get("/{player_id}/profile")
async def get_profile(
    player_id: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    season = season or latest_completed_season()
    return await analytics_service.player_profile(
        player_id=player_id, full_name=p.full_name, position=p.position, season=season
    )


@router.get("/{player_id}/gamelog")
async def get_gamelog(
    player_id: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    season = season or latest_completed_season()
    return await analytics_service.player_gamelog(
        player_id=player_id, full_name=p.full_name, season=season
    )


@router.get("/{player_id}/trend")
async def get_trend(
    player_id: str,
    metric: str = Query(..., description="Metric key from /profile"),
    start: int | None = None,
    end: int | None = None,
    db: Session = Depends(get_db),
):
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    end = end or latest_completed_season()
    start = start or (end - 4)
    seasons = list(range(start, end + 1))
    return await analytics_service.player_trend(
        player_id=player_id, full_name=p.full_name, position=p.position,
        seasons=seasons, metric=metric,
    )


@router.get("/{player_id}/news", response_model=list[NewsItemOut])
def get_player_news(
    player_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Substring search news for a player by full name."""
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    return news_service.search_news_by_text(db, p.full_name, limit=limit)
