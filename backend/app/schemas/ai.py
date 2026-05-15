from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from .widget import WidgetSpec


class ChatRequest(BaseModel):
    session_id: uuid.UUID | None = None
    message: str
    enable_tools: bool = True


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    content: str
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    widget: WidgetSpec | None = None


class WidgetBuildRequest(BaseModel):
    prompt: str
    save: bool = True
