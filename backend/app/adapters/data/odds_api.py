"""The Odds API adapter — free tier 500 req/mo.

Always pull aggressively cached via the scheduler; never call from a
user-facing endpoint without a cache check.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()


class TheOddsApiAdapter:
    SPORT = "americanfootball_nfl"

    def __init__(self) -> None:
        self.api_key = settings.odds_api_key
        self.base = settings.odds_api_base
        self.client = httpx.AsyncClient(timeout=15.0)

    def _enabled(self) -> bool:
        return bool(self.api_key)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _get(self, path: str, params: dict) -> Any:
        params = {"apiKey": self.api_key, **params}
        r = await self.client.get(f"{self.base}{path}", params=params)
        if r.status_code == 401:
            log.warning("odds_api_unauthorized")
            return []
        if r.status_code == 429:
            log.warning("odds_api_rate_limited")
            return []
        r.raise_for_status()
        return r.json()

    async def fetch_game_odds(
        self,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        regions: str = "us",
    ) -> list[dict[str, Any]]:
        if not self._enabled():
            return []
        return await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "american"},
        )

    async def fetch_futures(self, market: str = "outrights") -> list[dict[str, Any]]:
        """Note: futures markets vary by season. Check endpoint availability."""
        if not self._enabled():
            return []
        # Outrights live under their own endpoint pattern.
        return await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": "us", "markets": market, "oddsFormat": "american"},
        )

    async def aclose(self) -> None:
        await self.client.aclose()
