"""Head-to-head matchup endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import h2h_service

router = APIRouter()


@router.get("/{team_a}/{team_b}")
async def get_h2h(
    team_a: str,
    team_b: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Everything the H2H page needs in one round-trip."""
    return await h2h_service.head_to_head(db, team_a, team_b, season)
