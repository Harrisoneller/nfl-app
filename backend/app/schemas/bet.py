"""Pydantic schemas for the bet tracker."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

MARKETS = {"spread", "total", "moneyline", "player_prop"}

# Anytime-TD props have no line — every other prop market requires one.
_LINELESS_PROP_MARKETS = {"player_anytime_td"}


class BetLegCreate(BaseModel):
    market: str
    selection: str                       # team_id, or "over"/"under" for totals/props
    selection_label: str = ""            # display, e.g. "PHI -3.5"
    line: float | None = None            # required for spread/total/most props
    odds_american: int
    event_id: str | None = None
    game_id: str | None = None
    home_team_id: str | None = None
    away_team_id: str | None = None
    commence_time: datetime | None = None
    # player_prop legs only
    player_name: str | None = None
    prop_market: str | None = None       # Odds API key, e.g. "player_rush_yds"

    @model_validator(mode="after")
    def _check(self) -> "BetLegCreate":
        m = self.market.lower().strip()
        if m not in MARKETS:
            raise ValueError(f"market must be one of {sorted(MARKETS)}")
        self.market = m
        if m in ("spread", "total") and self.line is None:
            raise ValueError(f"{m} bets require a line")
        if m == "total" and self.selection.lower() not in ("over", "under"):
            raise ValueError("total selection must be 'over' or 'under'")
        if m == "player_prop":
            if not (self.player_name or "").strip():
                raise ValueError("player_prop bets require player_name")
            if not (self.prop_market or "").strip():
                raise ValueError("player_prop bets require prop_market")
            sel = self.selection.lower().strip()
            if sel == "yes":  # anytime-TD "yes" normalizes to over
                sel = "over"
            if sel not in ("over", "under"):
                raise ValueError("player_prop selection must be 'over' or 'under'")
            self.selection = sel
            if self.line is None and self.prop_market not in _LINELESS_PROP_MARKETS:
                raise ValueError(f"{self.prop_market} bets require a line")
        if self.odds_american in (0,):
            raise ValueError("odds_american cannot be 0")
        return self


class BetCreate(BaseModel):
    bet_type: str = "straight"           # straight | parlay
    stake_units: float = Field(default=1.0, gt=0)
    stake_dollars: float | None = Field(default=None, ge=0)
    source: str = "manual"               # manual | odds | sparky
    note: str = ""
    placed_at: datetime | None = None
    legs: list[BetLegCreate]

    @model_validator(mode="after")
    def _check(self) -> "BetCreate":
        t = self.bet_type.lower().strip()
        if t not in ("straight", "parlay"):
            raise ValueError("bet_type must be 'straight' or 'parlay'")
        self.bet_type = t
        n = len(self.legs)
        if t == "straight" and n != 1:
            raise ValueError("straight bets must have exactly one leg")
        if t == "parlay" and n < 2:
            raise ValueError("parlay bets must have at least two legs")
        return self


class BetLegOut(BaseModel):
    id: int
    market: str
    selection: str
    selection_label: str
    line: float | None
    player_name: str | None = None
    prop_market: str | None = None
    odds_american: int
    odds_decimal: float
    event_id: str | None
    home_team_id: str | None
    away_team_id: str | None
    commence_time: datetime | None
    closing_line: float | None
    closing_odds_american: int | None
    clv_pct: float | None
    clv_line: float | None
    beat_close: bool | None
    leg_result: str

    class Config:
        from_attributes = True


class BetOut(BaseModel):
    id: str
    bet_type: str
    status: str
    source: str
    note: str
    stake_units: float
    stake_dollars: float | None
    odds_american: int
    odds_decimal: float
    placed_at: datetime
    settled_at: datetime | None
    payout_units: float | None
    result_units: float | None
    result_dollars: float | None
    clv_pct: float | None
    beat_close: bool | None
    legs: list[BetLegOut]

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: object) -> str:
        return str(v)

    class Config:
        from_attributes = True


class MarketRecord(BaseModel):
    won: int = 0
    lost: int = 0
    push: int = 0


class BetProfileSummary(BaseModel):
    total_bets: int
    pending: int
    settled: int
    won: int
    lost: int
    push: int
    win_rate: float | None              # won / (won + lost)

    staked_units: float                 # settled stake
    profit_units: float
    roi_pct: float | None               # profit / staked
    open_risk_units: float              # units staked on still-pending bets

    staked_dollars: float | None
    profit_dollars: float | None
    roi_dollars_pct: float | None

    # CLV — the sharp metric. avg over moneyline legs (price-based); beat-close
    # rate over all settled legs (works for every market).
    avg_clv_pct: float | None
    beat_close_pct: float | None
    legs_with_clv: int

    record_by_market: dict[str, MarketRecord]
    record_by_type: dict[str, MarketRecord]
    current_streak: int                 # +N win streak / -N losing streak (settled, by placed order)
