from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _emit_trusted_kickoff(cls, value):
        """API kickoffs are real UTC, including unrepaired nflverse rows."""
        if isinstance(value, dict) or not hasattr(value, "start_time"):
            return value
        from ..services.scores_service import trusted_kickoff_of

        kick = trusted_kickoff_of(value)
        return {
            "id": value.id,
            "season": value.season,
            "week": value.week,
            "season_type": getattr(value, "season_type", 2),
            "start_time": kick,
            "status": value.status,
            "status_detail": getattr(value, "status_detail", "") or "",
            "home_team_id": value.home_team_id,
            "away_team_id": value.away_team_id,
            "home_score": value.home_score,
            "away_score": value.away_score,
            "venue": getattr(value, "venue", "") or "",
            "broadcast": getattr(value, "broadcast", "") or "",
        }

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
