"""Compare teams or players side-by-side.

Modes:
- compare_teams(team_ids, season) → table of N teams' aggregate stats
- compare_team_to_league(team_id, season) → team rank + league mean/median
- compare_players(player_names, season) → table of N players' season stats
"""
from __future__ import annotations

import statistics
from typing import Any

from . import stats_service


async def compare_teams(team_ids: list[str], season: int) -> dict[str, Any]:
    rows = []
    for tid in team_ids:
        agg = await stats_service.team_aggregate(season, tid)
        rows.append(agg)
    metrics = [k for k in (rows[0] if rows else {}).keys() if k not in ("team_id", "season")]
    winners: dict[str, str] = {}
    for m in metrics:
        try:
            best = max(rows, key=lambda r: float(r.get(m) or 0))
            winners[m] = best["team_id"]
        except Exception:
            continue
    return {"season": season, "metrics": metrics, "rows": rows, "winners": winners}


async def compare_team_to_league(team_id: str, season: int) -> dict[str, Any]:
    from ..models.seed import NFL_TEAMS
    all_aggs = []
    for t in NFL_TEAMS:
        all_aggs.append(await stats_service.team_aggregate(season, t["id"]))

    metrics = [k for k in all_aggs[0].keys() if k not in ("team_id", "season")]
    me = next((a for a in all_aggs if a["team_id"] == team_id), None)
    if me is None:
        return {"error": f"team {team_id} not found"}

    summary: dict[str, dict[str, Any]] = {}
    for m in metrics:
        vals = [float(a.get(m) or 0) for a in all_aggs]
        my_v = float(me.get(m) or 0)
        sorted_desc = sorted(vals, reverse=True)
        rank = sorted_desc.index(my_v) + 1 if my_v in sorted_desc else None
        summary[m] = {
            "team_value": my_v,
            "league_mean": round(statistics.fmean(vals), 2) if vals else None,
            "league_median": round(statistics.median(vals), 2) if vals else None,
            "league_max": max(vals) if vals else None,
            "league_min": min(vals) if vals else None,
            "rank_of_32": rank,
        }
    return {"season": season, "team_id": team_id, "summary": summary}


async def compare_players(names: list[str], season: int) -> dict[str, Any]:
    rows = []
    for name in names:
        row = await stats_service.player_aggregate(season, name)
        rows.append({"player": name, "stats": row or {}})
    return {"season": season, "rows": rows}
