"""Base contracts for data-source adapters."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class ScoreboardEvent(dict):
    """Loose dict so we can ingest into our SQLAlchemy Game without a 1:1 schema."""


class ScoreboardAdapter(Protocol):
    async def fetch_current_scoreboard(self) -> list[dict[str, Any]]: ...
    async def fetch_team_schedule(
        self, team_id: str, season: int
    ) -> list[dict[str, Any]]: ...


class StatsAdapter(Protocol):
    """Returns pandas DataFrames for season aggregates / play-by-play."""

    def team_season_stats(self, season: int) -> Any: ...
    def player_season_stats(self, season: int) -> Any: ...
    def rosters(self, season: int) -> Any: ...


class OddsAdapter(Protocol):
    async def fetch_game_odds(self) -> list[dict[str, Any]]: ...
    async def fetch_futures(self, market: str) -> list[dict[str, Any]]: ...


class NewsAdapter(Protocol):
    async def fetch_news(self) -> list[dict[str, Any]]: ...


class PlayerMetaAdapter(Protocol):
    async def fetch_all_players(self) -> dict[str, dict[str, Any]]: ...
    async def fetch_trending(self, kind: str = "add", lookback_hours: int = 24) -> list[dict]: ...
