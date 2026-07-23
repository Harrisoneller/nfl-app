"""Analytics engine.

Computes team + player season profiles (basic stats + advanced metrics from
play-by-play + percentile ranks vs peers), plus year-over-year trends and
weekly game logs.

Performance design:
- The expensive operations are (1) downloading parquet from nflverse, and
  (2) recomputing percentile distributions across every player on every
  request. Both are cached: parquet is on disk (nfl-data-py's own cache),
  enriched DataFrames + per-(season, position) percentile tables live in
  our in-process TTL cache.
- `pd.DataFrame.apply(axis=1)` is the wrong tool for derived columns at
  scale. We use vectorized operations instead, which is roughly 10x faster
  on a ~3k-row frame.
- Multi-season trend queries fan out with `asyncio.gather`, so 5 seasons
  fetch in ~the time of 1 (each season is independent I/O).
"""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import artifact_cache

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 60 * 6  # 6h
CACHE_TTL_LONG = 60 * 60 * 24  # 24h for stable historical seasons


# ============================================================================
# Helpers
# ============================================================================


def percentile_rank(
    value: float | None, distribution: np.ndarray | list, higher_is_better: bool = True,
) -> float | None:
    """Public alias for percentile rank (0–100)."""
    return _pct_rank(value, distribution, higher_is_better)


