from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OddsLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market: str
    event_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    commence_time: datetime | None = None
    bookmaker: str
    label: str
    price: int | None = None
    point: float | None = None
