"""Player game + season projections — v2, distribution-first.

v1 was a rolling 4-week average with a defensive z-score nudge. v2 upgrades the
player layer to the same variance-first standard as the game model
(docs/PREDICTION_MODEL_SPEC.md):

- **Multi-year Bayesian prior** (recency + age weighted; rookie archetypes),
  updated week-by-week in season with conjugate shrinkage — projections adjust
  dynamically as games are played, with no hand-tuned switch point.
- **Game-model coupling**: every per-game projection is conditioned on
  ``predictions_service.predict_game`` outputs for that matchup — the team's
  implied points (scoring environment), expected margin (game script →
  pass/rush tilt) and the opponent's *positional* defense factor.
- **Distributions everywhere**: each stat ships mean + SD (+ anytime-TD
  probability), so P(over any prop line) and honest credible intervals fall
  out of one consistent distribution. Season totals separate correlated talent
  uncertainty from independent game noise, mirroring the hierarchical season
  Monte Carlo.

The pure math lives in ``player_projection_engine``; this module owns data
access, caching, and response shapes. Weather/injury multipliers from v1 are
retained and compose on top of the game-environment multiplier.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.player import Player
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import (
    analytics_service,
    elo_service,
    overrides_service,
    prediction_dist,
    predictions_service,
)
from . import player_projection_engine as engine

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 min
MODEL_VERSION = engine.MODEL_VERSION

# Per-position stat kits to project (fantasy points are derived, not modeled).
POSITION_STATS: dict[str, list[str]] = {
    "QB": [
        "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
        "carries", "rushing_yards", "rushing_tds",
    ],
    "RB": [
        "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds",
    ],
    "WR": ["targets", "receptions", "receiving_yards", "receiving_tds"],
    "TE": ["targets", "receptions", "receiving_yards", "receiving_tds"],
}

# How many prior seasons feed the prior (matches engine.PRIOR_SEASON_WEIGHTS).
PRIOR_LOOKBACK = 3


# ---- Weekly frames + per-season rates --------------------------------------


async def _player_weekly_frame(season: int) -> pd.DataFrame | None:
    """Cached weekly DataFrame for one season (canonical team ids)."""
    key = f"player_weekly_indexed:{season}"
    if (v := cache.get(key)) is not None:
        return v
    df = await _nfl.weekly_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    if "fantasy_points_ppr" not in df.columns and "fantasy_points" in df.columns:
        df["fantasy_points_ppr"] = df["fantasy_points"]
    for col in ("recent_team", "opponent_team"):
        if col in df.columns:
            df[col] = df[col].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    cache.set(key, df, CACHE_TTL)
    return df


_ALL_STATS = sorted({s for kit in POSITION_STATS.values() for s in kit} | {"fantasy_points_ppr"})


def _season_rate_table(df: pd.DataFrame, stats: list[str]) -> pd.DataFrame:
    """Per-player per-game mean/sd/games for one season — one groupby for ALL
    players so bulk consumers (leaderboard, backtest) never scan per player."""
    cols = [s for s in stats if s in df.columns]
    if not cols or "player_id" not in df.columns:
        return pd.DataFrame()
    g = df.groupby("player_id")[cols]
    out = pd.concat({"mean": g.mean(), "sd": g.std()}, axis=1)
    out[("meta", "games")] = df.groupby("player_id")["week"].nunique()
    return out


async def _rate_tables(seasons: list[int]) -> dict[int, pd.DataFrame]:
    """{season: rate table} for every requested season that has weekly data."""
    out: dict[int, pd.DataFrame] = {}
    for s in seasons:
        if s < 2000:
            continue
        key = f"player_rate_table:{s}"
        if (v := cache.get(key)) is not None:
            out[s] = v
            continue
        df = await _player_weekly_frame(s)
        if df is None or len(df) == 0:
            continue
        tbl = _season_rate_table(df, _ALL_STATS)
        if len(tbl):
            cache.set(key, tbl, CACHE_TTL)
            out[s] = tbl
    return out


def _rates_for(tbl: pd.DataFrame | None, gsis_id: str, stat: str) -> dict | None:
    if tbl is None or len(tbl) == 0 or gsis_id not in tbl.index:
        return None
    try:
        mean = tbl.loc[gsis_id, ("mean", stat)]
        sd = tbl.loc[gsis_id, ("sd", stat)]
        games = tbl.loc[gsis_id, ("meta", "games")]
    except KeyError:
        return None
    if pd.isna(mean):
        return None
    return {
        "mean": float(mean),
        "sd": float(sd) if pd.notna(sd) else 0.0,
        "games": int(games),
    }


def _clean_gsis(v) -> str | None:
    """Sleeper pads gsis ids with whitespace (' 00-0034796') and leaves many
    null — normalize before any join against nflverse frames."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_name(name: str | None) -> str:
    """Casefold, strip punctuation and generational suffixes for matching."""
    if not name:
        return ""
    tokens = "".join(
        ch if (ch.isalnum() or ch.isspace()) else " " for ch in name.lower()
    ).split()
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


async def _resolve_gsis(player: Player) -> str | None:
    """Weekly frames key on GSIS ids; players sync from Sleeper. Prefer the
    stored gsis_id, fall back to a name match in recent frames."""
    if (g := _clean_gsis(player.gsis_id)) is not None:
        return g
    for offset in range(PRIOR_LOOKBACK + 1):
        df = await _player_weekly_frame(latest_completed_season() - offset)
        if df is None:
            continue
        for col in ("player_display_name", "player_name"):
            if col in df.columns:
                hits = df[df[col] == player.full_name]
                if len(hits) and "player_id" in hits.columns:
                    return str(hits["player_id"].iloc[0])
    return None


def _depth_order(player: Player | None) -> int | None:
    if player is None:
        return None
    v = (player.metadata_json or {}).get("depth_chart_order")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


_POS_POOL_SIZE = {"QB": 32, "RB": 50, "WR": 70, "TE": 32}


async def _position_prior_means(prior_seasons: list[int]) -> dict[str, dict[str, float]]:
    """{pos: {stat: starter-average per-game rate}} from the most recent prior
    season with data. Pool = top fantasy usage per position with ≥8 games —
    i.e. 'a typical relevant starter'. Used to regress every prior toward the
    positional mean (Marcel-style), which keeps outliers near market consensus.
    """
    key = f"pos_prior_means:{prior_seasons[0] if prior_seasons else 0}"
    if (v := cache.get(key)) is not None:
        return v
    out: dict[str, dict[str, float]] = {}
    for s in prior_seasons:
        tbl = (await _rate_tables([s])).get(s)
        frame = await _player_weekly_frame(s)
        if tbl is None or frame is None or "position" not in frame.columns:
            continue
        pos_by_pid = frame.groupby("player_id")["position"].first()
        try:
            ppr = tbl[("mean", "fantasy_points_ppr")]
            games = tbl[("meta", "games")]
        except KeyError:
            continue
        for pos, stats in POSITION_STATS.items():
            pids = pos_by_pid[pos_by_pid == pos].index
            sub = ppr.reindex(pids).dropna()
            sub = sub[games.reindex(sub.index) >= 8]
            pool = sub.sort_values(ascending=False).head(_POS_POOL_SIZE.get(pos, 40)).index
            if not len(pool):
                continue
            out[pos] = {}
            for stat in stats:
                try:
                    vals = tbl[("mean", stat)].reindex(pool).dropna()
                except KeyError:
                    continue
                if len(vals):
                    out[pos][stat] = float(vals.mean())
        if out:
            break  # most recent season with data is enough
    cache.set(key, out, CACHE_TTL)
    return out


def _rookie_tier(player: Player) -> str:
    meta = player.metadata_json or {}
    rnd = meta.get("draft_round") or (meta.get("metadata") or {}).get("draft_round")
    try:
        rnd = int(rnd)
    except (TypeError, ValueError):
        return "day2"
    if rnd <= 1:
        return "day1"
    if rnd <= 3:
        return "day2"
    return "day3"


# ---- Positional defense factors ---------------------------------------------

# Stat → the defensive family it faces. Receiving families split by position
# because "good vs WRs" and "good vs TEs" are different skills.
_DEF_FAMILY_BY_STAT: dict[str, str] = {
    "attempts": "pass", "completions": "pass", "passing_yards": "pass",
    "passing_tds": "pass", "interceptions": "pass",
    "carries": "rush", "rushing_yards": "rush", "rushing_tds": "rush",
}
_DEF_SHRINK = 0.5   # regress factors halfway to 1.0 — small weekly samples
_DEF_CLAMP = (0.80, 1.25)


