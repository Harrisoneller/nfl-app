"""Sleeper API adapter — free, no key, gold standard for player metadata."""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


class SleeperAdapter:
    BASE = "https://api.sleeper.app/v1"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=15.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _get(self, path: str) -> Any:
        r = await self.client.get(f"{self.BASE}{path}")
        r.raise_for_status()
        return r.json()

    async def fetch_all_players(self) -> dict[str, dict[str, Any]]:
        return await self._get("/players/nfl")

    async def fetch_trending(
        self, kind: str = "add", lookback_hours: int = 24, limit: int = 25
    ) -> list[dict]:
        return await self._get(
            f"/players/nfl/trending/{kind}?lookback_hours={lookback_hours}&limit={limit}"
        )

    async def aclose(self) -> None:
        await self.client.aclose()