def _pct_rank(value: float | None, distribution: np.ndarray | list, higher_is_better: bool = True) -> float | None:
    """Percentile rank (0..100). None if value or distribution missing."""
    if value is None:
        return None
    arr = np.asarray(distribution, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return None
    if higher_is_better:
        rank = (arr < value).sum() + 0.5 * (arr == value).sum()
    else:
        rank = (arr > value).sum() + 0.5 * (arr == value).sum()
    return float(round(100 * rank / arr.size, 1))


def _safe_div_series(a: pd.Series, b: pd.Series) -> pd.Series:
    """Vectorized safe division. Returns NaN where b==0 or either is NaN."""
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    out = a / b
    return out.where(b != 0, np.nan)


# ============================================================================
# Team analytics
# ============================================================================

TEAM_METRICS: list[tuple[str, str, bool]] = [
    ("off_epa_per_play", "pbp_off", True),
    ("off_success_rate", "pbp_off", True),
    ("off_pass_epa_per_play", "pbp_off", True),
    ("off_rush_epa_per_play", "pbp_off", True),
    ("off_explosive_play_rate", "pbp_off", True),
    ("off_red_zone_td_pct", "pbp_off", True),
    ("off_third_down_pct", "pbp_off", True),
    ("off_plays_per_game", "pbp_off", True),
    ("off_yards_per_play", "pbp_off", True),
    ("points_per_game", "pbp_off", True),
    ("adj_off_epa_per_play", "pbp_off", True),
    ("adj_off_success_rate", "pbp_off", True),
    ("off_cpoe", "pbp_off", True),
    ("off_proe", "pbp_off", True),
    ("off_neutral_sec_per_play", "pbp_off", False),  # lower = faster
    ("def_epa_per_play", "pbp_def", False),
    ("adj_def_epa_per_play", "pbp_def", False),
    ("adj_def_success_rate", "pbp_def", False),
    ("def_success_rate", "pbp_def", False),
    ("def_explosive_play_rate", "pbp_def", False),
    ("def_red_zone_td_pct", "pbp_def", False),
    ("def_third_down_pct", "pbp_def", False),
    ("def_yards_per_play", "pbp_def", False),
    ("points_allowed_per_game", "pbp_def", False),
    ("sacks_per_game", "pbp_def", True),
    ("turnover_margin_per_game", "pbp_team", True),
    ("pass_rate_neutral", "pbp_team", True),
]


async def _team_pbp_aggregates(
    season: int, *, allow_live_fallback: bool = True,
) -> dict[str, dict[str, float]]:
    """Team PBP aggregates: L1 cache → Postgres (preferred) → optional live compute."""
    key = f"team_pbp_agg:{season}"
    if (v := cache.get(key)) is not None:
        return v

    async with _lock_for(key):
        if (v := cache.get(key)) is not None:
            return v
        from ..db import SessionLocal
        from . import materialize_service

        db = SessionLocal()
        try:
            loaded = materialize_service.load_team_aggregates(db, season)
        finally:
            db.close()
        if loaded:
            cache.set(key, loaded, CACHE_TTL)
            return loaded
        if not allow_live_fallback:
            # Hot read paths should avoid on-request parquet/PBP computation.
            return {}
        return await _compute_team_pbp_aggregates_cached(season, key)


async def compute_team_pbp_aggregates(season: int) -> dict[str, dict[str, float]]:
    """Compute aggregates from PBP (worker ingest path — loads parquet once)."""
    pbp = await _nfl.pbp_df(season)
    if pbp is None or len(pbp) == 0:
        return {}

    # Normalize team columns
    for col in ("posteam", "defteam", "home_team", "away_team"):
        if col in pbp.columns:
            pbp[col] = pbp[col].map(lambda x: canonical_team(x) if isinstance(x, str) else x)

    pbp = pbp[pbp["play_type"].isin(["pass", "run"])]
    teams = sorted(set(pbp["posteam"].dropna().unique()) | set(pbp["defteam"].dropna().unique()))
    out: dict[str, dict[str, float]] = {tm: {} for tm in teams}

    # Offense
    for tm, plays in pbp.groupby("posteam"):
        if not tm:
            continue
        out[tm]["off_epa_per_play"] = float(plays["epa"].mean())
        out[tm]["off_success_rate"] = float(plays["success"].mean())
        passes = plays[plays["play_type"] == "pass"]
        runs = plays[plays["play_type"] == "run"]
        out[tm]["off_pass_epa_per_play"] = float(passes["epa"].mean()) if len(passes) else None
        out[tm]["off_rush_epa_per_play"] = float(runs["epa"].mean()) if len(runs) else None
        out[tm]["off_explosive_play_rate"] = float((plays["yards_gained"] >= 20).mean())
        rz = plays[plays["yardline_100"] <= 20]
        rz_drives = rz.groupby(["game_id", "drive"]).agg({"touchdown": "max"})
        out[tm]["off_red_zone_td_pct"] = float(rz_drives["touchdown"].mean()) if len(rz_drives) else None
        third = plays[plays["down"] == 3]
        out[tm]["off_third_down_pct"] = (
            float(third["first_down"].mean()) if "first_down" in third.columns and len(third) else None
        )
        games_played = plays["game_id"].nunique()
        out[tm]["off_plays_per_game"] = float(len(plays) / max(games_played, 1))
        out[tm]["off_yards_per_play"] = float(plays["yards_gained"].mean())
        pts = plays.groupby("game_id")["posteam_score"].max().sum()
        out[tm]["points_per_game"] = float(pts / max(games_played, 1)) if games_played else None

        if "wp" in plays.columns:
            neutral = plays[(plays["wp"].between(0.2, 0.8)) & (plays["down"] == 1) & (plays["qtr"] <= 3)]
            if len(neutral):
                out[tm]["pass_rate_neutral"] = float((neutral["play_type"] == "pass").mean())

    # Defense
    for tm, plays in pbp.groupby("defteam"):
        if not tm:
            continue
        out[tm]["def_epa_per_play"] = float(plays["epa"].mean())
        out[tm]["def_success_rate"] = float(plays["success"].mean())
        out[tm]["def_explosive_play_rate"] = float((plays["yards_gained"] >= 20).mean())
        rz = plays[plays["yardline_100"] <= 20]
        rz_drives = rz.groupby(["game_id", "drive"]).agg({"touchdown": "max"})
        out[tm]["def_red_zone_td_pct"] = float(rz_drives["touchdown"].mean()) if len(rz_drives) else None
        third = plays[plays["down"] == 3]
        out[tm]["def_third_down_pct"] = (
            float(third["first_down"].mean()) if "first_down" in third.columns and len(third) else None
        )
        out[tm]["def_yards_per_play"] = float(plays["yards_gained"].mean())
        games_played = plays["game_id"].nunique()
        pts_allowed = plays.groupby("game_id")["posteam_score"].max().sum()
        out[tm]["points_allowed_per_game"] = float(pts_allowed / max(games_played, 1)) if games_played else None
        if "sack" in plays.columns:
            out[tm]["sacks_per_game"] = float(plays["sack"].sum() / max(games_played, 1))

    # Turnover margin
    if "interception" in pbp.columns and "fumble_lost" in pbp.columns:
        for tm, plays in pbp.groupby("posteam"):
            if not tm: continue
            tos = int(plays["interception"].sum()) + int(plays["fumble_lost"].sum())
            gp = plays["game_id"].nunique() or 1
            out[tm].setdefault("turnovers_committed_per_game", float(tos / gp))
        for tm, plays in pbp.groupby("defteam"):
            if not tm: continue
            tos = int(plays["interception"].sum()) + int(plays["fumble_lost"].sum())
            gp = plays["game_id"].nunique() or 1
            out[tm].setdefault("turnovers_forced_per_game", float(tos / gp))
        for tm, row in out.items():
            f, c = row.get("turnovers_forced_per_game"), row.get("turnovers_committed_per_game")
            if f is not None and c is not None:
                row["turnover_margin_per_game"] = round(f - c, 3)

    # Opponent-adjusted EPA core (ridge pass) + CPOE / PROE / neutral pace.
    # Prior season's materialized adjusted values stabilize weeks 1–4; the
    # materialized-only load can't recurse into a live PBP compute.
    try:
        from . import epa_adjust_service

        prior = await _team_pbp_aggregates(season - 1, allow_live_fallback=False)
        adjusted = epa_adjust_service.compute_adjusted_metrics(pbp, prior=prior or None)
        for tm, metrics in adjusted.items():
            out.setdefault(tm, {}).update(metrics)
    except Exception as e:  # noqa: BLE001 — adjusted layer must never sink base aggregates
        log.warning("epa_adjust_failed", season=season, error=str(e)[:200])

    return out


async def _compute_team_pbp_aggregates_cached(season: int, key: str) -> dict[str, dict[str, float]]:
    out = await compute_team_pbp_aggregates(season)
    if out:
        cache.set(key, out, CACHE_TTL)
    return out


async def team_profile(team_id: str, season: int) -> dict[str, Any]:
    """Team season profile (metrics + percentile ranks), persisted in L2.

    A completed season's profile never changes, so it's cached permanently and
    survives restarts/redeploys; the active/upcoming season refreshes every few
    hours. This removes the recompute-after-restart latency on team pages.
    """
    ttl = None if season < current_or_upcoming_season() else CACHE_TTL
    return await artifact_cache.get_or_compute(
        kind="team_profile",
        key=f"{team_id}:{season}",
        compute=lambda: _compute_team_profile(team_id, season),
        ttl_seconds=ttl,
        l1_ttl_seconds=CACHE_TTL,
    )


async def _compute_team_profile(team_id: str, season: int) -> dict[str, Any]:
    aggs = await _team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        return {"team_id": team_id, "season": season, "error": "no data"}
    me = aggs.get(team_id)
    if me is None:
        return {"team_id": team_id, "season": season, "error": f"no PBP for {team_id}"}

    metrics_out: dict[str, dict[str, Any]] = {}
    for name, _, higher_better in TEAM_METRICS:
        if name not in me:
            continue
        distribution = [t.get(name) for t in aggs.values() if t.get(name) is not None]
        value = me.get(name)
        metrics_out[name] = {
            "value": round(value, 4) if isinstance(value, (int, float)) and value is not None else value,
            "percentile": _pct_rank(value, distribution, higher_better),
            "higher_is_better": higher_better,
        }

    return {
        "team_id": team_id,
        "season": season,
        "metrics": metrics_out,
        "record": await _team_record(team_id, season),
    }


async def _team_record(team_id: str, season: int) -> dict[str, Any]:
    sched = await _nfl.schedules_df(season)
    if sched is None or len(sched) == 0:
        return {"wins": 0, "losses": 0, "ties": 0}
    sched = sched.copy()
    sched["home_team"] = sched["home_team"].map(canonical_team)
    sched["away_team"] = sched["away_team"].map(canonical_team)
    sched = sched[(sched["home_team"] == team_id) | (sched["away_team"] == team_id)]
    sched = sched.dropna(subset=["home_score", "away_score"])
    if len(sched) == 0:
        return {"wins": 0, "losses": 0, "ties": 0}
    is_home = sched["home_team"] == team_id
    my_score = np.where(is_home, sched["home_score"], sched["away_score"]).astype(float)
    opp_score = np.where(is_home, sched["away_score"], sched["home_score"]).astype(float)
    return {
        "wins": int((my_score > opp_score).sum()),
        "losses": int((my_score < opp_score).sum()),
        "ties": int((my_score == opp_score).sum()),
    }


async def team_trend(team_id: str, seasons: list[int], metric: str) -> dict[str, Any]:
    """Parallel fan-out across seasons — 5x faster than serial."""
    aggs_all = await asyncio.gather(
        *[_team_pbp_aggregates(s, allow_live_fallback=False) for s in seasons]
    )
    points = []
    for s, aggs in zip(seasons, aggs_all):
        v = (aggs.get(team_id) or {}).get(metric)
        points.append({
            "season": s,
            "value": round(v, 4) if isinstance(v, (int, float)) and v is not None else None,
        })
    return {"team_id": team_id, "metric": metric, "points": points}


# ============================================================================
# Player analytics
# ============================================================================

POSITION_METRICS: dict[str, list[tuple[str, bool]]] = {
    "QB": [
        ("games", True), ("attempts", True), ("completions", True), ("completion_pct", True),
        ("passing_yards", True), ("yards_per_attempt", True), ("passing_tds", True),
        ("interceptions", False), ("sack_rate", False), ("passer_rating", True),
        ("epa_per_play", True), ("success_rate", True), ("adot", True), ("cpoe", True),
        ("rushing_yards", True), ("rushing_tds", True), ("fantasy_points_ppr", True),
    ],
    "RB": [
        ("games", True), ("carries", True), ("rushing_yards", True), ("yards_per_carry", True),
        ("rushing_tds", True), ("targets", True), ("receptions", True), ("receiving_yards", True),
        ("yards_per_target", True), ("receiving_tds", True), ("epa_per_touch", True),
        ("success_rate", True), ("snap_share", True), ("target_share", True),
        ("red_zone_carries", True), ("fantasy_points_ppr", True),
    ],
    "WR": [
        ("games", True), ("targets", True), ("receptions", True), ("catch_rate", True),
        ("receiving_yards", True), ("yards_per_reception", True), ("yards_per_target", True),
        ("receiving_tds", True), ("target_share", True), ("air_yards_share", True),
        ("adot", True), ("yac", True), ("racr", True), ("wopr", True),
        ("snap_share", True), ("fantasy_points_ppr", True),
    ],
    "TE": [
        ("games", True), ("targets", True), ("receptions", True), ("catch_rate", True),
        ("receiving_yards", True), ("yards_per_reception", True), ("yards_per_target", True),
        ("receiving_tds", True), ("target_share", True), ("air_yards_share", True),
        ("adot", True), ("yac", True), ("racr", True), ("wopr", True),
        ("snap_share", True), ("fantasy_points_ppr", True),
    ],
}

POSITION_MIN_SAMPLES: dict[str, dict[str, int]] = {
    "QB": {"attempts": 100},
    "RB": {"carries": 50, "targets": 0},
    "WR": {"targets": 30},
    "TE": {"targets": 20},
}


# In-flight locks so concurrent requests don't all compute the same thing
_inflight: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    if key not in _inflight:
        _inflight[key] = asyncio.Lock()
    return _inflight[key]


async def build_seasonal_player_dataframe(season: int) -> pd.DataFrame | None:
    """Build enriched seasonal table from nflverse (worker ingest / cache miss)."""
    df = await _nfl.seasonal_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()

    if "position" not in df.columns:
        rosters = await _nfl.rosters_df(season)
        if rosters is not None and len(rosters):
            ros = rosters[["player_id", "position", "team"]].drop_duplicates(subset=["player_id"])
            df = df.merge(ros, on="player_id", how="left")

    if "team" not in df.columns and "recent_team" in df.columns:
        df["team"] = df["recent_team"]
    df["team"] = df["team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)

    df["completion_pct"] = _safe_div_series(df.get("completions", 0), df.get("attempts", 0))
    df["yards_per_attempt"] = _safe_div_series(df.get("passing_yards", 0), df.get("attempts", 0))
    df["yards_per_carry"] = _safe_div_series(df.get("rushing_yards", 0), df.get("carries", 0))
    df["yards_per_reception"] = _safe_div_series(df.get("receiving_yards", 0), df.get("receptions", 0))

    targets = pd.to_numeric(df.get("targets", 0), errors="coerce")
    ry = pd.to_numeric(df.get("receiving_yards", 0), errors="coerce").fillna(0)
    df["yards_per_target"] = (ry / targets).where(targets != 0, np.nan)
    df["catch_rate"] = _safe_div_series(df.get("receptions", 0), df.get("targets", 0))

    carries = pd.to_numeric(df.get("carries", 0), errors="coerce").fillna(0)
    recs = pd.to_numeric(df.get("receptions", 0), errors="coerce").fillna(0)
    df["touches"] = carries + recs
    epa = pd.to_numeric(df.get("epa", np.nan), errors="coerce")
    df["epa_per_touch"] = (epa / df["touches"]).where(df["touches"] != 0, np.nan)

    opps = (
        pd.to_numeric(df.get("attempts", 0), errors="coerce").fillna(0)
        + pd.to_numeric(df.get("targets", 0), errors="coerce").fillna(0)
        + carries
    )
    df["epa_per_play"] = (epa / opps).where(opps != 0, np.nan)

    sacks = pd.to_numeric(df.get("sacks", 0), errors="coerce").fillna(0)
    atts = pd.to_numeric(df.get("attempts", 0), errors="coerce").fillna(0)
    df["sack_rate"] = (sacks / (atts + sacks)).where((atts + sacks) > 0, np.nan)

    if "fantasy_points_ppr" not in df.columns and "fantasy_points" in df.columns:
        df["fantasy_points_ppr"] = df["fantasy_points"]

    if "player_id" in df.columns:
        df = df.set_index("player_id", drop=False)
    return df


async def _seasonal_player_table(
    season: int, *, allow_live_fallback: bool = True,
) -> pd.DataFrame | None:
    """Enriched seasonal table: L1 cache → Postgres → optional live nflverse."""
    key = f"player_seasonal_enriched:{season}"
    if (v := cache.get(key)) is not None:
        return v

    async with _lock_for(key):
        if (v := cache.get(key)) is not None:
            return v

        from ..db import SessionLocal
        from . import materialize_service

        db = SessionLocal()
        try:
            df = materialize_service.load_player_dataframe(db, season)
        finally:
            db.close()
        if df is None or len(df) == 0:
            if not allow_live_fallback:
                return None
            df = await build_seasonal_player_dataframe(season)
        if df is None or len(df) == 0:
            return None
        cache.set(key, df, CACHE_TTL_LONG)
        return df


async def _position_percentile_table(
    season: int, position: str, *, allow_live_fallback: bool = True,
) -> dict[str, np.ndarray]:
    """Pre-compute the value array (sorted) per metric for one (season, position).

    Cached, so percentile lookups across many players in the same position
    avoid redoing the column scan. Returns { metric_name: np.ndarray }.
    """
    key = f"pct_table:{season}:{position}"
    if (v := cache.get(key)) is not None:
        return v

    df = await _seasonal_player_table(season, allow_live_fallback=allow_live_fallback)
    if df is None or len(df) == 0:
        return {}

    sub = df[df["position"] == position]
    mins = POSITION_MIN_SAMPLES.get(position, {})
    for col, threshold in mins.items():
        if col in sub.columns:
            sub = sub[sub[col].fillna(0) >= threshold]

    out: dict[str, np.ndarray] = {"_peer_count": np.array([len(sub)])}
    for name, _ in POSITION_METRICS.get(position, []):
        if name in sub.columns:
            arr = pd.to_numeric(sub[name], errors="coerce").to_numpy()
            arr = arr[~np.isnan(arr)]
            out[name] = arr
    cache.set(key, out, CACHE_TTL_LONG)
    return out


async def player_profile(
    player_id: str, full_name: str, position: str, season: int
) -> dict[str, Any]:
    """PlayerProfiler-style profile. Uses pre-computed peer distributions for speed.

    Persisted in L2 (Postgres) keyed by (player_id, season): a completed
    season's profile is immutable and cached permanently (survives restarts);
    the active/upcoming season refreshes every few hours. get_or_compute also
    provides the in-process layer + singleflight, so concurrent first-loads of
    the same player collapse to one computation.
    """
    ttl = None if season < current_or_upcoming_season() else CACHE_TTL
    return await artifact_cache.get_or_compute(
        kind="player_profile",
        key=f"{player_id}:{season}",
        compute=lambda: _compute_player_profile(player_id, full_name, position, season),
        ttl_seconds=ttl,
        l1_ttl_seconds=CACHE_TTL,
    )


async def _compute_player_profile(
    player_id: str, full_name: str, position: str, season: int
) -> dict[str, Any]:
    """Heavy path (cache miss): scan the seasonal table + rank vs peers.

    Season fallback: if the requested season has no data (e.g. current season
    in offseason before nflverse publishes), transparently fall back to the
    most recent completed season and mark `fallback_from`.
    """
    df = await _seasonal_player_table(season, allow_live_fallback=False)
    fallback_from: int | None = None
    if df is None or len(df) == 0:
        # Walk back up to 3 seasons looking for data.
        for fallback in range(season - 1, season - 4, -1):
            df = await _seasonal_player_table(fallback, allow_live_fallback=False)
            if df is not None and len(df) > 0:
                fallback_from = season
                season = fallback
                break
    if df is None or len(df) == 0:
        return {"player_id": player_id, "season": season, "error": "no data"}

    pos = (position or "").upper()
    # Indexed lookup
    me_row: pd.Series | None = None
    if player_id in df.index:
        me_row = df.loc[player_id]
        if isinstance(me_row, pd.DataFrame):
            me_row = me_row.iloc[0]
    if me_row is None and full_name:
        for col in ("player_display_name", "player_name"):
            if col in df.columns:
                hits = df[df[col] == full_name]
                if len(hits):
                    me_row = hits.iloc[0]
                    break
    if me_row is None:
        return {"player_id": player_id, "season": season, "error": "player not found in season data"}

    pct_table = await _position_percentile_table(
        season, pos, allow_live_fallback=False
    )
    peer_count = int(pct_table.get("_peer_count", np.array([0]))[0])
    metric_defs = POSITION_METRICS.get(pos, POSITION_METRICS["WR"])
    metrics_out: dict[str, dict[str, Any]] = {}
    for name, higher_better in metric_defs:
        if name not in df.columns:
            continue
        raw = me_row.get(name)
        try:
            value = float(raw) if raw is not None and not (isinstance(raw, float) and np.isnan(raw)) else None
        except (TypeError, ValueError):
            value = None
        dist = pct_table.get(name, np.array([]))
        metrics_out[name] = {
            "value": round(value, 4) if value is not None else None,
            "percentile": _pct_rank(value, dist, higher_better),
            "higher_is_better": higher_better,
        }

    result = {
        "player_id": player_id,
        "season": season,
        "position": pos,
        "team": me_row.get("team"),
        "peer_count": peer_count,
        "metrics": metrics_out,
    }
    if fallback_from is not None:
        result["fallback_from"] = fallback_from
    return result


async def player_gamelog(player_id: str, full_name: str, season: int) -> list[dict[str, Any]]:
    """Week-by-week stats. Walks back up to 3 prior seasons if the requested
    season has no data or the player has no rows that season (rookies,
    returnees, offseason before nflverse publishes current year)."""
    sub: pd.DataFrame | None = None
    for offset in range(4):
        s = season - offset
        if s < 2000:
            break
        df = await _nfl.weekly_df(s)
        if df is None or len(df) == 0:
            continue
        candidate = df[df["player_id"] == player_id] if "player_id" in df.columns else df.iloc[0:0]
        if len(candidate) == 0 and full_name:
            for col in ("player_display_name", "player_name"):
                if col in df.columns:
                    candidate = df[df[col] == full_name]
                    if len(candidate):
                        break
        if len(candidate) > 0:
            sub = candidate
            break
    if sub is None or len(sub) == 0:
        return []
    keep = [
        "week", "opponent_team", "completions", "attempts", "passing_yards",
        "passing_tds", "interceptions", "carries", "rushing_yards", "rushing_tds",
        "receptions", "targets", "receiving_yards", "receiving_tds",
        "fantasy_points", "fantasy_points_ppr",
    ]
    keep = [c for c in keep if c in sub.columns]
    out = sub[keep].sort_values("week").to_dict(orient="records")
    for row in out:
        for k, v in list(row.items()):
            if isinstance(v, float) and np.isnan(v):
                row[k] = None
    return out


async def player_trend(
    player_id: str, full_name: str, position: str, seasons: list[int], metric: str
) -> dict[str, Any]:
    """Parallel fan-out across seasons — 5x faster than serial."""
    profiles = await asyncio.gather(
        *[player_profile(player_id, full_name, position, s) for s in seasons]
    )
    points = []
    for s, prof in zip(seasons, profiles):
        m = (prof.get("metrics") or {}).get(metric, {})
        points.append({"season": s, "value": m.get("value"), "percentile": m.get("percentile")})
    return {"player_id": player_id, "metric": metric, "points": points}


# ============================================================================
# Warmup
# ============================================================================


async def warmup(seasons: list[int]) -> None:
    """Warm L1 percentile tables from materialized DB (no PBP if Postgres is warm)."""
    log.info("analytics_warmup_starting", seasons=seasons)
    for s in seasons:
        try:
            await _team_pbp_aggregates(s)
            await _seasonal_player_table(s)
            await asyncio.gather(
                *[_position_percentile_table(s, pos) for pos in ("QB", "RB", "WR", "TE")],
                return_exceptions=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("analytics_warmup_partial_failure", season=s, error=str(e))
    log.info("analytics_warmup_complete", seasons=seasons)


async def build_derived_profiles(
    seasons: list[int] | None = None,
    *,
    per_position: int = 75,
) -> dict[str, int]:
    """Pre-compute team + player profiles into model_artifacts (derive pipeline)."""
    from ..models.seed import NFL_TEAMS

    if seasons is None:
        latest = latest_completed_season()
        seasons = [latest]

    teams_done = 0
    for s in seasons:
        for t in NFL_TEAMS:
            await team_profile(t["id"], s)
            teams_done += 1
        await _precompute_popular_players(s, per_position=per_position)

    log.info("derived_profiles_built", seasons=seasons, teams=teams_done)
    return {"teams": teams_done, "seasons": len(seasons)}


async def _precompute_popular_players(season: int, per_position: int = 100) -> None:
    """Cache profile results for the most relevant players in a season.

    Uses fantasy_points_ppr as the relevance ranking. Iterates the position
    DataFrame in sorted order; each call to player_profile populates that
    player's `player_profile:{id}:{season}` cache entry.
    """
    df = await _seasonal_player_table(season)
    if df is None or len(df) == 0:
        return
    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    relevance = "fantasy_points_ppr" if "fantasy_points_ppr" in df.columns else None
    total = 0
    for pos in ("QB", "RB", "WR", "TE"):
        sub = df[df["position"] == pos]
        if relevance and relevance in sub.columns:
            sub = sub.sort_values(relevance, ascending=False)
        sub = sub.head(per_position)
        for _, row in sub.iterrows():
            pid = row.get("player_id")
            if not pid:
                continue
            full_name = row.get(name_col) or ""
            try:
                await player_profile(pid, full_name, pos, season)
                total += 1
            except Exception:  # noqa: BLE001
                continue
    log.info("popular_players_precomputed", season=season, total=total)
