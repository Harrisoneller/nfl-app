from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    espn_id: int | None = None
    name: str
    market: str
    full_name: str
    conference: str
    division: str
    primary_color: str
    secondary_color: str
    logo_url: str


class TeamWithStats(TeamOut):
    record: dict[str, Any] = {}
    stats: dict[str, Any] = {}
