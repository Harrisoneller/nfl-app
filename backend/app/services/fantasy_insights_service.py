"""Fantasy command center: rest-of-season values, waiver targets, trades.

All three surfaces are derived from the SAME season projection board the
Players hub shows (``player_predictions_service.projection_leaderboard``), so
fantasy values, waiver scores and trade verdicts are mutually consistent with
every other number in the product — the "one distribution" contract.

- **ROS value** is VORP-style: projected remaining fantasy points above a
  positional replacement level (12-team defaults, league size configurable).
- **Waiver targets** merge Sleeper's trending-adds signal with model ROS value
  and near-term schedule ease — "the crowd is grabbing him AND the model
  agrees" ranks highest.
- **Trade analyzer** sums each side's ROS value with uncertainty carried
  through, so lopsided-but-noisy verdicts say "toss-up", not false precision.
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import latest_completed_season
from . import fantasy_service, news_service
from . import player_predictions_service as proj
from .player_projection_engine import SCORING_FORMATS

log = get_logger(__name__)

CACHE_TTL = 60 * 15

# Replacement-level positional rank for a 12-team, 1QB/2RB/3WR-ish league.
# Scaled linearly for other league sizes.
_REPLACEMENT_RANK_12 = {"QB": 13, "RB": 26, "WR": 38, "TE": 13}

# p10–p90 spans ±1.2816σ around the mean for a normal.
_P90_P10_TO_SD = 2.0 * 1.281552


def _sd_from_band(f: dict[str, Any] | None) -> float:
    if not f or f.get("p90") is None or f.get("p10") is None:
        return 0.0
    return max(0.0, (float(f["p90"]) - float(f["p10"])) / _P90_P10_TO_SD)


async def _board_rows(db: Session, season: int | None, scoring: str) -> dict[str, Any]:
    board = await proj.projection_leaderboard(
        db, season=season, scoring=scoring, sort="fantasy", limit=300,
    )
    return board


async def ros_value_board(
    db: Session,
    season: int | None = None,
    scoring: str = "ppr",
    league_size: int = 12,
    position: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Rest-of-season fantasy values with VORP, positional scarcity tiers."""
    scoring = scoring if scoring in SCORING_FORMATS else "ppr"
    league_size = max(6, min(20, league_size))
    board = await _board_rows(db, season, scoring)
    rows = board.get("players") or []
    fkey = f"fantasy_{scoring}"

    # Replacement level per position: the per-game mean of the player at the
    # (league-size-scaled) replacement rank.
    by_pos: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r)
    replacement: dict[str, float] = {}
    for pos, rs in by_pos.items():
        rs.sort(key=lambda r: float(r[fkey]["per_game"]), reverse=True)
        rank12 = _REPLACEMENT_RANK_12.get(pos, 13)
        rank = max(1, round(rank12 * league_size / 12))
        idx = min(rank - 1, len(rs) - 1)
        replacement[pos] = float(rs[idx][fkey]["per_game"]) if rs else 0.0

    out: list[dict[str, Any]] = []
    for pos, rs in by_pos.items():
        repl = replacement.get(pos, 0.0)
        prev_vorp: float | None = None
        tier = 1
        for i, r in enumerate(rs):
            f = r[fkey]
            per_game = float(f["per_game"])
            games = int(r.get("games_remaining") or 0)
            vorp_pg = per_game - repl
            vorp_ros = vorp_pg * games
            # New tier when the drop from the previous player is big (≥ 8% of
            # the position's top VORP, floored at 4 pts) — cheap gap clustering.
            if prev_vorp is not None and rs:
                top = max(1.0, (float(rs[0][fkey]["per_game"]) - repl) * games)
                if (prev_vorp - vorp_ros) >= max(4.0, 0.08 * top):
                    tier += 1
            prev_vorp = vorp_ros
            out.append({
                "player_id": r.get("player_id"),
                "name": r["name"],
                "position": pos,
                "team": r.get("team"),
                "injury_status": r.get("injury_status"),
                "rookie": r.get("rookie"),
                "role": r.get("role"),
                "pos_rank": i + 1,
                "tier": tier,
                "games_remaining": games,
                "per_game": per_game,
                "ros_points": float(f["mean"]),
                "ros_sd": round(_sd_from_band(f), 1),
                "replacement_per_game": round(repl, 2),
                "vorp_per_game": round(vorp_pg, 2),
                "vorp_ros": round(vorp_ros, 1),
                "next_game": r.get("next_game"),
                # Fantasy-market block from the projection board (ADP,
                # trending). value_vs_adp is recomputed below against the
                # VORP ordering — this board's own rank, not the raw
                # points rank the projection board used.
                "market": r.get("market"),
            })

    out.sort(key=lambda r: r["vorp_ros"], reverse=True)
    for i, r in enumerate(out):
        r["overall_rank"] = i + 1
        m = r.get("market")
        if isinstance(m, dict) and m.get("adp_overall_rank"):
            m["value_vs_adp"] = int(m["adp_overall_rank"]) - (i + 1)
    if position:
        out = [r for r in out if r["position"] == position.upper()]
    return {
        "season": board.get("season"),
        "scoring": scoring,
        "league_size": league_size,
        "replacement_levels": {k: round(v, 2) for k, v in replacement.items()},
        "model_version": board.get("model_version"),
        "note": (
            "VORP = projected per-game points above the positional replacement "
            f"level for a {league_size}-team league, times games remaining."
        ),
        "count": len(out[:limit]),
        "players": out[:limit],
    }


