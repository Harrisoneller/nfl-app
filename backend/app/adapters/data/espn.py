"""ESPN public JSON adapter.

These endpoints are unofficial — they're what the ESPN web app itself uses.
They return rich game/team data with no auth required. They occasionally
move; if anything 404s, search for "espn site api scoreboard" — the
community keeps current docs.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()


class ESPNScoreboardAdapter:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.espn_base_url
        self.client = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_current_scoreboard(self) -> list[dict[str, Any]]:
        data = await self._get("/scoreboard")
        return self._normalize_events(data.get("events", []))

    async def fetch_team_schedule(self, team_id: str, season: int) -> list[dict[str, Any]]:
        # ESPN uses numeric team ids; team_id is our string code, caller should map first
        data = await self._get(f"/teams/{team_id}/schedule", params={"season": season})
        return self._normalize_events(data.get("events", []))

    async def fetch_team(self, team_id: str) -> dict[str, Any]:
        data = await self._get(f"/teams/{team_id}")
        return data.get("team", {})

    async def fetch_team_roster(self, team_id: str) -> list[dict[str, Any]]:
        data = await self._get(f"/teams/{team_id}/roster")
        roster = []
        for grp in data.get("athletes", []):
            roster.extend(grp.get("items", []))
        return roster

    @staticmethod
    def _normalize_events(events: list[dict]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            status = (ev.get("status") or {}).get("type", {})
            season = ev.get("season") or {}
            week = (ev.get("week") or {}).get("number")
            broadcasts = comp.get("broadcasts") or []
            broadcast = ", ".join(b.get("names", [""])[0] for b in broadcasts if b.get("names"))
            out.append({
                "id": str(ev.get("id")),
                "season": season.get("year"),
                "season_type": season.get("type", 2),
                "week": week,
                "start_time": ev.get("date"),
                "status": status.get("state", "scheduled"),
                "status_detail": status.get("shortDetail", ""),
                "venue": (comp.get("venue") or {}).get("fullName", ""),
                "broadcast": broadcast,
                "home": _team_from_competitor(home),
                "away": _team_from_competitor(away),
                "home_score": _safe_int(home.get("score")),
                "away_score": _safe_int(away.get("score")),
                "raw": ev,
            })
        return out

    async def aclose(self) -> None:
        await self.client.aclose()


def _safe_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _team_from_competitor(c: dict) -> dict:
    t = c.get("team", {})
    return {
        "abbrev": t.get("abbreviation"),
        "espn_id": _safe_int(t.get("id")),
        "name": t.get("displayName"),
        "logo": t.get("logo"),
    }