async def positional_defense_factors(season: int) -> dict[str, dict[str, float]]:
    """{team_id: {family: factor}} where factor > 1 means the defense allows
    MORE of that family than league average (a good matchup for the player).

    Families: pass, rush, recv_WR, recv_RB, recv_TE. Built from the weekly
    frame (yards allowed per game to each family), shrunk 50% to the mean.
    This is the position-specific defense upgrade the v1 docstring promised.
    """
    key = f"pos_def_factors:{season}"
    if (v := cache.get(key)) is not None:
        return v
    df = await _player_weekly_frame(season)
    out: dict[str, dict[str, float]] = {}
    if df is None or len(df) == 0 or "opponent_team" not in df.columns:
        cache.set(key, out, CACHE_TTL)
        return out

    def _family_factors(sub: pd.DataFrame, value_col: str) -> dict[str, float]:
        if sub is None or not len(sub) or value_col not in sub.columns:
            return {}
        totals = sub.groupby("opponent_team")[value_col].sum()
        weeks = sub.groupby("opponent_team")["week"].nunique().clip(lower=1)
        per_team = totals / weeks
        league = float(per_team.mean()) if len(per_team) else 0.0
        if league <= 0:
            return {}
        lo, hi = _DEF_CLAMP
        return {
            str(t): float(min(hi, max(lo, 1.0 + _DEF_SHRINK * (f - 1.0))))
            for t, f in (per_team / league).items()
        }

    fams: dict[str, dict[str, float]] = {}
    if "position" in df.columns:
        fams["pass"] = _family_factors(df[df["position"] == "QB"], "passing_yards")
        for p in ("WR", "RB", "TE"):
            fams[f"recv_{p}"] = _family_factors(df[df["position"] == p], "receiving_yards")
    fams["rush"] = _family_factors(df, "rushing_yards")

    teams: set[str] = set()
    for d in fams.values():
        teams |= set(d)
    for t in teams:
        out[t] = {fam: d.get(t, 1.0) for fam, d in fams.items()}
    cache.set(key, out, CACHE_TTL)
    return out


def _defense_factor(
    factors: dict[str, dict[str, float]], opp: str | None, stat: str, position: str,
) -> float:
    if not opp or opp not in factors:
        return 1.0
    fam = _DEF_FAMILY_BY_STAT.get(stat)
    if fam is None:  # receiving stat — family depends on the receiver's position
        fam = f"recv_{position}" if position in ("WR", "RB", "TE") else "recv_WR"
    return factors[opp].get(fam, 1.0)


def _matchup_grade(defense_factor: float) -> str:
    if defense_factor >= 1.10:
        return "A"
    if defense_factor >= 1.03:
        return "B"
    if defense_factor >= 0.97:
        return "C"
    if defense_factor >= 0.90:
        return "D"
    return "F"


# ---- Game environments (the coupling to the game model) ---------------------


async def league_game_environments(
    db: Session, season: int,
) -> dict[str, list[dict[str, Any]]]:
    """{team_id: [game env, ...]} for every REMAINING game, computed once from
    the same Elo + scoring inputs the game predictor uses. Each env carries the
    team's implied points, the opponent's, and the game-script label — the
    player layer's window into the game model."""
    key = f"league_game_envs:{season}"
    if (v := cache.get(key)) is not None:
        return v

    sched = await predictions_service._season_schedule(season, db=db)  # noqa: SLF001
    out: dict[str, list[dict[str, Any]]] = {}
    if sched is None or len(sched) == 0:
        cache.set(key, out, CACHE_TTL)
        return out

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)  # noqa: SLF001
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)  # noqa: SLF001
    aggs = aggs or {}

    remaining = sched[sched["home_score"].isna() | sched["away_score"].isna()]
    for _, g in remaining.sort_values("week").iterrows():
        h, a = g.get("home_team"), g.get("away_team")
        if not h or not a:
            continue
        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        pred = predictions_service.predict_game(
            hr, ar,
            home_off_ppg=(aggs.get(h) or {}).get("points_per_game"),
            away_off_ppg=(aggs.get(a) or {}).get("points_per_game"),
            home_def_ppg_allowed=(aggs.get(h) or {}).get("points_allowed_per_game"),
            away_def_ppg_allowed=(aggs.get(a) or {}).get("points_allowed_per_game"),
        )
        base = {
            "week": _safe_int(g.get("week")),
            "gameday": str(g.get("gameday") or ""),
            "game_id": str(g.get("game_id") or ""),
            "game_script": pred["game_script"],
            "predicted_total": pred["predicted_total"],
        }
        out.setdefault(h, []).append({
            **base, "opponent": a, "is_home": True,
            "exp_pts_for": pred["predicted_home_score"],
            "exp_pts_against": pred["predicted_away_score"],
        })
        out.setdefault(a, []).append({
            **base, "opponent": h, "is_home": False,
            "exp_pts_for": pred["predicted_away_score"],
            "exp_pts_against": pred["predicted_home_score"],
        })
    cache.set(key, out, CACHE_TTL)
    return out


# ---- Posterior construction --------------------------------------------------


async def _stat_posteriors(
    player: Player, gsis_id: str | None, season: int, stats: list[str],
) -> tuple[dict[str, engine.StatPosterior], dict[str, Any]]:
    """Per-stat posterior for one player: prior = up to 3 completed seasons
    before `season` (recency+age weighted, rookie-archetype fallback);
    observed = the target season's weekly rows (empty in the offseason)."""
    latest_done = latest_completed_season()
    prior_first = min(season - 1, latest_done)
    prior_seasons = [prior_first - i for i in range(PRIOR_LOOKBACK)]
    obs_season = season if season <= latest_done else None

    tables = await _rate_tables(prior_seasons + ([obs_season] if obs_season else []))
    pos_means = (await _position_prior_means(prior_seasons)).get(
        (player.position or "").upper(), {}
    )

    posteriors: dict[str, engine.StatPosterior] = {}
    games_observed = 0
    prior_games_total = 0
    used_rookie_prior = False

    for stat in stats:
        season_rates: list[dict | None] = []
        if gsis_id:
            for s in prior_seasons:
                season_rates.append(_rates_for(tables.get(s), gsis_id, stat))
        prior = engine.build_prior(
            stat, season_rates, position=player.position or "", age=player.age,
            position_mean=pos_means.get(stat),
        )
        if prior is None:
            prior = engine.rookie_prior(stat, player.position or "", _rookie_tier(player))
            if prior is not None:
                used_rookie_prior = True
        if prior is None:
            continue
        p_mean, p_sd, p_n = prior

        obs = None
        if gsis_id and obs_season and obs_season in tables:
            obs = _rates_for(tables[obs_season], gsis_id, stat)
        posteriors[stat] = engine.bayesian_update(
            p_mean, p_sd, p_n,
            obs["mean"] if obs else None,
            obs["sd"] if obs else None,
            float(obs["games"]) if obs else 0.0,
        )
        if obs:
            games_observed = max(games_observed, obs["games"])
        prior_games_total = max(
            prior_games_total, sum(r["games"] for r in season_rates if r),
        )

    meta = {
        "prior_seasons": [s for s in prior_seasons if s in tables],
        "prior_games": prior_games_total,
        "games_observed": games_observed,
        "rookie_prior": used_rookie_prior,
        "age": player.age,
    }
    return posteriors, meta


def _market_anchors(db: Session, full_name: str) -> dict[str, dict[str, Any]]:
    """{stat: {line, books}} from the latest consensus prop lines for this
    player's upcoming game. Best-effort — empty when books haven't posted."""
    try:
        from . import player_props_service as props  # lazy: avoids import cycle

        rows = props._latest_rows(db, player_name=full_name)  # noqa: SLF001
        consensus = props._consensus(rows)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in consensus:
        stat = PROP_MARKET_TO_STAT.get(item.get("market") or "")
        if not stat or stat.startswith("__") or item.get("line") is None:
            continue
        # Anchor only volume/yardage stats: their line ≈ the market's median ≈
        # our mean. TD/INT lines (0.5, 1.5) are thresholds, not medians —
        # anchoring a Poisson-ish mean to them would distort the rate.
        if engine.STAT_CLASS.get(stat) not in ("volume", "yardage"):
            continue
        books = int(item.get("books") or 0)
        if books < 2:
            continue
        out[stat] = {"line": float(item["line"]), "books": books}
    return out


