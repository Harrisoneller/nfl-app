from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.odds import OddsLineOut
from ..services import odds_service

router = APIRouter()


@router.get("", response_model=list[OddsLineOut])
def list_odds(market: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    return odds_service.list_odds(db, market=market, limit=limit)


@router.get("/event/{event_id}", response_model=list[OddsLineOut])
def event_odds(event_id: str, db: Session = Depends(get_db)):
    return odds_service.get_event_odds(db, event_id)
