"""Head-to-head matchup composition.

Single endpoint returns everything the H2H page needs in one shot — avoids
the request waterfall of fetching 6 separate endpoints and helps the page
feel instant once warmed.
"""
from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from statistics import fmean, pstdev
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models.game import Game
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import analytics_service, artifact_cache, betting_service, elo_service, predictions_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

# In-process layer TTL (L2/Postgres handles durability + cross-restart reuse).
L1_TTL = 60 * 30  # 30 min
ACTIVE_TTL = 60 * 60 * 24  # 24h
RECOMPUTE_BUDGET_SECONDS = 2.5
PREWARM_CONCURRENCY = 8

# Strong refs to background compute tasks so they aren't GC'd mid-flight.
# Pattern: `asyncio.shield` keeps a task alive when the caller is cancelled, but
# without a hard reference somewhere, Python may garbage-collect a "fire and
# forget" task before it finishes. The done-callback drains the set.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_background(task: asyncio.Task) -> asyncio.Task:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def head_to_head(
    db: Session, team_a: str, team_b: str, season: int | None = None,
) -> dict[str, Any]:
    """Composite of two teams, served from the persistent cache.

    The expensive assembly (PBP-derived profiles, historical H2H, Elo trends)
    only changes when a new game completes, so we cache the result in the L2
    artifact store keyed by the season's completed-game count. That means:
      - a finished season is computed once and served forever (immutable),
      - the active season auto-refreshes the moment a game goes final (the
        count changes → new key → recompute) without any manual invalidation,
      - results survive restarts/redeploys instead of recomputing on the next
        click (the latency this is meant to remove).
    """
    a_raw, b_raw = team_a.upper(), team_b.upper()
    a = canonical_team(a_raw) or a_raw
    b = canonical_team(b_raw) or b_raw
    if a == b:
        return {"error": "Pick two different teams"}  # never cache the error case
    season = season or current_or_upcoming_season()

    final_count = _final_games_count(db, season)
    # Completed seasons are immutable; the active/upcoming season gets a 24h
    # backstop on top of the version key (covers schedule corrections etc.).
    is_completed = season < current_or_upcoming_season()
    ttl = None if is_completed else ACTIVE_TTL
    key = f"{a}:{b}:{season}:v{final_count}"
    cache_args = dict(
        kind="h2h",
        key=key,
        compute=lambda: _compute_head_to_head(db, a, b, season),
        ttl_seconds=ttl,
        l1_ttl_seconds=L1_TTL,
    )
    fallback = _latest_cached_h2h(a, b, season)
    # Run the compute as an independent task and SHIELD it from caller
    # cancellation. If the router's outer request budget trips while a cold
    # compute is still running (e.g. first-ever pair on a fresh container that
    # has to download nfl_data_py parquets), we don't want the compute torn
    # down — let it finish so the L2 cache is populated for the next click.
    # Without this, a too-tight outer budget = forever-cold endpoint.
    compute_task = _track_background(
        asyncio.create_task(artifact_cache.get_or_compute(**cache_args))
    )
    if fallback is None:
        try:
            return await asyncio.shield(compute_task)
        except asyncio.CancelledError:
            # Outer budget tripped before any cache existed — let the router
            # serve its summary fallback. Background compute keeps running and
            # will populate L2 within a few seconds for the next visitor.
            log.warning("h2h_cold_compute_shielded_for_background", key=key)
            raise
    try:
        return await asyncio.wait_for(asyncio.shield(compute_task), timeout=RECOMPUTE_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        log.warning("h2h_recompute_timed_out_serving_stale", key=key, stale_key=fallback["key"])
        return _with_stale_cache_meta(
            fallback["payload"],
            requested_key=key,
            stale_key=fallback["key"],
            reason="timeout",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("h2h_recompute_failed_serving_stale", key=key, error=str(e)[:160])
        return _with_stale_cache_meta(
            fallback["payload"],
            requested_key=key,
            stale_key=fallback["key"],
            reason="compute_error",
        )


def _final_games_count(db: Session, season: int) -> int:
    """Number of completed games in a season — the cache version signal."""
    try:
        return (
            db.query(Game)
            .filter(Game.season == season, Game.status == "final")
            .count()
        )
    except Exception:  # noqa: BLE001 — versioning must never break the request
        return 0


def _latest_cached_h2h(a: str, b: str, season: int) -> dict[str, Any] | None:
    """Most recent H2H payload for this ordered pair/season (fresh or stale)."""
    prefix = f"{a}:{b}:{season}:v"
    try:
        local = SessionLocal()
        try:
            return artifact_cache.latest_by_prefix(
                local,
                kind="h2h",
                key_prefix=prefix,
                include_expired=True,
            )
        finally:
            local.close()
    except Exception:  # noqa: BLE001
        return None


def _with_stale_cache_meta(
    payload: Any,
    *,
    requested_key: str,
    stale_key: str,
    reason: str,
) -> Any:
    """Attach cache metadata to stale-served responses without mutating source."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    meta = dict(out.get("_cache") or {})
    meta.update(
        {
            "served_stale": True,
            "reason": reason,
            "requested_key": requested_key,
            "stale_key": stale_key,
        }
    )
    out["_cache"] = meta
    return out


async def _compute_head_to_head(
    db: Session, a: str, b: str, season: int,
) -> dict[str, Any]:
    """Assemble the full H2H payload (the expensive path, run on cache miss)."""
    # ---- Current ratings + records + grades ---------------------------------
    ratings = elo_service.current_ratings(db)
    elo_a = ratings.get(a, elo_service.INITIAL_RATING)
    elo_b = ratings.get(b, elo_service.INITIAL_RATING)
    grade_a = elo_service.rating_to_grade(elo_a)
    grade_b = elo_service.rating_to_grade(elo_b)

    # Latest-completed-season record for context
    last_completed = latest_completed_season()
    record_a = await analytics_service._team_record(a, last_completed)
    record_b = await analytics_service._team_record(b, last_completed)

    # ---- Predicted matchup ---------------------------------------------------
    # If the two teams play in the current season, surface the game prediction.
    upcoming_game = await _scheduled_meeting(season, a, b)
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)

    predicted_game = None
    if upcoming_game:
        h = upcoming_game["home_team"]
        away = upcoming_game["away_team"]
        h_off = (aggs.get(h) or {}).get("points_per_game")
        a_off = (aggs.get(away) or {}).get("points_per_game")
        h_def = (aggs.get(h) or {}).get("points_allowed_per_game")
        a_def = (aggs.get(away) or {}).get("points_allowed_per_game")
        pred = predictions_service.predict_game(
            ratings.get(h, elo_service.INITIAL_RATING),
            ratings.get(away, elo_service.INITIAL_RATING),
            home_off_ppg=h_off, away_off_ppg=a_off,
            home_def_ppg_allowed=h_def, away_def_ppg_allowed=a_def,
            home_aggs=aggs.get(h), away_aggs=aggs.get(away),
        )
        predicted_game = {**upcoming_game, "prediction": pred}
    else:
        # Hypothetical: simulate a neutral-site matchup between them this week.
        h_off = (aggs.get(a) or {}).get("points_per_game")
        b_off = (aggs.get(b) or {}).get("points_per_game")
        h_def = (aggs.get(a) or {}).get("points_allowed_per_game")
        b_def = (aggs.get(b) or {}).get("points_allowed_per_game")
        pred = predictions_service.predict_game(
            elo_a, elo_b,
            home_off_ppg=h_off, away_off_ppg=b_off,
            home_def_ppg_allowed=h_def, away_def_ppg_allowed=b_def,
            neutral_site=True,
            home_aggs=aggs.get(a), away_aggs=aggs.get(b),
        )
        predicted_game = {
            "home_team": a, "away_team": b, "week": None, "gameday": None,
            "neutral_site": True, "hypothetical": True, "prediction": pred,
        }
    market_context = None
    if predicted_game:
        market_context = await betting_service.matchup_market_context(
            db,
            home_team_id=predicted_game["home_team"],
            away_team_id=predicted_game["away_team"],
            prediction=predicted_game.get("prediction"),
        )

    # ---- Profile delta + cross-side matchup breakdown ----------------------
    profile_season = season - 1 if season > last_completed else season
    profile_a = await analytics_service.team_profile(a, season=profile_season)
    profile_b = await analytics_service.team_profile(b, season=profile_season)
    # League distribution for the SAME season the profiles came from — needed to
    # turn raw offense-vs-defense gaps into matchup-adjusted expectations and
    # significance-scored edges (not "any positive delta = edge").
    league_aggs = await analytics_service._team_pbp_aggregates(
        profile_season, allow_live_fallback=False
    )  # noqa: SLF001
    if not league_aggs:
        league_aggs = await analytics_service._team_pbp_aggregates(
            profile_season - 1, allow_live_fallback=False
        )  # noqa: SLF001
    league_stats = _league_metric_stats(league_aggs)
    deltas = _profile_deltas(profile_a, profile_b)
    matchup_breakdown = _cross_side_matchups(profile_a, profile_b, a, b, league_stats)
    decision_metrics = _decision_metrics(
        profile_a=profile_a,
        profile_b=profile_b,
        predicted_game=predicted_game,
        market_context=market_context,
        team_a=a,
        team_b=b,
    )

    # ---- Historical H2H ------------------------------------------------------
    history = await _historical_h2h(a, b, n_seasons=8)

    # ---- Elo trajectories ----------------------------------------------------
    elo_history_a = elo_service.rating_history(db, a)
    elo_history_b = elo_service.rating_history(db, b)

    # ---- Betting splits when these two play each other ----------------------
    # (Not enriched with line edges — just historical results.)

    result = {
        "team_a": a,
        "team_b": b,
        "season": season,
        "elo": {"a": round(elo_a, 1), "b": round(elo_b, 1)},
        "grade": {"a": grade_a, "b": grade_b},
        "record": {"a": record_a, "b": record_b, "season": last_completed},
        "predicted_matchup": predicted_game,
        "market_context": market_context,
        "decision_metrics": decision_metrics,
        "profile": {
            "a": profile_a, "b": profile_b,
            "deltas": deltas,
        },
        "matchup_breakdown": matchup_breakdown,
        "history": history,
        "elo_history": {"a": elo_history_a, "b": elo_history_b},
    }
    return result


# ---- Helpers ---------------------------------------------------------------


async def _scheduled_meeting(season: int, a: str, b: str) -> dict[str, Any] | None:
    """Find the first game in the season where teams a and b face each other."""
    df = await _nfl.schedules_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    sub = df[((df["home_team"] == a) & (df["away_team"] == b))
             | ((df["home_team"] == b) & (df["away_team"] == a))]
    sub = sub.dropna(subset=["home_team", "away_team"]).sort_values("week")
    if len(sub) == 0:
        return None
    g = sub.iloc[0]
    return {
        "game_id": str(g.get("game_id") or ""),
        "season": int(season),
        "week": _safe_int(g.get("week")),
        "gameday": str(g.get("gameday") or ""),
        "home_team": g["home_team"],
        "away_team": g["away_team"],
        "venue": str(g.get("stadium") or ""),
        "broadcast": str(g.get("network") or ""),
        "played": pd.notna(g.get("home_score")) and pd.notna(g.get("away_score")),
        "home_score": _safe_int(g.get("home_score")),
        "away_score": _safe_int(g.get("away_score")),
    }


async def _historical_h2h(a: str, b: str, n_seasons: int) -> dict[str, Any]:
    """Pull all completed games between a and b from the last N seasons."""
    last = latest_completed_season()
    seasons = list(range(last - n_seasons + 1, last + 1))
    games_combined = await betting_service._completed_games_with_lines(seasons)  # noqa: SLF001
    if len(games_combined) == 0:
        return {"a_wins": 0, "b_wins": 0, "ties": 0, "games": []}
    matchups = games_combined[
        ((games_combined["home_team"] == a) & (games_combined["away_team"] == b))
        | ((games_combined["home_team"] == b) & (games_combined["away_team"] == a))
    ].copy()
    if len(matchups) == 0:
        return {"a_wins": 0, "b_wins": 0, "ties": 0, "games": []}
    matchups = matchups.sort_values("gameday", ascending=False)

    out_games = []
    a_wins = b_wins = ties = 0
    for _, g in matchups.iterrows():
        home_score = int(g["home_score"])
        away_score = int(g["away_score"])
        home = g["home_team"]
        away = g["away_team"]
        if home_score > away_score:
            winner = home
        elif home_score < away_score:
            winner = away
        else:
            winner = None
        if winner == a:
            a_wins += 1
        elif winner == b:
            b_wins += 1
        else:
            ties += 1
        out_games.append({
            "season": int(g.get("season") or 0),
            "week": _safe_int(g.get("week")),
            "gameday": str(g.get("gameday") or ""),
            "home_team": home, "away_team": away,
            "home_score": home_score, "away_score": away_score,
            "winner": winner,
            "spread_line": float(g["spread_line"]) if pd.notna(g.get("spread_line")) else None,
            "total_line": float(g["total_line"]) if "total_line" in g and pd.notna(g.get("total_line")) else None,
        })
    return {"a_wins": a_wins, "b_wins": b_wins, "ties": ties, "games": out_games[:12]}


# Offense metric → defensive counterpart (the "yards/points allowed" version).
# Both pair members are "higher is better" from the actor's own perspective:
# offense wants high off_epa, defense wants to give up LOW def_epa — so when
# we compare an offense's value to the opponent's defensive value, the lower
# the defensive number, the harder the matchup for the offense.
_OFF_TO_DEF_PAIRS: list[tuple[str, str, str]] = [
    # (offense_metric, defense_metric, friendly_label)
    ("points_per_game",         "points_allowed_per_game",      "Scoring"),
    ("off_epa_per_play",        "def_epa_per_play",             "EPA / play"),
    ("off_success_rate",        "def_success_rate",             "Success rate"),
    ("off_yards_per_play",      "def_yards_per_play",           "Yards / play"),
    ("off_explosive_play_rate", "def_explosive_play_rate",      "Explosive rate"),
    ("off_red_zone_td_pct",     "def_red_zone_td_pct",          "Red-zone TD%"),
    ("off_third_down_pct",      "def_third_down_pct",           "3rd-down conv%"),
]


# Edge significance bands, in standard deviations of the league's combined
# offense+defense spread. An edge only "counts" once it clears EDGE_MIN_Z — a
# raw gap that's small relative to normal week-to-week league variation is
# noise, not an advantage.
EDGE_MIN_Z = 0.4
EDGE_CLEAR_Z = 1.0
EDGE_STRONG_Z = 1.75


def _league_metric_stats(aggs: dict[str, dict[str, float]]) -> dict[str, tuple[float, float]]:
    """Per-metric (mean, population-stddev) across every team in the season.

    This is the league baseline we measure matchup edges against. Computed from
    the same aggregates the team profiles are built from, so values line up.
    """
    if not aggs:
        return {}
    metrics: set[str] = set()
    for off_key, def_key, _ in _OFF_TO_DEF_PAIRS:
        metrics.add(off_key)
        metrics.add(def_key)
    out: dict[str, tuple[float, float]] = {}
    for m in metrics:
        vals = [
            float(t[m]) for t in aggs.values()
            if isinstance(t.get(m), (int, float)) and t.get(m) is not None
        ]
        if len(vals) >= 2:
            out[m] = (fmean(vals), pstdev(vals))
        elif len(vals) == 1:
            out[m] = (vals[0], 0.0)
    return out


def _grade_edge(z: float) -> tuple[str, bool]:
    """Map a signed z-score to a lean label + whether the OFFENSE has a real edge.

    Positive z favors the offense; negative favors the defense. Returns e.g.
    ("slight_off", True), ("strong_def", False), ("even", False).
    """
    az = abs(z)
    if az < EDGE_MIN_Z:
        return "even", False
    tier = "slight" if az < EDGE_CLEAR_Z else ("clear" if az < EDGE_STRONG_Z else "strong")
    if z > 0:
        return f"{tier}_off", True
    return f"{tier}_def", False


def _matchup_side(
    off_team: str, off_profile: dict, def_team: str, def_profile: dict,
    league_stats: dict[str, tuple[float, float]],
) -> dict:
    """Build one direction of the matchup ("when [off_team] has the ball").

    Each row is a matchup-adjusted projection, not a raw comparison:
      expected = off_val + (def_val - league_avg)
        → the offense's own pace, nudged by how much more/less than a league-
          average defense this specific defense allows.
      edge     = (off_val - league_avg) + (def_val - league_avg)
        → offense's strength-above-average PLUS defense's weakness-above-average.
      edge_z   = edge / sqrt(σ_off² + σ_def²)
        → that edge expressed in standard deviations of normal league spread,
          so we can tell a real mismatch from noise.
    """
    rows: list[dict] = []
    advantage_count = 0
    if not off_profile or "metrics" not in off_profile:
        return {"offense": off_team, "defense": def_team, "rows": [], "advantage_count": 0, "metrics_count": 0}
    if not def_profile or "metrics" not in def_profile:
        return {"offense": off_team, "defense": def_team, "rows": [], "advantage_count": 0, "metrics_count": 0}
    off_m = off_profile["metrics"]
    def_m = def_profile["metrics"]
    for off_key, def_key, label in _OFF_TO_DEF_PAIRS:
        off_card = off_m.get(off_key)
        def_card = def_m.get(def_key)
        if not off_card or not def_card:
            continue
        off_val = off_card.get("value")
        def_val = def_card.get("value")
        if not isinstance(off_val, (int, float)) or not isinstance(def_val, (int, float)):
            continue

        off_stat = league_stats.get(off_key)
        def_stat = league_stats.get(def_key)
        delta = off_val - def_val
        if off_stat is None or def_stat is None:
            # No league context (shouldn't happen in-season) — degrade gracefully
            # to the raw view rather than inventing a baseline.
            expected = (off_val + def_val) / 2
            edge_raw = delta
            edge_z = None
            lean, offense_has_edge = ("off" if delta > 0 else "def" if delta < 0 else "even", delta > 0)
            league_avg: float | None = None
        else:
            mu_off, sd_off = off_stat
            mu_def, sd_def = def_stat
            league_avg = mu_off
            # Matchup-adjusted expected production of THIS offense vs THIS defense.
            expected = off_val + (def_val - mu_def)
            # Combined deviation from average (offense strength + defense leakiness).
            edge_raw = (off_val - mu_off) + (def_val - mu_def)
            sd_comb = math.sqrt(sd_off * sd_off + sd_def * sd_def)
            edge_z = (edge_raw / sd_comb) if sd_comb > 1e-9 else 0.0
            lean, offense_has_edge = _grade_edge(edge_z)

        if offense_has_edge:
            advantage_count += 1
        rows.append({
            "metric": off_key,
            "label": label,
            "off_value": round(off_val, 3),
            "def_value": round(def_val, 3),
            "league_avg": round(league_avg, 3) if league_avg is not None else None,
            "expected": round(expected, 3),
            "delta": round(delta, 3),
            "edge": round(edge_raw, 3),
            "edge_z": round(edge_z, 2) if edge_z is not None else None,
            "lean": lean,
            "offense_has_edge": offense_has_edge,
            # Percentile context for the UI (offense's own pct + opponent's def pct)
            "off_percentile": off_card.get("percentile"),
            # For the def card, the percentile is "rank against league at giving
            # up X". We want to surface "this defense is good/bad at preventing
            # this", so we invert it (higher = worse defense for the offense to
            # face).
            "def_percentile_for_offense": (
                None if def_card.get("percentile") is None
                else round(100 - def_card.get("percentile"), 1)
            ),
        })
    return {
        "offense": off_team,
        "defense": def_team,
        "rows": rows,
        "advantage_count": advantage_count,
        "metrics_count": len(rows),
    }


def _cross_side_matchups(
    profile_a: dict, profile_b: dict, a: str, b: str,
    league_stats: dict[str, tuple[float, float]],
) -> dict:
    """Two-sided matchup breakdown.

    "When A has the ball" pairs A's offensive metrics against B's defensive
    counterparts and vice versa. This is the analyst-style view — strength
    vs. weakness — instead of comparing offense-to-offense. Edges are scored
    against the league distribution (`league_stats`) so only material mismatches
    are flagged.
    """
    a_offense = _matchup_side(a, profile_a, b, profile_b, league_stats)
    b_offense = _matchup_side(b, profile_b, a, profile_a, league_stats)
    return {
        "when_a_has_ball": a_offense,
        "when_b_has_ball": b_offense,
    }


def _profile_deltas(profile_a: dict, profile_b: dict) -> list[dict]:
    """For each metric in common, who has the edge and by how much?"""
    out: list[dict] = []
    if not profile_a or not profile_b or "metrics" not in profile_a or "metrics" not in profile_b:
        return out
    ma = profile_a["metrics"]
    mb = profile_b["metrics"]
    for key in ma:
        if key not in mb:
            continue
        a_val = ma[key].get("value")
        b_val = mb[key].get("value")
        if not isinstance(a_val, (int, float)) or not isinstance(b_val, (int, float)):
            continue
        higher_better = ma[key].get("higher_is_better", True)
        a_pct = ma[key].get("percentile")
        b_pct = mb[key].get("percentile")
        if higher_better:
            winner = "a" if a_val > b_val else ("b" if b_val > a_val else None)
        else:
            winner = "a" if a_val < b_val else ("b" if b_val < a_val else None)
        out.append({
            "metric": key,
            "a_value": a_val, "b_value": b_val,
            "a_percentile": a_pct, "b_percentile": b_pct,
            "higher_is_better": higher_better,
            "winner": winner,
            "delta": round(abs(a_val - b_val), 3),
        })
    return out


def _decision_metrics(
    *,
    profile_a: dict,
    profile_b: dict,
    predicted_game: dict[str, Any] | None,
    market_context: dict[str, Any] | None,
    team_a: str,
    team_b: str,
) -> list[dict[str, Any]]:
    """Top-of-page decision metrics for quick H2H reads."""

    def val(profile: dict, key: str) -> float | None:
        metric = (profile.get("metrics") or {}).get(key) or {}
        raw = metric.get("value")
        return float(raw) if isinstance(raw, (int, float)) else None

    out: list[dict[str, Any]] = []
    off_epa_a = val(profile_a, "off_epa_per_play")
    off_epa_b = val(profile_b, "off_epa_per_play")
    if off_epa_a is not None and off_epa_b is not None:
        diff = off_epa_a - off_epa_b
        out.append({
            "key": "epa_diff",
            "label": "Offensive EPA diff",
            "value": round(diff, 3),
            "favored": team_a if diff >= 0 else team_b,
            "detail": f"{team_a} {off_epa_a:.3f} vs {team_b} {off_epa_b:.3f}",
        })

    sacks_a = val(profile_a, "sacks_per_game")
    sacks_b = val(profile_b, "sacks_per_game")
    if sacks_a is not None and sacks_b is not None:
        diff = sacks_a - sacks_b
        out.append({
            "key": "pressure_proxy_diff",
            "label": "Pressure proxy diff (sacks/g)",
            "value": round(diff, 2),
            "favored": team_a if diff >= 0 else team_b,
            "detail": f"{team_a} {sacks_a:.2f} vs {team_b} {sacks_b:.2f}",
        })

    pred = predicted_game.get("prediction") if predicted_game else None
    if pred:
        spread = pred.get("predicted_spread")
        if isinstance(spread, (int, float)):
            proxy = round(float(spread) * 0.85, 2)
            favored = predicted_game.get("home_team") if proxy <= 0 else predicted_game.get("away_team")
            out.append({
                "key": "injury_adjusted_spread_proxy",
                "label": "Availability-adjusted spread proxy",
                "value": proxy,
                "favored": favored,
                "detail": "Heuristic: 15% shrink toward pick'em to absorb unknown availability shocks.",
            })

        explainability = pred.get("explainability") or {}
        confidence = explainability.get("confidence_context") if isinstance(explainability, dict) else {}
        out.append({
            "key": "confidence",
            "label": "Confidence / calibration",
            "value": confidence.get("calibration_score") if isinstance(confidence, dict) else pred.get("calibration_score"),
            "favored": None,
            "detail": (
                f"{confidence.get('tier', pred.get('confidence_tier', 'n/a'))} confidence"
                if isinstance(confidence, dict)
                else f"{pred.get('confidence_tier', 'n/a')} confidence"
            ),
        })

    if market_context and market_context.get("market_delta"):
        delta = market_context["market_delta"]
        spread_delta = delta.get("spread")
        if isinstance(spread_delta, (int, float)):
            out.append({
                "key": "market_delta_spread",
                "label": "Model vs market spread delta",
                "value": round(float(spread_delta), 2),
                "favored": predicted_game.get("home_team") if spread_delta > 0 else predicted_game.get("away_team"),
                "detail": "Positive means market is more home-favoring than our model.",
            })
    return out[:5]


def _safe_int(v) -> int | None:
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


# ============================================================================
# Pre-warm — make the matchups people actually click instant on first load
# ============================================================================


def _upcoming_week_matchups(db: Session, season: int) -> list[tuple[str, str]]:
    """(home, away) ids for the next week that still has an unplayed game."""
    row = (
        db.query(Game.week)
        .filter(Game.season == season, Game.status != "final", Game.week.isnot(None))
        .order_by(Game.week.asc())
        .first()
    )
    if not row:
        return []
    week = row[0]
    games = db.query(Game).filter(Game.season == season, Game.week == week).all()
    return [(g.home_team_id, g.away_team_id) for g in games if g.home_team_id and g.away_team_id]


def _recent_final_matchups(db: Session, season: int, weeks: int = 2) -> list[tuple[str, str]]:
    """Recently completed games people often click into after scoreboard checks."""
    row = (
        db.query(Game.week)
        .filter(Game.season == season, Game.status == "final", Game.week.isnot(None))
        .order_by(Game.week.desc())
        .first()
    )
    if not row:
        return []
    max_week = row[0]
    min_week = max(1, int(max_week) - weeks + 1)
    games = (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.status == "final",
            Game.week.isnot(None),
            Game.week >= min_week,
        )
        .all()
    )
    return [(g.home_team_id, g.away_team_id) for g in games if g.home_team_id and g.away_team_id]


def _prewarm_pairs(db: Session, season: int) -> list[tuple[str, str]]:
    """Pairs to pre-compute: all intra-division rivalries + this week's games.

    Returns BOTH orderings of each pair so a click in either direction is an
    instant cache hit (the payload is order-sensitive — "when A has the ball").
    """
    from ..models.seed import NFL_TEAMS

    by_div: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in NFL_TEAMS:
        by_div[(t["conference"], t["division"])].append(t["id"])

    unordered: set[frozenset] = set()
    for ids in by_div.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                unordered.add(frozenset((ids[i], ids[j])))
    for home, away in _upcoming_week_matchups(db, season):
        unordered.add(frozenset((home, away)))
    for home, away in _recent_final_matchups(db, season):
        unordered.add(frozenset((home, away)))

    pairs: list[tuple[str, str]] = []
    for fs in unordered:
        xs = sorted(fs)
        if len(xs) == 2:
            pairs.append((xs[0], xs[1]))
            pairs.append((xs[1], xs[0]))
    return pairs


async def prewarm_h2h(db: Session, season: int | None = None) -> int:
    """Pre-compute popular matchups so their first click is instant.

    Cheap: the heavy building blocks (team aggregates, schedules) are shared and
    already cached after the analytics/predictions warmup, so each pair is just
    assembly + a couple of DB reads, landing the result in the L2 store.
    """
    target_season = season or current_or_upcoming_season()
    seasons = sorted({target_season, latest_completed_season()})
    jobs: list[tuple[int, str, str]] = []
    for s in seasons:
        for a, b in _prewarm_pairs(db, s):
            jobs.append((s, a, b))
    sem = asyncio.Semaphore(PREWARM_CONCURRENCY)

    async def _warm_one(s: int, a: str, b: str) -> int:
        async with sem:
            try:
                await head_to_head(db, a, b, s)
                return 1
            except Exception as e:  # noqa: BLE001 — one bad pair shouldn't stop warmup
                log.debug("h2h_prewarm_pair_failed", season=s, a=a, b=b, error=str(e)[:120])
                return 0

    warmed = sum(await asyncio.gather(*[_warm_one(s, a, b) for s, a, b in jobs])) if jobs else 0
    log.info("h2h_prewarmed", seasons=seasons, pairs=warmed)
    return warmed