# How hard we pull a next-game mean toward the consensus line: 12% per book,
# capped at 40%. The market is the best public forecast — we only keep the
# share of our disagreement the evidence can support (spec: "treat the market
# as the prior and the model as the attempt to find where it's wrong").
_ANCHOR_WEIGHT_PER_BOOK = 0.12
_ANCHOR_WEIGHT_CAP = 0.40


def _project_stat_for_game(
    post: engine.StatPosterior,
    stat: str,
    env: dict[str, Any],
    defense_factor: float,
    weather: dict | None,
    inj_mult: float,
    anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One stat × one game → distribution + product-facing fields."""
    env_mult = engine.game_environment_multiplier(
        stat,
        team_expected_pts=float(env["exp_pts_for"]),
        opp_expected_pts=float(env["exp_pts_against"]),
        defense_factor=defense_factor,
    )
    w_mult = weather_multiplier(weather, stat)
    mean = post.mean * env_mult * w_mult * inj_mult
    sd = post.game_sd  # multipliers shift the mean; week-to-week noise stays

    market_anchor: dict[str, Any] | None = None
    if anchor is not None and mean > 0:
        k = min(_ANCHOR_WEIGHT_CAP, _ANCHOR_WEIGHT_PER_BOOK * anchor["books"])
        raw_mean = mean
        mean = mean + k * (anchor["line"] - mean)
        market_anchor = {
            "line": anchor["line"],
            "books": anchor["books"],
            "weight": round(k, 2),
            "raw_mean": round(raw_mean, 2),
        }

    lo50, hi50 = engine.stat_interval(mean, sd, 0.50)
    lo80, hi80 = engine.stat_interval(mean, sd, 0.80)
    out = {
        "predicted": _round_for_stat(stat, mean),
        "low": _round_for_stat(stat, lo50),
        "high": _round_for_stat(stat, hi50),
        "mean": round(mean, 2),
        "sd": round(sd, 2),
        "interval_80": [_round_for_stat(stat, lo80), _round_for_stat(stat, hi80)],
        "env_multiplier": round(env_mult, 3),
    }
    if market_anchor is not None:
        out["market_anchor"] = market_anchor
    if stat in engine.TD_STATS:
        out["anytime_prob"] = round(engine.anytime_td_prob(mean), 3)
    return out


async def player_game_predictions(
    db: Session, player_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Distribution-first stat projections for the player's next games."""
    season = season or current_or_upcoming_season()
    # Cache key embeds the admin-override version so a new override is served
    # immediately (stale entries orphan + TTL out).
    cache_key = (
        f"player_game_preds_v2:{player_id}:{season}"
        f":{overrides_service.version(db)}"
    )
    if (cached := cache.get(cache_key)) is not None:
        return cached

    player: Player | None = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "error": "player not found"}
    pos = (player.position or "").upper()
    stats = POSITION_STATS.get(pos)
    if not stats:
        return {"player_id": player_id, "position": pos, "error": "unsupported position"}
    team_id = player.team_id
    if not team_id or (player.status or "").strip().lower() == "inactive":
        return {
            "player_id": player_id, "name": player.full_name, "position": pos,
            "team": team_id, "season": season, "games": [],
            "error": "player is not on an active roster — no forward projection",
        }

    gsis = await _resolve_gsis(player)
    posteriors, evidence = await _stat_posteriors(player, gsis, season, stats)
    if not posteriors:
        return {
            "player_id": player_id, "name": player.full_name, "position": pos,
            "team": team_id, "season": season, "games": [],
            "error": "no historical usage — projections unavailable",
        }

    # Role share: depth-chart slot scales the whole distribution (a backup QB
    # projects a sliver of a starter's line, not a starter's line).
    depth = _depth_order(player)
    role_mult = engine.role_multiplier(pos, depth)
    if role_mult < 1.0:
        posteriors = {
            s: engine.scale_posterior(p, role_mult) for s, p in posteriors.items()
        }

    envs = (await league_game_environments(db, season)).get(team_id, [])[:8]
    def_season = season if season <= latest_completed_season() else season - 1
    def_factors = await positional_defense_factors(def_season)

    injury_status = (player.metadata_json or {}).get("injury_status")
    inj_mult = injury_multiplier(injury_status)

    # Market anchors: consensus prop lines for this player's next game (when
    # books have posted them). Blended into the next-game means below.
    anchors = _market_anchors(db, player.full_name)

    # Batch weather for the slate (best-effort).
    try:
        from . import weather_service
        forecasts = await weather_service.forecasts_for_games([
            {"id": e["game_id"],
             "home_team_id": team_id if e["is_home"] else e["opponent"],
             "gameday": e["gameday"]}
            for e in envs
        ])
    except Exception:  # noqa: BLE001
        forecasts = {}

    # Admin overrides for this player's season, keyed (player_id, week) —
    # applied per game below so hand-set stat means (and their fantasy
    # ripple) supersede the model.
    ov_by_week = overrides_service.player_overrides_by_week(db, season)

    games_out = []
    for gi, env in enumerate(envs):
        weather = forecasts.get(env["game_id"], {"available": False})
        predicted: dict[str, dict[str, Any]] = {}
        stat_means: dict[str, float] = {}
        stat_sds: dict[str, float] = {}
        rep_def_factor = 1.0
        for stat in stats:
            post = posteriors.get(stat)
            if post is None:
                continue
            d_factor = _defense_factor(def_factors, env["opponent"], stat, pos)
            if stat in ("passing_yards", "rushing_yards", "receiving_yards"):
                rep_def_factor = d_factor
            predicted[stat] = _project_stat_for_game(
                post, stat, env, d_factor, weather, inj_mult,
                anchor=anchors.get(stat) if gi == 0 else None,
            )
            stat_means[stat] = float(predicted[stat]["mean"])
            stat_sds[stat] = float(predicted[stat]["sd"])

        ov = ov_by_week.get((player_id, int(env["week"] or 0)), {})
        if ov:
            overrides_service.apply_player_game_overrides(ov, predicted, stat_means)

        fantasy = {
            fmt: {
                "mean": round(engine.fantasy_points(stat_means, fmt), 2),
                "sd": round(engine.fantasy_sd(stat_sds, fmt), 2),
            }
            for fmt in engine.SCORING_FORMATS
        }
        if ov:
            overrides_service.apply_fantasy_overrides(ov, fantasy)

        games_out.append({
            "week": env["week"],
            "gameday": env["gameday"],
            "opponent": env["opponent"],
            "is_home": env["is_home"],
            "matchup_grade": _matchup_grade(rep_def_factor),
            "defense_factor": round(rep_def_factor, 3),
            "game_env": {
                "team_implied_pts": env["exp_pts_for"],
                "opp_implied_pts": env["exp_pts_against"],
                "game_script": env["game_script"],
                "predicted_total": env["predicted_total"],
            },
            "weather": {
                "summary": weather_summary_blurb(weather),
                "is_indoor": bool(weather.get("is_indoor")),
                "available": bool(weather.get("available")),
            },
            "injury_status": injury_status,
            "injury_multiplier": round(inj_mult, 2),
            "predicted": predicted,
            "fantasy": fantasy,
        })

    result = {
        "player_id": player_id,
        "name": player.full_name,
        "position": pos,
        "team": team_id,
        "season": season,
        "model_version": MODEL_VERSION,
        "evidence": evidence,
        "role": {"depth_chart_order": depth, "multiplier": round(role_mult, 2)},
        "games": games_out,
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


async def player_season_projection(
    db: Session, player_id: str, season: int | None = None,
) -> dict[str, Any]:
    """YTD + remaining-schedule projection with a full season distribution."""
    season = season or current_or_upcoming_season()
    cache_key = f"player_season_proj_v2:{player_id}:{season}"
    if (cached := cache.get(cache_key)) is not None:
        return cached

    player: Player | None = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "error": "player not found"}
    pos = (player.position or "").upper()
    stats = POSITION_STATS.get(pos)
    if not stats:
        return {"player_id": player_id, "position": pos, "error": "unsupported position"}
    if not player.team_id or (player.status or "").strip().lower() == "inactive":
        return {
            "player_id": player_id, "name": player.full_name, "position": pos,
            "team": player.team_id, "season": season, "stats": {},
            "games_played": 0, "games_remaining": 0,
            "error": "player is not on an active roster — no forward projection",
        }

    gsis = await _resolve_gsis(player)
    posteriors, evidence = await _stat_posteriors(player, gsis, season, stats)
    if not posteriors:
        return {
            "player_id": player_id, "name": player.full_name, "position": pos,
            "team": player.team_id, "season": season, "stats": {},
            "games_played": 0, "games_remaining": 17,
            "error": "no historical usage — projections unavailable",
        }

    # Role share (see player_game_predictions) — scales forward projection only;
    # YTD is what actually happened.
    depth = _depth_order(player)
    role_mult = engine.role_multiplier(pos, depth)
    if role_mult < 1.0:
        posteriors = {
            s: engine.scale_posterior(p, role_mult) for s, p in posteriors.items()
        }

    # YTD totals in the target season (0 in the offseason).
    ytd: dict[str, float] = {s: 0.0 for s in stats}
    games_played = 0
    if season <= latest_completed_season() and gsis:
        df = await _player_weekly_frame(season)
        if df is not None and "player_id" in df.columns:
            sub = df[df["player_id"] == gsis]
            if len(sub):
                games_played = int(sub["week"].nunique())
                for s in stats:
                    if s in sub.columns:
                        ytd[s] = float(pd.to_numeric(sub[s], errors="coerce").fillna(0).sum())

    envs = (await league_game_environments(db, season)).get(player.team_id or "", [])
    games_remaining = len(envs) if envs else max(0, 17 - games_played)
    def_season = season if season <= latest_completed_season() else season - 1
    def_factors = await positional_defense_factors(def_season)

    out_stats: dict[str, dict[str, Any]] = {}
    season_means: dict[str, float] = {}
    season_sds: dict[str, float] = {}
    for stat in stats:
        post = posteriors.get(stat)
        if post is None:
            continue
        if envs:
            game_means = [
                post.mean * engine.game_environment_multiplier(
                    stat,
                    team_expected_pts=float(e["exp_pts_for"]),
                    opp_expected_pts=float(e["exp_pts_against"]),
                    defense_factor=_defense_factor(def_factors, e["opponent"], stat, pos),
                )
                for e in envs
            ]
        else:
            game_means = [post.mean] * games_remaining
        agg = engine.aggregate_season(game_means, post.game_sd, post.talent_sd)
        final_mean = ytd.get(stat, 0.0) + agg["mean"]
        qs = engine.season_quantiles(final_mean, agg["sd"])
        out_stats[stat] = {
            "ytd": _round_for_stat(stat, ytd.get(stat, 0.0)),
            "per_game_pace": _round_for_stat(stat, post.mean),
            "projected_remaining": _round_for_stat(stat, agg["mean"]),
            "projected_final": _round_for_stat(stat, final_mean),
            "low_final": _round_for_stat(stat, qs["p25"]),
            "high_final": _round_for_stat(stat, qs["p75"]),
            "sd_final": round(agg["sd"], 1),
            "quantiles": {k: _round_for_stat(stat, v) for k, v in qs.items()},
        }
        season_means[stat] = final_mean
        season_sds[stat] = agg["sd"]

    fantasy: dict[str, Any] = {}
    for fmt in engine.SCORING_FORMATS:
        f_mean = engine.fantasy_points(season_means, fmt)
        f_sd = engine.fantasy_sd(season_sds, fmt)
        fantasy[fmt] = {
            "mean": round(f_mean, 1),
            "sd": round(f_sd, 1),
            "quantiles": {k: round(v, 1) for k, v in engine.season_quantiles(f_mean, f_sd).items()},
            "per_game": round(f_mean / max(1, games_played + games_remaining), 2),
        }

    result = {
        "player_id": player_id,
        "name": player.full_name,
        "position": pos,
        "team": player.team_id,
        "season": season,
        "games_played": games_played,
        "games_remaining": games_remaining,
        "model_version": MODEL_VERSION,
        "evidence": evidence,
        "role": {"depth_chart_order": depth, "multiplier": round(role_mult, 2)},
        "stats": out_stats,
        "fantasy": fantasy,
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


# ---- Prop probability (used by props service + ad-hoc endpoint) -------------

# The Odds API player-prop market key → our stat key.
PROP_MARKET_TO_STAT: dict[str, str] = {
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
    "player_pass_attempts": "attempts",
    "player_pass_completions": "completions",
    "player_pass_interceptions": "interceptions",
    "player_rush_yds": "rushing_yards",
    "player_rush_attempts": "carries",
    "player_receptions": "receptions",
    "player_reception_yds": "receiving_yards",
    "player_anytime_td": "__anytime_td__",
}


async def stat_over_probability(
    db: Session, player_id: str, stat: str, line: float, season: int | None = None,
) -> dict[str, Any]:
    """P(stat > line) in the player's NEXT game, from the same distribution the
    projections ship. `stat="__anytime_td__"` returns the anytime-TD prob."""
    preds = await player_game_predictions(db, player_id, season)
    games = preds.get("games") or []
    if not games:
        return {"player_id": player_id, "stat": stat, "line": line,
                "error": preds.get("error") or "no upcoming games"}
    nxt = games[0]

    if stat == "__anytime_td__":
        lam = 0.0
        for td_stat in ("rushing_tds", "receiving_tds"):
            s = nxt["predicted"].get(td_stat)
            if s:
                lam += float(s["mean"])
        return {"player_id": player_id, "stat": "anytime_td", "week": nxt["week"],
                "opponent": nxt["opponent"],
                "prob": round(engine.anytime_td_prob(lam), 4),
                "expected_tds": round(lam, 3), "model_version": MODEL_VERSION}

    s = nxt["predicted"].get(stat)
    if not s:
        return {"player_id": player_id, "stat": stat, "line": line,
                "error": "stat not projected for this position"}
    p = engine.stat_over_prob(float(s["mean"]), float(s["sd"]), line)
    return {
        "player_id": player_id, "stat": stat, "line": line,
        "week": nxt["week"], "opponent": nxt["opponent"],
        "mean": s["mean"], "sd": s["sd"],
        "over_prob": round(p, 4), "under_prob": round(1 - p, 4),
        "model_version": MODEL_VERSION,
    }


# ---- Leaderboard --------------------------------------------------------------

_LEADERBOARD_POOL_PER_POS = 90
_MIN_POOL_GAMES = 3

# Depth-chart slots deep enough to matter on a season board, per position —
# gates the supplemental (rookie / no-history) candidate pass.
_SUPPLEMENT_MAX_DEPTH = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}


