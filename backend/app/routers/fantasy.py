from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user_optional, get_db
from ..models.player import Player
from ..models.user import User
from ..rate_limits import limiter
from ..schemas.ai import ChatResponse
from ..schemas.news import NewsItemOut
from ..services import ai_service, fantasy_insights_service, fantasy_service, news_service
from ..services.cost_service import BudgetExceeded

router = APIRouter()


@router.post("/roster")
def enrich_roster(
    names_or_ids: list[str] = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    rows = fantasy_service.enrich_roster(db, names_or_ids)
    return {"rows": rows, "summary": fantasy_service.trending_summary(rows)}


@router.get("/news", response_model=list[NewsItemOut])
async def fantasy_news(limit: int = 30, db: Session = Depends(get_db)):
    return await news_service.list_fantasy_news(db, limit=limit)


@router.get("/trending")
async def trending_players(
    kind: str = "add",
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Sleeper trending adds/drops, joined to our player records."""
    rows = await news_service.fetch_sleeper_trending(kind=kind, limit=limit)
    if not rows:
        return {"kind": kind, "items": []}

    ids = [r.get("player_id") for r in rows if r.get("player_id")]
    players = {p.id: p for p in db.query(Player).filter(Player.id.in_(ids)).all()}
    out = []
    for r in rows:
        pid = r.get("player_id")
        p = players.get(pid) if pid else None
        out.append({
            "player_id": pid,
            "count": r.get("count"),
            "name": p.full_name if p else None,
            "position": p.position if p else None,
            "team": p.team_id if p else None,
            "injury_status": ((p.metadata_json or {}).get("injury_status") if p else None),
        })
    return {"kind": kind, "items": out}


@router.get("/ros")
async def ros_values(
    season: int | None = None,
    scoring: str = "ppr",
    league_size: int = 12,
    position: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Rest-of-season fantasy values: VORP over positional replacement,
    scarcity tiers, and per-game pace — the draft/trade currency board."""
    return await fantasy_insights_service.ros_value_board(
        db, season=season, scoring=scoring, league_size=league_size,
        position=position, limit=limit,
    )


@router.get("/waivers")
async def waiver_wire(
    season: int | None = None,
    scoring: str = "ppr",
    limit: int = 25,
    db: Session = Depends(get_db),
):
    """Model-checked waiver targets: Sleeper trending adds scored against ROS
    value and next-3-week schedule ease."""
    return await fantasy_insights_service.waiver_targets(
        db, season=season, scoring=scoring, limit=limit,
    )


@router.post("/trade")
async def trade_analyzer(
    side_a: list[str] = Body(..., embed=True),
    side_b: list[str] = Body(..., embed=True),
    scoring: str = Body(default="ppr", embed=True),
    league_size: int = Body(default=12, embed=True),
    season: int | None = Body(default=None, embed=True),
    db: Session = Depends(get_db),
):
    """Grade a proposed trade by summed ROS VORP, uncertainty included."""
    return await fantasy_insights_service.analyze_trade(
        db, side_a, side_b, season=season, scoring=scoring, league_size=league_size,
    )


@router.post("/advise", response_model=ChatResponse)
@limiter.limit(get_settings().rate_limit_ai)
async def fantasy_advise(
    request: Request,
    roster: list[str] = Body(..., embed=True),
    question: str = Body(default="Give me start/sit recommendations and 2 waiver-wire targets.", embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """One-shot AI advisor — passes the user's roster + question into the chat tool loop."""
    msg = (
        f"My fantasy roster: {', '.join(roster)}.\n\n"
        f"Question: {question}\n\n"
        "Use the available tools to look up player stats, injury status, and recent news, "
        "then return a concise, prioritized recommendation."
    )
    try:
        return await ai_service.chat(db, user_id=user.id, user_message=msg, enable_tools=True)
    except BudgetExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
