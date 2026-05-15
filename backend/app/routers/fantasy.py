from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db
from ..models.player import Player
from ..models.user import User
from ..rate_limits import limiter
from ..schemas.ai import ChatRequest, ChatResponse
from ..schemas.news import NewsItemOut
from ..services import ai_service, fantasy_service, news_service
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


@router.post("/advise", response_model=ChatResponse)
@limiter.limit(get_settings().rate_limit_ai)
async def fantasy_advise(
    request: Request,
    roster: list[str] = Body(..., embed=True),
    question: str = Body(default="Give me start/sit recommendations and 2 waiver-wire targets.", embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
