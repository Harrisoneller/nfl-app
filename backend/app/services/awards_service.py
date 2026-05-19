"""Season awards — MVP, OPOY, DPOY-ish odds derived from player percentiles.

Pragmatic v1: composite scores from existing percentile data, ranked, then
normalized to "odds %" via softmax. No live betting integration (would
require paid futures data), just data-driven leaderboard.

DPOY isn't well-supported by our metric coverage — we don't track defensive
player stats granularly — so we surface a "top defenders" placeholder for
parity but lean on team defensive Elo as a proxy.
"""
from __future__ import annotations

import math
from typing import Any

from . import analytics_service, artifact_cache, elo_service
from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import latest_completed_season

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 60 * 6  # 6h

# Composite weighting per award (metric: weight, all metrics treated as %iles)
MVP_WEIGHTS = {
    "passing_yards": 0.18,
    "passing_tds": 0.18,
    "yards_per_attempt": 0.10,
    "passer_rating": 0.12,
    "epa_per_play": 0.18,
    "success_rate": 0.08,
    "fantasy_points_ppr": 0.16,
}

OPOY_WEIGHTS = {
    "receiving_yards": 0.18,
    "receiving_tds": 0.12,
    "rushing_yards": 0.18,
    "rushing_tds": 0.12,
    "fantasy_points_ppr": 0.20,
    "wopr": 0.10,
    "epa_per_play": 0.10,
}


async def _award_leaderboard(
    season: int, position_filter: list[str], weights: dict[str, float], top: int = 12,
) -> list[dict[str, Any]]:
    """Compute composite scores for the position filter, return top N."""
    df = await analytics_service._seasonal_player_table(season)  # noqa: SLF001 — internal but stable
    if df is None or len(df) == 0:
        return []
    sub = df[df["position"].isin(position_filter)] if "position" in df.columns else df

    # Compute % rank for each weight metric over the position pool
    rows = []
    for _, row in sub.iterrows():
        composite = 0.0
        weight_sum = 0.0
        has_any = False
        for metric, w in weights.items():
            if metric not in sub.columns:
                continue
            val = row.get(metric)
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            # Skip NaN
            if v != v:
                continue
            # Percentile rank within the position pool
            vals = sub[metric].dropna().astype(float).values
            if len(vals) == 0:
                continue
            pct = float((vals < v).sum() + 0.5 * (vals == v).sum()) / len(vals)
            composite += pct * w
            weight_sum += w
            has_any = True
        if not has_any:
            continue
        if weight_sum > 0:
            composite /= weight_sum
        rows.append({
            "player_id": row.get("player_id"),
            "name": row.get("player_display_name") or row.get("player_name"),
            "position": row.get("position"),
            "team": row.get("team"),
            "composite_score": round(composite * 100, 1),
        })
    rows.sort(key=lambda r: -r["composite_score"])
    return rows[:top]


def _softmax_odds(scores: list[float], temperature: float = 8.0) -> list[float]:
    """Convert raw composite scores to odds (%) via tempered softmax."""
    if not scores:
        return []
    norm = [s / 100.0 for s in scores]
    e = [math.exp(temperature * v) for v in norm]
    total = sum(e)
    return [round(100 * x / total, 1) for x in e]


async def award_leaderboards(season: int | None = None) -> dict[str, Any]:
    """Return MVP + OPOY leaderboards with normalized odds.

    DB-cached: a completed season's awards are immutable (never expires),
    the current season is refreshed daily by the scheduler.
    """
    season = season or latest_completed_season()
    is_completed = season < latest_completed_season() + 1
    ttl = None if is_completed else 60 * 60 * 24

    async def _compute() -> dict[str, Any]:
        mvp_rows = await _award_leaderboard(season, ["QB"], MVP_WEIGHTS, top=10)
        opoy_rows = await _award_leaderboard(season, ["RB", "WR", "TE"], OPOY_WEIGHTS, top=10)
        for rows in (mvp_rows, opoy_rows):
            odds = _softmax_odds([r["composite_score"] for r in rows])
            for r, o in zip(rows, odds):
                r["odds_pct"] = o
        return {"season": season, "mvp": mvp_rows, "opoy": opoy_rows}

    return await artifact_cache.get_or_compute(
        kind="awards",
        key=f"season:{season}",
        compute=_compute,
        ttl_seconds=ttl,
        l1_ttl_seconds=60 * 30,
    )