def _historical_posteriors(
    gsis_id: str,
    pos: str,
    player: Player,
    tables: dict[int, pd.DataFrame],
    prior_seasons: list[int],
    obs_season: int | None,
    pos_means: dict[str, float],
) -> dict[str, engine.StatPosterior]:
    """Posteriors for one player from prior-season rates (+ in-season obs)."""
    posteriors: dict[str, engine.StatPosterior] = {}
    for stat in POSITION_STATS.get(pos, []):
        season_rates = [_rates_for(tables.get(s), gsis_id, stat) for s in prior_seasons]
        prior = engine.build_prior(
            stat, season_rates, position=pos, age=player.age,
            position_mean=pos_means.get(stat),
        )
        if prior is None:
            continue
        obs = _rates_for(tables.get(obs_season), gsis_id, stat) if obs_season else None
        posteriors[stat] = engine.bayesian_update(
            prior[0], prior[1], prior[2],
            obs["mean"] if obs else None,
            obs["sd"] if obs else None,
            float(obs["games"]) if obs else 0.0,
        )
    return posteriors


def _coverage_summary(
    rows: list[dict[str, Any]], all_teams: list[str],
) -> dict[str, Any]:
    """Per-position team coverage — the health check that would have caught
    the 11-QB regression. `missing` lists teams with no projected player at
    that position (QB missing should always be empty after the supplemental
    pass; RB/WR/TE gaps can be legitimate early in a rebuild)."""
    total = sorted(set(all_teams))
    out: dict[str, Any] = {}
    for pos in POSITION_STATS:
        teams = {r["team"] for r in rows if r["position"] == pos and r.get("team")}
        out[pos] = {
            "teams": len(teams & set(total)),
            "total_teams": len(total),
            "missing": sorted(set(total) - teams),
        }
    return out


