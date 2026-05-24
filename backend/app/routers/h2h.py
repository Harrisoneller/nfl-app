"""Head-to-head matchup endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import h2h_service

router = APIRouter()


@router.get("/{team_a}/{team_b}")
async def get_h2h(
    team_a: str,
    team_b: str,
    response: Response,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Everything the H2H page needs in one round-trip."""
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    return await h2h_service.head_to_head(db, team_a, team_b, season)