async def waiver_targets(
    db: Session,
    season: int | None = None,
    scoring: str = "ppr",
    limit: int = 25,
) -> dict[str, Any]:
    """Model-checked waiver wire: Sleeper trending adds scored against ROS value
    and next-3-week schedule ease."""
    scoring = scoring if scoring in SCORING_FORMATS else "ppr"
    cache_key = f"waiver_targets:{season or 0}:{scoring}"
    if (v := cache.get(cache_key)) is not None:
        return v

    trending = await news_service.fetch_sleeper_trending(kind="add", limit=60)
    counts = {r["player_id"]: int(r.get("count") or 0) for r in trending if r.get("player_id")}

    ros = await ros_value_board(db, season=season, scoring=scoring, limit=300)
    ros_by_id = {r["player_id"]: r for r in ros["players"] if r.get("player_id")}

    season_resolved = ros.get("season")
    envs_by_team = await proj.league_game_environments(db, season_resolved)
    def_season = (
        season_resolved
        if season_resolved <= latest_completed_season()
        else season_resolved - 1
    )
    def_factors = await proj.positional_defense_factors(def_season)

    def _schedule_ease(row: dict[str, Any]) -> float | None:
        envs = (envs_by_team.get(row.get("team") or "") or [])[:3]
        if not envs:
            return None
        stat = proj._GRADE_STAT_BY_POS.get(row["position"], "receiving_yards")  # noqa: SLF001
        fs = [
            proj._defense_factor(def_factors, e["opponent"], stat, row["position"])  # noqa: SLF001
            for e in envs
        ]
        return round(sum(fs) / len(fs), 3)

    candidates: list[dict[str, Any]] = []
    for pid, count in counts.items():
        row = ros_by_id.get(pid)
        if row is None:
            continue
        candidates.append({**row, "trend_count": count})

    if not candidates:
        result = {"season": season_resolved, "scoring": scoring, "count": 0,
                  "targets": [], "note": "No Sleeper trending data yet."}
        cache.set(cache_key, result, 60 * 5)
        return result

    max_count = max(c["trend_count"] for c in candidates) or 1
    max_vpg = max((c["vorp_per_game"] for c in candidates), default=1.0)
    min_vpg = min((c["vorp_per_game"] for c in candidates), default=0.0)
    vpg_span = max(0.5, max_vpg - min_vpg)

    targets: list[dict[str, Any]] = []
    for c in candidates:
        ease = _schedule_ease(c)
        trend_z = c["trend_count"] / max_count
        value_z = (c["vorp_per_game"] - min_vpg) / vpg_span
        ease_bonus = ((ease or 1.0) - 1.0) * 1.5  # ±~0.35
        score = 0.55 * value_z + 0.35 * trend_z + 0.10 + ease_bonus * 0.10
        reasons: list[str] = []
        if c["vorp_per_game"] > 0:
            reasons.append(f"projects above replacement (+{c['vorp_per_game']}/gm)")
        if trend_z >= 0.5:
            reasons.append("heavily added on Sleeper (24h)")
        if ease and ease >= 1.05:
            reasons.append("soft next-3 schedule")
        if (c.get("role") or {}).get("depth_chart_order") == 1:
            reasons.append("top of depth chart")
        targets.append({
            **c,
            "schedule_ease_next3": ease,
            "waiver_score": round(score, 3),
            "reasons": reasons,
        })

    targets.sort(key=lambda t: t["waiver_score"], reverse=True)
    result = {
        "season": season_resolved,
        "scoring": scoring,
        "model_version": ros.get("model_version"),
        "note": (
            "Score blends model ROS value (55%), Sleeper add-trend (35%) and "
            "next-3-week matchup ease (10%). Schedule ease >1 = softer than average."
        ),
        "count": len(targets[:limit]),
        "targets": targets[:limit],
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


async def analyze_trade(
    db: Session,
    side_a: list[str],
    side_b: list[str],
    season: int | None = None,
    scoring: str = "ppr",
    league_size: int = 12,
) -> dict[str, Any]:
    """Grade a proposed trade by summed ROS VORP with honest uncertainty."""
    scoring = scoring if scoring in SCORING_FORMATS else "ppr"
    ros = await ros_value_board(
        db, season=season, scoring=scoring, league_size=league_size, limit=300,
    )
    by_id = {r["player_id"]: r for r in ros["players"] if r.get("player_id")}
    by_name = {r["name"].strip().lower(): r for r in ros["players"]}

    def _resolve_side(tokens: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
        rows, missing = [], []
        enriched = fantasy_service.enrich_roster(db, [t for t in tokens if t.strip()])
        for e in enriched:
            if not e.get("found"):
                missing.append(e["query"])
                continue
            row = by_id.get(e["player_id"]) or by_name.get((e.get("name") or "").strip().lower())
            if row is None:
                # On a roster but not projection-relevant → replacement-level.
                rows.append({
                    "player_id": e["player_id"], "name": e["name"],
                    "position": e.get("position"), "team": e.get("team"),
                    "vorp_ros": 0.0, "ros_points": None, "ros_sd": 0.0,
                    "note": "not on the projection board — valued at replacement level",
                })
            else:
                rows.append(row)
        return rows, missing

    a_rows, a_missing = _resolve_side(side_a)
    b_rows, b_missing = _resolve_side(side_b)

    def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
        vorp = sum(float(r.get("vorp_ros") or 0.0) for r in rows)
        var = sum(float(r.get("ros_sd") or 0.0) ** 2 for r in rows)
        return {"vorp_ros": round(vorp, 1), "sd": round(math.sqrt(var), 1)}

    a_tot, b_tot = _totals(a_rows), _totals(b_rows)
    diff = a_tot["vorp_ros"] - b_tot["vorp_ros"]
    combined_sd = math.sqrt(a_tot["sd"] ** 2 + b_tot["sd"] ** 2)

    if combined_sd > 0 and abs(diff) < 0.5 * combined_sd:
        verdict = "toss-up"
        detail = "The gap is well inside the projection uncertainty."
    elif diff > 0:
        verdict = "side_a"
        detail = f"Side A projects {abs(diff):.0f} ROS VORP points stronger."
    else:
        verdict = "side_b"
        detail = f"Side B projects {abs(diff):.0f} ROS VORP points stronger."

    return {
        "season": ros.get("season"),
        "scoring": scoring,
        "league_size": league_size,
        "model_version": ros.get("model_version"),
        "side_a": {"players": a_rows, "missing": a_missing, **a_tot},
        "side_b": {"players": b_rows, "missing": b_missing, **b_tot},
        "difference_vorp": round(diff, 1),
        "uncertainty_sd": round(combined_sd, 1),
        "verdict": verdict,
        "detail": detail,
        "note": (
            "Values are rest-of-season VORP from the same projection engine as "
            "the Players hub. Roster context (byes, positional needs) is on you."
        ),
    }
