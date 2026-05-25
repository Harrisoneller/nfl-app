from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.odds import OddsLineOut
from ..services import odds_service

router = APIRouter()


@router.get("/status")
def odds_status(db: Session = Depends(get_db)):
    """Whether odds are configured and present in the DB (for UI empty states)."""
    return odds_service.odds_status(db)


@router.get("", response_model=list[OddsLineOut])
def list_odds(
    response: Response,
    market: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    response.headers["X-Cache-Status"] = "hit"
    return odds_service.list_odds(db, market=market, limit=limit)


@router.get("/event/{event_id}", response_model=list[OddsLineOut])
def event_odds(event_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["X-Cache-Status"] = "hit"
    return odds_service.get_event_odds(db, event_id)
