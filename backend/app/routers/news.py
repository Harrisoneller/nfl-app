from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.news import NewsItemOut
from ..services import news_service

router = APIRouter()


@router.get("", response_model=list[NewsItemOut])
def list_news(
    limit: int = 30,
    source: str | None = None,
    team: str | None = None,
    db: Session = Depends(get_db),
):
    return news_service.list_news(db, limit=limit, source=source, team_id=team)


@router.get("/search", response_model=list[NewsItemOut])
def search_news(q: str, limit: int = 25, db: Session = Depends(get_db)):
    """Substring search on title/summary — used by player news feeds."""
    return news_service.search_news_by_text(db, q, limit=limit)
