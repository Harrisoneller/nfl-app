from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewsItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    source_label: str
    title: str
    summary: str
    link: str
    author: str
    image_url: str
    published_at: datetime | None = None
    team_tags: str = ""
