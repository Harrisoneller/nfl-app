"""FantasyFootballCalculator ADP adapter (free public JSON API, no key).

GET /api/v1/adp/{format}?teams=12&year=YYYY returns real mock-draft ADP:
    {"status": "Success", "players": [
        {"player_id": 1, "name": "...", "position": "RB", "team": "SF",
         "adp": 1.2, "high": 1, "low": 4, "stdev": 0.8, "times_drafted": 310}, ...]}

Formats: "standard" | "ppr" | "half-ppr" (also 2qb/dynasty/rookie, unused).
Best-effort: empty list on any error; caller caches via artifact_cache.
"""
from __future__ import annotations

from typing import Any

import httpx

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

# Our scoring keys → FFC path segment.
SCORING_TO_FFC = {"ppr": "ppr", "half_ppr": "half-ppr", "standard": "standard"}


class FfcAdpAdapter:
    def __init__(self) -> None:
        self._base = get_settings().ffc_adp_base.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_adp(
        self, scoring: str = "ppr", *, year: int, teams: int = 12,
    ) -> list[dict[str, Any]]:
        """ADP rows sorted by adp ascending. Empty on any failure."""
        fmt = SCORING_TO_FFC.get(scoring, "ppr")
        try:
            r = await self._http().get(
                f"{self._base}/adp/{fmt}", params={"teams": teams, "year": year},
            )
            r.raise_for_status()
            data = r.json()
            players = data.get("players") or []
            rows = [
                {
                    "name": str(p.get("name") or ""),
                    "position": str(p.get("position") or "").upper(),
                    "team": str(p.get("team") or "").upper() or None,
                    "adp": float(p["adp"]),
                    "stdev": _f(p.get("stdev")),
                    "high": _f(p.get("high")),
                    "low": _f(p.get("low")),
                    "times_drafted": int(p.get("times_drafted") or 0),
                }
                for p in players
                if p.get("adp") is not None and p.get("name")
            ]
            rows.sort(key=lambda x: x["adp"])
            return rows
        except Exception as e:  # noqa: BLE001 — ADP is an enrichment, never fatal
            log.info("ffc_adp_unavailable", error=str(e)[:160])
            return []


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
