from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.game import GameOut
from ..services import scores_service

router = APIRouter()


@router.get("", response_model=list[GameOut])
def get_scoreboard(limit: int = 32, db: Session = Depends(get_db)):
    return scores_service.list_current_games(db, limit=limit)
