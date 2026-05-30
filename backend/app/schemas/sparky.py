"""Pydantic response/request schemas for the Sparky API.

These are intentionally permissive (most detail nests under `dict`/`list`)
because the engine produces rich, evolving payloads and the service already
shapes them into JSON-safe dicts. The typed envelopes below document the stable
top-level contract the frontend relies on.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SignalOut(BaseModel):
    key: str
    label: str
    side: str
    severity: str
    magnitude: float
    weight: float
    explanation: str


class SlateOut(BaseModel):
    slate_date: str | None = None
    count: int = 0
    games: list[dict] = Field(default_factory=list)
    recommended_parlays: list[dict] = Field(default_factory=list)
    real_data_available: bool = False  # True when upcoming odds snapshots exist but no Sparky predictions yet built


class GameDetailOut(BaseModel):
    event_id: str
    prediction: dict | None = None
    movement: list[dict] = Field(default_factory=list)
    books: list[dict] = Field(default_factory=list)
    book_count: int = 0


class ParlayRequest(BaseModel):
    event_ids: list[str] = Field(..., min_length=3, max_length=3,
                                 description="Exactly three event ids to combine")
    persist: bool = False


class ParlayOut(BaseModel):
    slate_id: str
    slate_date: str
    games: list[dict] = Field(default_factory=list)
    parlays: list[dict] = Field(default_factory=list)


class AccuracyOut(BaseModel):
    sport: str
    as_of: str
    individual_picks: dict
    parlays: dict
    trends: dict


class AdminStatusOut(BaseModel):
    snapshots: int
    snapshot_events: int
    last_snapshot_at: str | None = None
    predictions: int
    last_slate_date: str | None = None
    settled_results: int
    parlay_rankings: int
    pipeline_ready: bool
    has_history_for_movement: bool


class SignalGlossaryOut(BaseModel):
    signals: list[dict]
