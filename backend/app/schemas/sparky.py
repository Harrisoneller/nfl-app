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


class WeekOut(BaseModel):
    week: int
    label: str
    start: str | None = None
    end: str | None = None
    #: Games the schedule says this week contains.
    games: int = 0
    #: Games Sparky has actually built a prediction for. `games` high with
    #: `priced` at zero means an unbuilt slate, not a quiet week.
    priced: int = 0


class WeeksOut(BaseModel):
    season: int
    current_week: int | None = None
    weeks: list[WeekOut] = Field(default_factory=list)
    #: False when no schedule is loaded (offseason, fresh DB). The board falls
    #: back to a time window in that case rather than showing nothing.
    schedule_available: bool = True


class SlateOut(BaseModel):
    slate_date: str | None = None
    season: int | None = None
    week: int | None = None
    count: int = 0
    games: list[dict] = Field(default_factory=list)
    #: Games in the selected week, including ones already kicked off.
    week_game_count: int = 0
    #: How many of those are hidden because they have started. Reported so a
    #: board that shrinks through the week explains itself.
    started_count: int = 0
    include_started: bool = False
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


class PriceTicketRequest(BaseModel):
    """One hand-built ticket, named leg by leg.

    A leg key is ``{event_id}:{market}:{side}`` for a moneyline and
    ``{event_id}:{market}:{side}:{line}`` for a spread or total — the line is
    part of the identity because alternate numbers are quoted as separate bets.
    Keys come from ``GET /sparky/parlay/legs`` or from a value-board row.

    Legs may be mixed freely across games and markets, and more than one leg may
    come from the same game: those are collapsed into a single unit and priced
    on the joint outcome distribution before the cross-game model sees them.
    """

    leg_keys: list[str] = Field(
        ..., min_length=2, max_length=8,
        description="Between 2 and 8 unique leg keys, in any mix of games and markets",
    )


class LegMenuOut(BaseModel):
    """Every priceable side of the requested games, grouped by game."""

    games: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


class ParlayOut(BaseModel):
    slate_id: str
    slate_date: str
    games: list[dict] = Field(default_factory=list)
    parlays: list[dict] = Field(default_factory=list)
    #: Requested games that produced no priceable side, each with the reason.
    #: A parlay needs one leg per game, so any entry here means no ticket could
    #: be built — reported rather than raised, so the UI can name the game.
    unavailable_events: list[dict] = Field(default_factory=list)
    #: Headline EV for whatever was built, positive or not.
    ev_summary: dict = Field(default_factory=dict)
    # The candidate legs the search ran over, and how the engine was configured
    # (fitted edge-trust lambda, calibration map, filters). Surfaced so a user
    # can see *why* a slate produced nothing, which is a common and correct
    # outcome under the strict +EV gate.
    legs: list[dict] = Field(default_factory=list)
    #: One entry per game that contributed more than one leg, priced off the
    #: joint (margin, total) distribution. Carries the fair price for that
    #: combination and how much book shading it can absorb — the EV against the
    #: multiplied leg prices is a hypothetical, because books do not offer that
    #: price on correlated legs.
    same_game: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    message: str | None = None


class ParlayBoardOut(BaseModel):
    """Slate-wide search: the best tickets available anywhere on the board."""

    slate_date: str
    #: Tickets Sparky will stand behind — +EV after shrinkage and the selection
    #: penalty. Frequently empty, which is the correct answer.
    parlays: list[dict] = Field(default_factory=list)
    #: What the board actually offers when nothing qualifies, priced honestly
    #: (usually negative EV). Kept separate from ``parlays`` on purpose: merging
    #: them would turn a refusal into a recommendation.
    best_available: list[dict] = Field(default_factory=list)
    ev_summary: dict = Field(default_factory=dict)
    legs: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    message: str | None = None


class ValueBoardOut(BaseModel):
    """Single-bet value board across moneyline, spread and total.

    ``picks`` are the legs that survived pricing, ordered by expected
    log-bankroll growth with the recommended ones first. ``priced_out`` are the
    ones that did not, each carrying the reason — deliberately returned rather
    than dropped, because the reasons are the most instructive thing on the
    board. ``meta.rules`` echoes every threshold that was applied, so an empty
    board can explain itself.
    """

    slate_date: str | None = None
    week: int | None = None
    picks: list[dict] = Field(default_factory=list)
    #: Top rows by EV regardless of tier, so the page is never blank. A slate
    #: with no qualifying bet still shows what came closest and what stopped it.
    best_available: list[dict] = Field(default_factory=list)
    priced_out: list[dict] = Field(default_factory=list)
    by_market: dict = Field(default_factory=dict)
    #: Why the board looks the way it does: how many games the model actually
    #: had a view on, which markets were found, a histogram of rejection
    #: reasons, and actionable health warnings. Check this first when the board
    #: looks wrong — an empty board is far more often a cold prediction store
    #: than an efficient market.
    diagnostics: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)
    message: str | None = None


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