async def projection_leaderboard(
    db: Session,
    season: int | None = None,
    position: str | None = None,
    scoring: str = "ppr",
    sort: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Season stat projections for every roster-active, projection-relevant
    player. Stat-first: `sort` accepts any projected stat key (e.g.
    "passing_yards", "receiving_yards") or "fantasy" for the cross-position
    composite. Built in bulk and served from cache."""
    season = season or current_or_upcoming_season()
    scoring = scoring if scoring in engine.SCORING_FORMATS else "ppr"
    cache_key = f"projection_leaderboard_v2:{season}:{overrides_service.version(db)}"
    board: dict[str, Any] | None = cache.get(cache_key)
    if board is None or not isinstance(board, dict):
        board = await _build_leaderboard_rows(db, season)
        cache.set(cache_key, board, CACHE_TTL)
    rows = board["rows"]

    # Season-scoped admin overrides (week IS NULL): direct season fantasy-point
    # totals, applied on row copies so the shared cached board stays pristine.
    season_ovs = overrides_service.player_season_overrides(db, season)
    if season_ovs:
        patched: list[dict[str, Any]] = []
        for r in rows:
            ov = season_ovs.get(str(r.get("player_id")))
            if ov:
                r = {**r}
                for fmt in engine.SCORING_FORMATS:
                    v = ov.get(f"fantasy_points_{fmt}")
                    fk = f"fantasy_{fmt}"
                    if v is not None and isinstance(r.get(fk), dict):
                        r[fk] = {**r[fk], "mean": round(max(0.0, float(v)), 2)}
            patched.append(r)
        rows = patched

    filtered = [r for r in rows if not position or r["position"] == position.upper()]

    fantasy_key = f"fantasy_{scoring}"
    sort_key = (sort or "fantasy").strip().lower()
    if sort_key != "fantasy" and sort_key in _ALL_STATS:
        def _sorter(r: dict[str, Any]) -> float:
            s = r["stats"].get(sort_key)
            return float(s["mean"]) if s else -1.0
    else:
        sort_key = "fantasy"
        def _sorter(r: dict[str, Any]) -> float:
            return float(r[fantasy_key]["mean"])
    filtered.sort(key=_sorter, reverse=True)

    # Admin rank pins (field "rank", season-scoped) hold players at a hand-set
    # slot in the fantasy-composite ordering of the current view.
    if sort_key == "fantasy" and season_ovs:
        pins = {
            pid: ov["rank"] for pid, ov in season_ovs.items() if "rank" in ov
        }
        if pins:
            filtered = overrides_service.apply_rank_pins(filtered, pins)

    out_rows = [
        {**r, "rank": i + 1}
        for i, r in enumerate(filtered[: max(1, min(limit, 300))])
    ]
    return {
        "season": season,
        "scoring": scoring,
        "sort": sort_key,
        "position": position.upper() if position else None,
        "model_version": MODEL_VERSION,
        "count": len(out_rows),
        "coverage": board.get("coverage", {}),
        "players": out_rows,
    }


async def _collect_candidates(db: Session, season: int) -> dict[str, Any]:
    """Shared candidate collection for every bulk projection board.

    Runs the pool → roster-match → supplemental → role-assignment pipeline once
    and returns everything a board needs to project any subset of games:

        {"candidates": [...], "envs_by_team": {...}, "def_factors": {...}}

    Each candidate: {gsis_id, pos, player, posteriors, envs, depth, rookie,
    role_mult}. Callers apply role scaling themselves (boards differ on the
    role threshold they care about).
    """
    latest_done = latest_completed_season()
    prior_first = min(season - 1, latest_done)
    prior_seasons = [prior_first - i for i in range(PRIOR_LOOKBACK)]
    obs_season = season if season <= latest_done else None
    all_seasons = ([obs_season] if obs_season else []) + prior_seasons
    tables = await _rate_tables(all_seasons)
    if not tables:
        return {"candidates": [], "envs_by_team": {}, "def_factors": {}}

    # Candidate pool: top PPR-per-game users per position in each frame. Also
    # record each pool member's display name + last team so unmatched gsis ids
    # can fall back to name matching against the (Sleeper-keyed) player table.
    pool: dict[str, str] = {}  # gsis_id -> position
    frame_names: dict[str, dict[str, str | None]] = {}  # gsis_id -> {name, team}
    for s in all_seasons:
        tbl = tables.get(s)
        frame = await _player_weekly_frame(s) if tbl is not None else None
        if tbl is None or frame is None or "position" not in frame.columns:
            continue
        pos_by_pid = frame.groupby("player_id")["position"].first()
        name_col = next(
            (c for c in ("player_display_name", "player_name") if c in frame.columns),
            None,
        )
        names_by_pid = frame.groupby("player_id")[name_col].first() if name_col else None
        teams_by_pid = (
            frame.groupby("player_id")["recent_team"].last()
            if "recent_team" in frame.columns else None
        )
        try:
            ppr = tbl[("mean", "fantasy_points_ppr")]
            games = tbl[("meta", "games")]
        except KeyError:
            continue
        for pos in POSITION_STATS:
            pids = pos_by_pid[pos_by_pid == pos].index
            sub = ppr.reindex(pids).dropna()
            sub = sub[games.reindex(sub.index) >= _MIN_POOL_GAMES]
            for pid in sub.sort_values(ascending=False).head(_LEADERBOARD_POOL_PER_POS).index:
                pid = str(pid)
                pool.setdefault(pid, pos)
                if pid not in frame_names:
                    frame_names[pid] = {
                        "name": str(names_by_pid.get(pid)) if names_by_pid is not None and pid in names_by_pid.index else None,
                        "team": str(teams_by_pid.get(pid)) if teams_by_pid is not None and pid in teams_by_pid.index else None,
                    }
    if not pool:
        return {"candidates": [], "envs_by_team": {}, "def_factors": {}}

    # Roster lookup: load ALL active skill-position players once, index by
    # cleaned gsis AND by normalized name+position. The old code joined on
    # Player.gsis_id verbatim — Sleeper leaves it null / whitespace-padded for
    # most players, which silently dropped the bulk of the pool (the "11 QBs"
    # bug). Name matching recovers those; the sync-time crosswalk backfill
    # (players_service.backfill_gsis_ids) shrinks the fallback set over time.
    roster_players = db.execute(
        select(Player).where(
            Player.position.in_(list(POSITION_STATS.keys())),
            Player.team_id.is_not(None),
        )
    ).scalars().all()
    by_gsis: dict[str, Player] = {}
    by_name: dict[tuple[str, str], list[Player]] = {}
    for p in roster_players:
        if (p.status or "").strip().lower() == "inactive":
            continue
        if (g := _clean_gsis(p.gsis_id)) is not None:
            by_gsis[g] = p
        key = (_normalize_name(p.full_name), (p.position or "").upper())
        by_name.setdefault(key, []).append(p)

    def _match_player(gsis_id: str, pos: str) -> Player | None:
        if (p := by_gsis.get(gsis_id)) is not None:
            return p
        meta = frame_names.get(gsis_id) or {}
        cands = by_name.get((_normalize_name(meta.get("name")), pos)) or []
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:  # same name+pos twice — disambiguate by team
            team = canonical_team(meta.get("team")) if meta.get("team") else None
            return next((c for c in cands if c.team_id == team), None)
        return None

    envs_by_team = await league_game_environments(db, season)
    def_season = season if season <= latest_done else season - 1
    def_factors = await positional_defense_factors(def_season)
    pos_means_all = await _position_prior_means(prior_seasons)

    # ---- Phase 1: gather roster-gated candidates with posteriors -------------
    candidates: list[dict[str, Any]] = []
    used_player_ids: set[str] = set()
    for gsis_id, pos in pool.items():
        player = _match_player(gsis_id, pos)
        # Roster gate: only project players who exist in our (Sleeper-synced)
        # player table AND are on an NFL roster. This is what keeps retired /
        # free-agent players (Sleeper: status "Inactive", team None) out of
        # forward-looking projections even though they dominate past frames.
        if player is None or not player.team_id:
            continue
        if player.id in used_player_ids:  # two frame ids → one roster player
            continue
        envs = envs_by_team.get(player.team_id, [])
        # No remaining games on the schedule for this team → no forward
        # projection. Never fall back to a scheduleless 17-game guess; that's
        # how phantom projections for off-roster players crept in.
        if not envs:
            continue

        posteriors = _historical_posteriors(
            gsis_id, pos, player, tables, prior_seasons, obs_season,
            pos_means_all.get(pos, {}),
        )
        if not posteriors:
            continue
        used_player_ids.add(player.id)
        candidates.append({
            "gsis_id": gsis_id, "pos": pos, "player": player,
            "posteriors": posteriors, "envs": envs,
            "depth": _depth_order(player), "rookie": False,
        })

    # ---- Phase 1.5: supplemental candidates the historical pool can't see ----
    # Rookies and role-changers have no (or thin) NFL history, so they never
    # crack a top-N-by-past-PPR pool — yet a 2026 first-round QB is exactly who
    # the board must show. Add active players holding a meaningful depth-chart
    # slot, using their own history when it exists outside the pool, else the
    # engine's rookie archetype prior.
    for player in roster_players:
        if player.id in used_player_ids or not player.team_id:
            continue
        if (player.status or "").strip().lower() == "inactive":
            continue
        pos = (player.position or "").upper()
        depth = _depth_order(player)
        max_depth = _SUPPLEMENT_MAX_DEPTH.get(pos)
        if max_depth is None or depth is None or depth > max_depth:
            continue
        envs = envs_by_team.get(player.team_id, [])
        if not envs:
            continue

        gsis_id = _clean_gsis(player.gsis_id)
        posteriors: dict[str, engine.StatPosterior] = {}
        rookie = False
        if gsis_id:
            posteriors = _historical_posteriors(
                gsis_id, pos, player, tables, prior_seasons, obs_season,
                pos_means_all.get(pos, {}),
            )
        if not posteriors:
            tier = _rookie_tier(player)
            for stat in POSITION_STATS[pos]:
                prior = engine.rookie_prior(stat, pos, tier)
                if prior is None:
                    continue
                posteriors[stat] = engine.bayesian_update(
                    prior[0], prior[1], prior[2], None, None, 0.0,
                )
            rookie = True
        if not posteriors:
            continue
        used_player_ids.add(player.id)
        candidates.append({
            "gsis_id": gsis_id or player.id, "pos": pos, "player": player,
            "posteriors": posteriors, "envs": envs,
            "depth": depth, "rookie": rookie,
        })

    # ---- Phase 2: role assignment (depth chart; QB fallback = team ranking) --
    # Sleeper depth data can be missing in the offseason. QBs are winner-take-
    # all, so an unknown-depth QB room is ranked by projected passing volume:
    # the top arm is treated as the starter, everyone else as a backup.
    qb_rooms: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        if c["pos"] == "QB":
            qb_rooms.setdefault(c["player"].team_id, []).append(c)
    for room in qb_rooms.values():
        with_depth1 = [c for c in room if c["depth"] == 1]
        if with_depth1:
            starter = with_depth1[0]
        else:
            starter = max(
                room,
                key=lambda c: c["posteriors"].get("passing_yards").mean
                if c["posteriors"].get("passing_yards") else 0.0,
            )
        for c in room:
            if c is starter:
                c["role_mult"] = engine.role_multiplier("QB", c["depth"] or 1)
            else:
                c["role_mult"] = engine.role_multiplier("QB", c["depth"] or 2)
    for c in candidates:
        if "role_mult" not in c:
            c["role_mult"] = engine.role_multiplier(c["pos"], c["depth"])

    return {
        "candidates": candidates,
        "envs_by_team": envs_by_team,
        "def_factors": def_factors,
    }


async def _build_leaderboard_rows(db: Session, season: int) -> dict[str, Any]:
    """Full board build → {"rows": [...], "coverage": {pos: {...}}}."""
    ctx = await _collect_candidates(db, season)
    candidates = ctx["candidates"]
    envs_by_team = ctx["envs_by_team"]
    def_factors = ctx["def_factors"]
    if not candidates:
        return {"rows": [], "coverage": {}}

    # ---- Aggregate seasons for meaningful roles only -------------------------
    rows: list[dict[str, Any]] = []
    for c in candidates:
        role_mult = c["role_mult"]
        if role_mult < engine.ROLE_LEADERBOARD_MIN:
            continue  # backups don't belong on a season projection board
        gsis_id, pos, player, envs = c["gsis_id"], c["pos"], c["player"], c["envs"]
        posteriors = c["posteriors"]
        if role_mult < 1.0:
            posteriors = {
                s: engine.scale_posterior(p, role_mult) for s, p in posteriors.items()
            }
        team_id = player.team_id
        games_remaining = len(envs)

        season_means: dict[str, float] = {}
        season_sds: dict[str, float] = {}
        headline: dict[str, Any] = {}
        for stat, post in posteriors.items():
            game_means = [
                post.mean * engine.game_environment_multiplier(
                    stat,
                    team_expected_pts=float(e["exp_pts_for"]),
                    opp_expected_pts=float(e["exp_pts_against"]),
                    defense_factor=_defense_factor(def_factors, e["opponent"], stat, pos),
                )
                for e in envs
            ]
            agg = engine.aggregate_season(game_means, post.game_sd, post.talent_sd)
            season_means[stat] = agg["mean"]
            season_sds[stat] = agg["sd"]
            headline[stat] = {
                "mean": _round_for_stat(stat, agg["mean"]),
                "p10": _round_for_stat(stat, engine.stat_quantile(agg["mean"], agg["sd"], 0.10)),
                "p90": _round_for_stat(stat, engine.stat_quantile(agg["mean"], agg["sd"], 0.90)),
            }

        fantasy: dict[str, Any] = {}
        for fmt in engine.SCORING_FORMATS:
            f_mean = engine.fantasy_points(season_means, fmt)
            f_sd = engine.fantasy_sd(season_sds, fmt)
            fantasy[f"fantasy_{fmt}"] = {
                "mean": round(f_mean, 1),
                "p10": round(engine.stat_quantile(f_mean, f_sd, 0.10), 1),
                "p90": round(engine.stat_quantile(f_mean, f_sd, 0.90), 1),
                "per_game": round(f_mean / max(1, games_remaining), 2),
            }

        next_env = envs[0]
        rows.append({
            "player_id": player.id,
            "gsis_id": gsis_id,
            "name": player.full_name,
            "position": pos,
            "team": team_id,
            "status": player.status,
            "injury_status": (player.metadata_json or {}).get("injury_status"),
            "role": {"depth_chart_order": c["depth"], "multiplier": round(role_mult, 2)},
            "rookie": bool(c.get("rookie")),
            "games_remaining": games_remaining,
            "next_game": {
                "week": next_env["week"],
                "opponent": next_env["opponent"],
                "is_home": next_env["is_home"],
                "game_script": next_env["game_script"],
            },
            "stats": headline,
            **fantasy,
        })

    coverage = _coverage_summary(rows, list(envs_by_team.keys()))
    for pos, cov in coverage.items():
        if cov["missing"]:
            log.warning(
                "projection_leaderboard_coverage_gap",
                position=pos, missing=cov["missing"], season=season,
            )
    return {"rows": rows, "coverage": coverage}


# ---- Weekly slate board (start/sit) -------------------------------------------

# Representative defense family per position — drives the row's matchup grade.
_GRADE_STAT_BY_POS = {
    "QB": "passing_yards", "RB": "rushing_yards",
    "WR": "receiving_yards", "TE": "receiving_yards",
}

# Start/sit tier boundaries by positional rank (12-team defaults, documented in
# the response). Injury/bye states override these labels.
_TIER_BOUNDS: dict[str, list[tuple[int, str]]] = {
    "QB": [(12, "Start"), (20, "Stream"), (10_000, "Sit")],
    "TE": [(12, "Start"), (18, "Stream"), (10_000, "Sit")],
    "RB": [(12, "Must start"), (24, "Start"), (40, "Flex"), (10_000, "Sit")],
    "WR": [(12, "Must start"), (24, "Start"), (44, "Flex"), (10_000, "Sit")],
}

# Weekly boards care about flex-level roles, not just locked-in starters.
_WEEKLY_ROLE_MIN = 0.15


def _tier_label(pos: str, rank: int) -> str:
    for bound, label in _TIER_BOUNDS.get(pos, [(10_000, "—")]):
        if rank <= bound:
            return label
    return "Sit"


def _bulk_market_anchors(db: Session) -> dict[str, dict[str, dict[str, Any]]]:
    """{player_name_lower: {stat: {line, books}}} for the whole upcoming slate
    in ONE snapshot query — the bulk counterpart of ``_market_anchors``."""
    try:
        from . import player_props_service as props  # lazy: avoids import cycle

        rows = props._latest_rows(db)  # noqa: SLF001
        consensus = props._consensus(rows)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for item in consensus:
        stat = PROP_MARKET_TO_STAT.get(item.get("market") or "")
        if not stat or stat.startswith("__") or item.get("line") is None:
            continue
        if engine.STAT_CLASS.get(stat) not in ("volume", "yardage"):
            continue
        books = int(item.get("books") or 0)
        if books < 2:
            continue
        name = (item.get("player_name") or "").strip().lower()
        if not name:
            continue
        out.setdefault(name, {})[stat] = {"line": float(item["line"]), "books": books}
    return out


async def weekly_projection_board(
    db: Session,
    season: int | None = None,
    week: int | None = None,
    scoring: str = "ppr",
    position: str | None = None,
    limit: int = 400,
) -> dict[str, Any]:
    """Slate-wide per-game projections for one week — the start/sit board.

    For every projectable roster player with a flex-or-better role: the full
    stat kit for that week's game (same engine as the player pages), fantasy
    mean/sd/p10/p90 per scoring format, matchup grade, game environment,
    weather/injury multipliers, and a positional start/sit tier. Players whose
    team is on bye that week are listed with ``bye: true``.
    """
    season = season or current_or_upcoming_season()
    scoring = scoring if scoring in engine.SCORING_FORMATS else "ppr"
    cache_key = (
        f"weekly_board_v1:{season}:{week or 'next'}:{overrides_service.version(db)}"
    )
    board: list[dict[str, Any]] | None = cache.get(cache_key)
    slate_week: int | None = cache.get(f"{cache_key}:week")

    if board is None:
        ctx = await _collect_candidates(db, season)
        candidates, def_factors = ctx["candidates"], ctx["def_factors"]
        envs_by_team = ctx["envs_by_team"]
        if not candidates:
            return {"season": season, "week": week, "scoring": scoring,
                    "model_version": MODEL_VERSION, "players": [], "count": 0}

        # The slate week: explicit, else the earliest remaining week league-wide.
        if week is None:
            weeks = [e["week"] for envs in envs_by_team.values() for e in envs[:1]
                     if e.get("week") is not None]
            slate_week = min(weeks) if weeks else None
        else:
            slate_week = week
        anchors_by_name = _bulk_market_anchors(db)
        slate_ovs = (
            overrides_service.player_week_overrides(db, season, int(slate_week))
            if slate_week is not None else {}
        )

        # One forecast batch for the whole slate (unique games).
        slate_games: dict[str, dict[str, Any]] = {}
        for team, envs in envs_by_team.items():
            for e in envs:
                if e["week"] == slate_week and e["game_id"] not in slate_games:
                    slate_games[e["game_id"]] = {
                        "id": e["game_id"],
                        "home_team_id": team if e["is_home"] else e["opponent"],
                        "gameday": e["gameday"],
                    }
        try:
            from . import weather_service
            forecasts = await weather_service.forecasts_for_games(list(slate_games.values()))
        except Exception:  # noqa: BLE001
            forecasts = {}

        rows: list[dict[str, Any]] = []
        for c in candidates:
            role_mult = c["role_mult"]
            if role_mult < _WEEKLY_ROLE_MIN:
                continue
            player, pos = c["player"], c["pos"]
            env = next((e for e in c["envs"] if e["week"] == slate_week), None)
            base = {
                "player_id": player.id,
                "name": player.full_name,
                "position": pos,
                "team": player.team_id,
                "injury_status": (player.metadata_json or {}).get("injury_status"),
                "role": {"depth_chart_order": c["depth"], "multiplier": round(role_mult, 2)},
                "rookie": bool(c.get("rookie")),
            }
            if env is None:  # team not on this week's slate
                rows.append({**base, "bye": True, "tier": "Bye"})
                continue

            posteriors = c["posteriors"]
            if role_mult < 1.0:
                posteriors = {
                    s: engine.scale_posterior(p, role_mult) for s, p in posteriors.items()
                }
            inj_mult = injury_multiplier(base["injury_status"])
            weather = forecasts.get(env["game_id"], {"available": False})
            anchors = anchors_by_name.get(player.full_name.strip().lower(), {})

            predicted: dict[str, dict[str, Any]] = {}
            stat_means: dict[str, float] = {}
            stat_sds: dict[str, float] = {}
            for stat, post in posteriors.items():
                d_factor = _defense_factor(def_factors, env["opponent"], stat, pos)
                predicted[stat] = _project_stat_for_game(
                    post, stat, env, d_factor, weather, inj_mult,
                    anchor=anchors.get(stat),
                )
                stat_means[stat] = float(predicted[stat]["mean"])
                stat_sds[stat] = float(predicted[stat]["sd"])

            # Admin override layer: hand-set stat means recenter distributions
            # (and re-flow into fantasy); direct fantasy-point overrides win last.
            ov = slate_ovs.get(player.id, {})
            if ov:
                overrides_service.apply_player_game_overrides(ov, predicted, stat_means)

            fantasy: dict[str, Any] = {}
            for fmt in engine.SCORING_FORMATS:
                f_mean = engine.fantasy_points(stat_means, fmt)
                f_sd = engine.fantasy_sd(stat_sds, fmt)
                fantasy[fmt] = {
                    "mean": round(f_mean, 2),
                    "sd": round(f_sd, 2),
                    "p10": round(engine.stat_quantile(f_mean, f_sd, 0.10), 1),
                    "p90": round(engine.stat_quantile(f_mean, f_sd, 0.90), 1),
                }
            if ov:
                overrides_service.apply_fantasy_overrides(ov, fantasy)

            grade_stat = _GRADE_STAT_BY_POS.get(pos, "receiving_yards")
            rep_factor = _defense_factor(def_factors, env["opponent"], grade_stat, pos)
            rows.append({
                **base,
                "bye": False,
                "week": env["week"],
                "opponent": env["opponent"],
                "is_home": env["is_home"],
                "gameday": env["gameday"],
                "matchup_grade": _matchup_grade(rep_factor),
                "defense_factor": round(rep_factor, 3),
                "game_env": {
                    "team_implied_pts": env["exp_pts_for"],
                    "opp_implied_pts": env["exp_pts_against"],
                    "game_script": env["game_script"],
                    "predicted_total": env["predicted_total"],
                },
                "weather": {
                    "summary": weather_summary_blurb(weather),
                    "is_indoor": bool(weather.get("is_indoor")),
                    "available": bool(weather.get("available")),
                },
                "injury_multiplier": round(inj_mult, 2),
                "predicted": predicted,
                "fantasy": fantasy,
            })

        board = rows
        cache.set(cache_key, board, CACHE_TTL)
        cache.set(f"{cache_key}:week", slate_week, CACHE_TTL)

    if slate_week is None:  # board cached but week marker expired — recover it
        slate_week = next((r.get("week") for r in board if r.get("week")), None)

    # Rank + tier within position for the REQUESTED scoring format (cheap; done
    # per request on the cached board).
    def _mean(r: dict[str, Any]) -> float:
        f = (r.get("fantasy") or {}).get(scoring)
        return float(f["mean"]) if f else -1.0

    # Admin rank pins (field "pos_rank", week-scoped): after the natural sort,
    # pinned players are moved to their hand-set positional rank.
    rank_pins: dict[str, float] = {}
    if slate_week is not None:
        for pid, ov in overrides_service.player_week_overrides(
            db, season, int(slate_week)
        ).items():
            if "pos_rank" in ov:
                rank_pins[pid] = ov["pos_rank"]

    by_pos: dict[str, list[dict[str, Any]]] = {}
    for r in board:
        by_pos.setdefault(r["position"], []).append(r)
    out_rows: list[dict[str, Any]] = []
    for pos, rs in by_pos.items():
        rs = sorted(rs, key=_mean, reverse=True)
        if rank_pins:
            rs = overrides_service.apply_rank_pins(rs, rank_pins)
        for i, r in enumerate(rs):
            r = dict(r)
            r["pos_rank"] = i + 1
            if r.get("bye"):
                r["tier"] = "Bye"
            elif r.get("injury_multiplier") == 0.0:
                r["tier"] = "Out"
            else:
                r["tier"] = _tier_label(pos, i + 1)
            out_rows.append(r)

    if position:
        # Positional view: order by pos_rank so admin rank pins hold (identical
        # to the mean sort when no pins exist).
        out_rows = [r for r in out_rows if r["position"] == position.upper()]
        out_rows.sort(key=lambda r: r.get("pos_rank") or 10_000)
    else:
        out_rows.sort(key=_mean, reverse=True)
    out_rows = out_rows[: max(1, min(limit, 600))]
    return {
        "season": season,
        "week": slate_week,
        "scoring": scoring,
        "position": position.upper() if position else None,
        "model_version": MODEL_VERSION,
        "tier_note": "Tiers assume a 12-team league (QB/TE start 12; RB/WR must-start 12, start 24, flex 40/44).",
        "count": len(out_rows),
        "players": out_rows,
    }


# ---- Backtest (interval coverage + CRPS, out-of-sample) ----------------------


async def backtest_player_projections(
    db: Session,
    season: int | None = None,
    start_week: int = 5,
    sample_per_pos: int = 25,
) -> dict[str, Any]:
    """Walk-forward backtest of the v2 engine on a completed season.

    For each week W ≥ start_week: build the posterior from prior seasons plus
    weeks < W only, project week W, then grade against what happened — MAE,
    CRPS, and 50%/80% interval coverage. Coverage ≈ nominal is the "our SDs
    are honest" check, the player-layer analogue of the game model's PIT test.
    """
    season = season or latest_completed_season()
    cache_key = f"player_proj_backtest:{season}:{start_week}:{sample_per_pos}"
    if (v := cache.get(cache_key)) is not None:
        return v

    df = await _player_weekly_frame(season)
    if df is None or len(df) == 0 or "position" not in df.columns:
        return {"season": season, "error": "no weekly data for season"}

    prior_seasons = [season - 1 - i for i in range(PRIOR_LOOKBACK)]
    tables = await _rate_tables(prior_seasons)
    def_factors = await positional_defense_factors(season - 1)
    pos_means_all = await _position_prior_means(prior_seasons)

    sampled: list[tuple[str, str]] = []
    for pos in POSITION_STATS:
        sub = df[df["position"] == pos]
        if not len(sub) or "fantasy_points_ppr" not in sub.columns:
            continue
        top = (
            sub.groupby("player_id")["fantasy_points_ppr"].mean()
            .sort_values(ascending=False).head(sample_per_pos).index
        )
        sampled.extend((str(pid), pos) for pid in top)

    max_week = int(pd.to_numeric(df["week"], errors="coerce").max())
    per_stat: dict[str, dict[str, list[float]]] = {}

    for pid, pos in sampled:
        hist = df[df["player_id"] == pid].sort_values("week")
        if not len(hist):
            continue
        for w in range(start_week, max_week + 1):
            actual_row = hist[hist["week"] == w]
            if not len(actual_row):
                continue
            before = hist[hist["week"] < w]
            opp = (
                actual_row["opponent_team"].iloc[0]
                if "opponent_team" in actual_row.columns else None
            )
            for stat in POSITION_STATS[pos]:
                if stat not in hist.columns:
                    continue
                actual = pd.to_numeric(actual_row[stat], errors="coerce").iloc[0]
                if pd.isna(actual):
                    continue
                season_rates = [_rates_for(tables.get(s), pid, stat) for s in prior_seasons]
                prior = engine.build_prior(
                    stat, season_rates, position=pos, age=None,
                    position_mean=pos_means_all.get(pos, {}).get(stat),
                )
                if prior is None:
                    continue
                obs_vals = pd.to_numeric(before[stat], errors="coerce").dropna()
                post = engine.bayesian_update(
                    prior[0], prior[1], prior[2],
                    float(obs_vals.mean()) if len(obs_vals) else None,
                    float(obs_vals.std()) if len(obs_vals) > 1 else None,
                    float(len(obs_vals)),
                )
                mean = post.mean * _defense_factor(def_factors, opp, stat, pos)
                sd = post.game_sd
                b = per_stat.setdefault(stat, {"ae": [], "crps": [], "in50": [], "in80": []})
                b["ae"].append(abs(float(actual) - mean))
                b["crps"].append(prediction_dist.crps_normal(mean, sd, float(actual)))
                lo50, hi50 = engine.stat_interval(mean, sd, 0.50)
                lo80, hi80 = engine.stat_interval(mean, sd, 0.80)
                b["in50"].append(1.0 if lo50 <= float(actual) <= hi50 else 0.0)
                b["in80"].append(1.0 if lo80 <= float(actual) <= hi80 else 0.0)

    stats_out = {}
    for stat, b in per_stat.items():
        n = len(b["ae"])
        if n == 0:
            continue
        stats_out[stat] = {
            "n": n,
            "mae": round(float(np.mean(b["ae"])), 2),
            "crps": round(float(np.mean(b["crps"])), 2),
            "coverage_50": round(float(np.mean(b["in50"])), 3),
            "coverage_80": round(float(np.mean(b["in80"])), 3),
        }
    result = {
        "season": season,
        "start_week": start_week,
        "sample_per_pos": sample_per_pos,
        "model_version": MODEL_VERSION,
        "note": (
            "coverage_50/coverage_80 should sit near 0.50/0.80 — above means the "
            "bands are too wide (under-confident), below means too tight "
            "(over-confident). CRPS is in stat units; lower is better."
        ),
        "stats": stats_out,
    }
    cache.set(cache_key, result, 60 * 60 * 6)
    return result


# ---- Formatting + retained v1 multipliers ------------------------------------


def _round_for_stat(stat: str, v: float) -> float:
    if v is None:
        return 0.0
    return round(float(v), 1)


_PASSING = {"attempts", "completions", "passing_yards", "passing_tds", "interceptions"}
_RUSHING = {"carries", "rushing_yards", "rushing_tds"}
_RECEIVING = {"targets", "receptions", "receiving_yards", "receiving_tds"}


def weather_multiplier(weather: dict | None, stat: str) -> float:
    """Multiplier (1.0 = no adjustment) for the given stat given the forecast."""
    if not weather or not weather.get("available") or weather.get("is_indoor"):
        return 1.0
    wind = float(weather.get("wind_mph") or 0)
    precip = float(weather.get("precipitation_in") or 0)
    temp = weather.get("temperature_f")
    temp = float(temp) if temp is not None else 65.0

    mult = 1.0
    if stat in _PASSING:
        if wind >= 25:
            mult *= 0.85
        elif wind >= 15:
            mult *= 0.92
        if precip >= 0.4:
            mult *= 0.85
        elif precip >= 0.15:
            mult *= 0.93
        if temp <= 25:
            mult *= 0.95
    elif stat in _RUSHING:
        if wind >= 20 or precip >= 0.4:
            mult *= 1.04
    elif stat in _RECEIVING:
        if wind >= 25:
            mult *= 0.88
        elif wind >= 15:
            mult *= 0.94
        if precip >= 0.4:
            mult *= 0.88
        elif precip >= 0.15:
            mult *= 0.95
    return mult


def injury_multiplier(injury_status: str | None) -> float:
    """Status comes from Sleeper metadata."""
    if not injury_status:
        return 1.0
    s = injury_status.strip().upper()
    if s in ("OUT", "IR", "PUP", "NFI", "SUSPENDED"):
        return 0.0
    if s == "DOUBTFUL":
        return 0.3
    if s == "QUESTIONABLE":
        return 0.85
    if s in ("PROBABLE", "ACTIVE", "HEALTHY"):
        return 1.0
    return 1.0


def weather_summary_blurb(weather: dict | None) -> str | None:
    """One-line, fan-readable description for the UI."""
    if not weather or not weather.get("available"):
        return None
    if weather.get("is_indoor"):
        return "Indoor — no weather impact"
    parts: list[str] = []
    temp = weather.get("temperature_f")
    if temp is not None:
        parts.append(f"{int(temp)}°F")
    wind = weather.get("wind_mph")
    if wind is not None:
        parts.append(f"{int(wind)}mph wind")
    precip = weather.get("precipitation_in") or 0
    if precip >= 0.15:
        parts.append(f"{precip:.2f}in precip")
    summary = weather.get("summary") or ""
    if summary and summary != "—":
        parts.append(summary)
    return " · ".join(parts) if parts else None


def _safe_int(v) -> int | None:
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None
