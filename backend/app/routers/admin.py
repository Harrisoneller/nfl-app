"""Manual data refresh endpoints. Useful while developing."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import news_service, odds_service, players_service, scores_service, teams_service

router = APIRouter()


@router.post("/refresh/teams")
def refresh_teams(db: Session = Depends(get_db)):
    return {"added": teams_service.ensure_seeded(db)}


@router.post("/refresh/scores")
async def refresh_scores(db: Session = Depends(get_db)):
    return {"events": await scores_service.refresh_scoreboard(db)}


@router.post("/refresh/players")
async def refresh_players(db: Session = Depends(get_db)):
    return {"players": await players_service.sync_from_sleeper(db)}


@router.post("/refresh/news")
async def refresh_news(db: Session = Depends(get_db)):
    return {"items": await news_service.refresh_news(db)}


@router.post("/refresh/odds")
async def refresh_odds(db: Session = Depends(get_db)):
    return {"lines": await odds_service.refresh_odds(db)}
