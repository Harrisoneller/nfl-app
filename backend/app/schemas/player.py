from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    position: str
    team_id: str | None = None
    jersey_number: int | None = None
    age: int | None = None
    height: str | None = None
    weight: int | None = None
    college: str | None = None
    status: str
    metadata_json: dict[str, Any] = {}
