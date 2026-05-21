"""The Odds API adapter — free tier 500 req/mo.

Always pull aggressively cached via the scheduler; never call from a
user-facing endpoint without a cache check.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()

OddsFetchStatus = Literal["ok", "disabled", "unauthorized", "rate_limited", "error"]


@dataclass(frozen=True)
class OddsFetchResult:
    events: list[dict[str, Any]]
    status: OddsFetchStatus
    message: str | None = None


class TheOddsApiAdapter:
    SPORT = "americanfootball_nfl"

    def __init__(self) -> None:
        self.api_key = settings.odds_api_key.strip()
        self.base = settings.odds_api_base
        self.client = httpx.AsyncClient(timeout=15.0)

    def _enabled(self) -> bool:
        return bool(self.api_key)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _get(self, path: str, params: dict) -> OddsFetchResult:
        if not self._enabled():
            return OddsFetchResult([], "disabled", "ODDS_API_KEY is not set")
        params = {"apiKey": self.api_key, **params}
        r = await self.client.get(f"{self.base}{path}", params=params)
        if r.status_code == 401:
            log.warning("odds_api_unauthorized")
            return OddsFetchResult(
                [],
                "unauthorized",
                "The Odds API rejected ODDS_API_KEY (401). Create a key at https://the-odds-api.com",
            )
        if r.status_code == 429:
            log.warning("odds_api_rate_limited")
            return OddsFetchResult([], "rate_limited", "The Odds API rate limit was hit (429)")
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.warning("odds_api_http_error", status=r.status_code)
            return OddsFetchResult([], "error", str(e))
        data = r.json()
        if not isinstance(data, list):
            return OddsFetchResult([], "error", "Unexpected response shape from The Odds API")
        return OddsFetchResult(data, "ok")

    async def fetch_game_odds(
        self,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        regions: str = "us",
    ) -> list[dict[str, Any]]:
        """Backward-compatible: returns events only (empty on any failure)."""
        return (await self.fetch_game_odds_result(markets=markets, regions=regions)).events

    async def fetch_game_odds_result(
        self,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        regions: str = "us",
    ) -> OddsFetchResult:
        return await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "american"},
        )

    async def fetch_futures(self, market: str = "outrights") -> list[dict[str, Any]]:
        """Note: futures markets vary by season. Check endpoint availability."""
        result = await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": "us", "markets": market, "oddsFormat": "american"},
        )
        return result.events

    async def aclose(self) -> None:
        await self.client.aclose()
