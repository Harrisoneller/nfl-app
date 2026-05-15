from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    season: int
    week: int | None = None
    season_type: int
    start_time: datetime | None = None
    status: str
    status_detail: str
    home_team_id: str | None = None
    away_team_id: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    venue: str
    broadcast: str
