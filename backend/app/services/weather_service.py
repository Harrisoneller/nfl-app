"""Weather enrichment for upcoming games."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..adapters.data.weather import OpenMeteoAdapter
from ..cache import cache

CACHE_TTL = 60 * 60 * 2  # 2 hours (forecasts shift, but not minute-to-minute)


async def forecasts_for_games(
    games: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Returns { game_id: forecast }. Cached individually so refreshes are cheap.

    Each `game` dict must include `id`, `home_team_id`, and one of `gameday`
    or `start_time` for kickoff.
    """
    adapter = OpenMeteoAdapter()
    out: dict[str, dict[str, Any]] = {}
    try:
        for g in games:
            gid = g.get("id") or ""
            if not gid:
                continue
            home = g.get("home_team_id")
            if not home:
                out[gid] = {"available": False}
                continue
            kickoff = _kickoff(g)
            cache_key = f"weather:{home}:{kickoff.isoformat() if kickoff else 'na'}"
            cached = cache.get(cache_key)
            if cached is not None:
                out[gid] = cached
                continue
            forecast = await adapter.forecast_for_game(home, kickoff)
            cache.set(cache_key, forecast, CACHE_TTL)
            out[gid] = forecast
    finally:
        await adapter.aclose()
    return out


def _kickoff(g: dict[str, Any]) -> datetime | None:
    # Prefer ISO start_time; fall back to gameday date with 1pm kickoff.
    st = g.get("start_time")
    if st:
        try:
            return datetime.fromisoformat(str(st).replace("Z", "+00:00"))
        except Exception:
            pass
    gd = g.get("gameday")
    if gd:
        try:
            return datetime.fromisoformat(str(gd) + "T13:00:00+00:00")
        except Exception:
            return None
    return None
