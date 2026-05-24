"""Stats service — reads materialized player_season_stats when available."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models.player_season_stat import PlayerSeasonStat
from . import materialize_service

log = get_logger(__name__)
_adapter = NflDataPyAdapter()

CACHE_TTL_SECONDS = 60 * 60 * 6  # 6h


def _player_rows_from_db(season: int) -> list[dict[str, Any]] | None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlayerSeasonStat).where(PlayerSeasonStat.season == season)
        ).scalars().all()
        if not rows:
            return None
        out: list[dict[str, Any]] = []
        for r in rows:
            rec: dict[str, Any] = {
                "player_id": r.player_id,
                "position": r.position,
                "team": r.team_id,
            }
            if r.display_name:
                rec["player_display_name"] = r.display_name
            rec.update(r.stats or {})
            out.append(rec)
        return out
    finally:
        db.close()


async def team_season_stats(season: int) -> list[dict[str, Any]]:
    key = f"team_season_stats:{season}"
    if (v := cache.get(key)) is not None:
        return v
    rows = await _adapter.team_season_stats(season)
    cache.set(key, rows, CACHE_TTL_SECONDS)
    return rows


async def player_season_stats(season: int) -> list[dict[str, Any]]:
    key = f"player_season_stats:{season}"
    if (v := cache.get(key)) is not None:
        return v
    rows = _player_rows_from_db(season)
    if rows is None:
        rows = await _adapter.seasonal_player_stats(season)
    cache.set(key, rows, CACHE_TTL_SECONDS)
    return rows


async def team_aggregate(season: int, team_id: str) -> dict[str, Any]:
    """Team aggregates from materialized PBP metrics when present."""
    key = f"team_agg:{season}:{team_id}"
    if (v := cache.get(key)) is not None:
        return v

    db = SessionLocal()
    try:
        aggs = materialize_service.load_team_aggregates(db, season)
    finally:
        db.close()

    if aggs and team_id in aggs:
        metrics = aggs[team_id]
        out = {
            "team_id": team_id,
            "season": season,
            "source": "materialized_pbp",
            **{k: v for k, v in metrics.items() if v is not None},
        }
        cache.set(key, out, CACHE_TTL_SECONDS)
        return out

    # Hot path fallback: prefer seasonal team rows over per-player weekly scans.
    season_rows = await team_season_stats(season)
    row = next(
        (
            r for r in season_rows
            if (r.get("team_id") or r.get("team") or r.get("recent_team")) == team_id
        ),
        None,
    )
    if row is not None:
        out = {
            "team_id": team_id,
            "season": season,
            "source": "team_season_stats_fallback",
            **{k: v for k, v in row.items() if k not in {"team_id", "team", "recent_team"}},
        }
        cache.set(key, out, CACHE_TTL_SECONDS)
        return out

    out = {"team_id": team_id, "season": season, "source": "no_materialized_data"}
    cache.set(key, out, CACHE_TTL_SECONDS)
    return out


async def player_aggregate(season: int, full_name: str) -> dict[str, Any] | None:
    key = f"player_agg:{season}:{full_name}"
    if (v := cache.get(key)) is not None:
        return v
    rows = await player_season_stats(season)
    for row in rows:
        if (row.get("player_display_name") or row.get("player_name")) == full_name:
            cache.set(key, row, CACHE_TTL_SECONDS)
            return row
    return None
