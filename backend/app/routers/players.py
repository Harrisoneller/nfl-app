from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.news import NewsItemOut
from ..schemas.player import PlayerOut
from ..services import (
    analytics_service,
    news_service,
    player_insights_service,
    player_predictions_service,
    player_props_service,
    players_service,
)
from ..utils.seasons import latest_completed_season

router = APIRouter()


# NOTE: static multi-segment paths are registered before "/{player_id}" so they
# never collide with the id catch-all.


@router.get("/projections/leaderboard")
async def projections_leaderboard(
    season: int | None = None,
    position: str | None = Query(default=None, description="QB | RB | WR | TE"),
    scoring: str = Query(default="ppr", description="ppr | half_ppr | standard"),
    sort: str | None = Query(
        default=None,
        description="Projected stat key to rank by (e.g. passing_yards) or 'fantasy'",
    ),
    limit: int = Query(default=100, le=300),
    db: Session = Depends(get_db),
):
    """Season stat-projection leaderboard (v2 engine). Roster-active players
    only; sortable by any projected stat, with fantasy as the composite."""
    return await player_predictions_service.projection_leaderboard(
        db, season=season, position=position, scoring=scoring, sort=sort, limit=limit
    )


@router.get("/projections/weekly")
async def projections_weekly(
    season: int | None = None,
    week: int | None = Query(default=None, description="NFL week; default = current slate"),
    position: str | None = Query(default=None, description="QB | RB | WR | TE"),
    scoring: str = Query(default="ppr", description="ppr | half_ppr | standard"),
    limit: int = Query(default=400, le=600),
    db: Session = Depends(get_db),
):
    """Slate-wide weekly projections with start/sit tiers, matchup grades,
    boom/bust bands, game environment and weather — the weekly board."""
    return await player_predictions_service.weekly_projection_board(
        db, season=season, week=week, scoring=scoring, position=position, limit=limit
    )


@router.get("/compare/projections")
async def compare_projections(
    ids: str = Query(..., description="2–4 comma-separated player ids"),
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Side-by-side comparison: season projection distributions, next-game
    distributions, usage trends and consistency profiles."""
    return await player_insights_service.compare_players(
        db, ids.split(","), season=season
    )


@router.get("/props/board")
async def props_board(
    market: str | None = Query(default=None, description="Odds API market key"),
    event_id: str | None = None,
    position: str | None = Query(default=None, description="QB | RB | WR | TE"),
    q: str | None = Query(default=None, description="Player name filter"),
    limit: int = Query(default=250, le=500),
    db: Session = Depends(get_db),
):
    """The Prop Finder workbench: per-book prices, best price per side, model
    probability at each book's exact line, filterable by market/game/position."""
    return await player_props_service.prop_board(
        db, market=market, event_id=event_id, position=position, q=q, limit=limit
    )


@router.get("/props/edges")
async def props_edges(
    min_edge: float = Query(default=0.04, ge=0.0, le=0.5),
    min_books: int = Query(default=2, ge=1),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    """Slate-wide player-prop edges: model P(over) vs de-vigged market P(over)."""
    return await player_props_service.prop_edges(
        db, min_edge=min_edge, min_books=min_books, limit=limit
    )


@router.get("/props/status")
def props_status(db: Session = Depends(get_db)):
    return player_props_service.props_status(db)


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


@router.get("/{player_id}/props")
async def get_player_props(player_id: str, db: Session = Depends(get_db)):
    """Current prop markets for this player: consensus line, de-vigged market
    P(over), model P(over) from the projection distribution, and the edge."""
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    return await player_props_service.props_for_player(db, player_id)


@router.get("/{player_id}/over-prob")
async def get_over_probability(
    player_id: str,
    stat: str = Query(..., description="e.g. receiving_yards, or anytime_td"),
    line: float = Query(default=0.0),
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """P(stat > line) in the player's next game — the custom-line calculator.

    Derived from the SAME distribution as the projections and prop edges."""
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    stat_key = "__anytime_td__" if stat in ("anytime_td", "__anytime_td__") else stat
    return await player_predictions_service.stat_over_probability(
        db, player_id, stat_key, line, season
    )


@router.get("/{player_id}/usage")
async def get_player_usage(
    player_id: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Weekly usage series (targets, carries, opportunity shares) plus a
    consistency profile (PPG, floor, ceiling, volatility)."""
    p = players_service.get_player(db, player_id)
    if not p:
        raise HTTPException(404, "player not found")
    return await player_insights_service.usage_profile(p, season)


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
