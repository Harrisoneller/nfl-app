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
    # Populated only by /admin/build_real so the UI can surface the upstream
    # Odds API result ("ok" / "rate_limited" / "skipped_fresh" / "error" + counts).
    odds_refresh: dict | None = None


class GameDetailOut(BaseModel):
    event_id: str
    prediction: dict | None = None
    movement: list[dict] = Field(default_factory=list)
    books: list[dict] = Field(default_factory=list)
    book_count: int = 0


class ParlayRequest(BaseModel):
    # The engine accepts 2..8 legs (matches parlay.MIN_LEGS / parlay.MAX_LEGS).
    # The service additionally rejects duplicate event_ids.
    event_ids: list[str] = Field(..., min_length=2, max_length=8,
                                 description="Between 2 and 8 unique event ids to combine into a parlay")
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
