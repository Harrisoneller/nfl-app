"""Orchestration for the v5 game model (``team_ratings_v5``).

Data flow
---------
1. ``season_rows(season)`` — PBP for one season → compact team-game rows
   (~570 rows). Persisted in ``artifact_cache`` so a restart never re-downloads
   PBP: completed seasons are effectively immutable (30-day TTL), the live
   season refreshes every 3 hours.
2. ``schedules()`` — nflverse games.csv (every season): scores for the points
   ridge, *past* closing spreads for the market prior, and the starting QB ids
   the QB layer prices. Falls back to per-season nfl_data_py schedules.
3. ``week_model(season, week)`` — fits the ratings for one slate, memoized in
   process (the fit is ~0.3s; the inputs are the slow part).

Everything is best-effort: any failure returns ``None`` and predictions fall
back to the v4 Elo × EPA path, so a data outage can never blank the board.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..logging_config import get_logger
from ..models.seed import NFL_TEAMS
from ..utils.seasons import current_or_upcoming_season
from ..utils.teams import canonical_team
from . import artifact_cache
from . import team_ratings_v5 as v5

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

ROWS_VERSION = "v5r1"
_WEEK_MODEL_TTL_S = 30 * 60
_week_models: dict[tuple, tuple[float, v5.WeekModel]] = {}

TEAM_IDS = sorted({t["id"] for t in NFL_TEAMS})


def _p(key: str, default: float) -> float:
    try:
        from . import param_registry

        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — scripts/tests without a DB
        return default


def enabled() -> bool:
    return _p("game.v5_enabled", 1.0) >= 0.5


def params() -> dict[str, float]:
    """Live hyperparameters (registry-backed where exposed)."""
    out = dict(v5.DEFAULT_PARAMS)
    for key, reg in (
        ("half_life_weeks", "v5.half_life_weeks"),
        ("season_carry", "v5.season_carry"),
        ("ridge_lambda_plays", "v5.ridge_lambda_plays"),
        ("qb_replacement_epa", "v5.qb_replacement_epa"),
        ("qb_prior_dropbacks", "v5.qb_prior_dropbacks"),
    ):
        out[key] = _p(reg, out[key])
    return out


def margin_coefs() -> dict[str, float]:
    c = dict(v5.MARGIN_COEFS)
    c["qb_d"] = _p("v5.coef_qb", c["qb_d"])
    c["hfa"] = _p("v5.hfa_pts", c["hfa"])
    c["mkt_d"] = _p("v5.coef_market_prior", c["mkt_d"])
    return c


def margin_coefs_no_mkt() -> dict[str, float]:
    c = dict(v5.MARGIN_COEFS_NO_MKT)
    c["qb_d"] = _p("v5.coef_qb", c["qb_d"])
    c["hfa"] = _p("v5.hfa_pts", c["hfa"])
    return c


# ---- inputs ----------------------------------------------------------------


async def schedules() -> pd.DataFrame | None:
    df = await _nfl.schedules_all_df()
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    for c in ("home_team", "away_team"):
        df[c] = df[c].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    if "game_type" in df.columns:
        df = df[df["game_type"].astype(str).isin(["REG", "WC", "DIV", "CON", "SB"])]
    return df


async def season_rows(season: int, sched: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """Team-game rows for one season (artifact-cached)."""
    live = season >= current_or_upcoming_season()

    async def _compute() -> dict[str, Any]:
        pbp = await _nfl.pbp_df(season)
        if pbp is None or len(pbp) == 0:
            return {"rows": []}
        rows = v5.team_game_rows(pbp, season, team_map=canonical_team, params=params())
        return {"rows": rows.to_dict(orient="records") if len(rows) else []}

    try:
        payload = await artifact_cache.get_or_compute(
            kind="v5_team_game_rows",
            key=f"{season}:{ROWS_VERSION}",
            compute=_compute,
            ttl_seconds=3 * 3600 if live else 30 * 24 * 3600,
            l1_ttl_seconds=600,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("v5_rows_failed", season=season, error=str(e)[:200])
        return None
    recs = (payload or {}).get("rows") or []
    if not recs:
        return None
    rows = pd.DataFrame(recs)
    return v5.attach_points(rows, sched)


# ---- the week model --------------------------------------------------------


async def week_model(season: int, week: int) -> v5.WeekModel | None:
    """Fitted v5 model for one slate, or None (caller falls back to v4)."""
    if not enabled():
        return None
    p = params()
    key = (season, week, tuple(sorted(p.items())), tuple(sorted(margin_coefs().items())))
    hit = _week_models.get(key)
    if hit and time.monotonic() - hit[0] < _WEEK_MODEL_TTL_S:
        return hit[1]
    try:
        sched = await schedules()
        frames = []
        for s in range(season - int(p["seasons_back"]), season + 1):
            r = await season_rows(s, sched)
            if r is not None and len(r):
                frames.append(r)
        if not frames:
            return None
        rows = pd.concat(frames, ignore_index=True)
        wm = v5.build_week_model(
            rows, sched, season, week, TEAM_IDS, p,
            margin_coefs=margin_coefs(),
            margin_coefs_no_mkt=margin_coefs_no_mkt(),
        )
    except Exception as e:  # noqa: BLE001 — never take the board down
        log.warning("v5_week_model_failed", season=season, week=week, error=str(e)[:200])
        return None
    _week_models[key] = (time.monotonic(), wm)
    return wm


def starters_for_week(sched: pd.DataFrame | None, season: int, week: int) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """{(home, away): (home_qb_id, away_qb_id)} from the nflverse schedule."""
    out: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    if sched is None or len(sched) == 0 or "home_qb_id" not in sched.columns:
        return out
    wk = sched[(sched["season"] == season) & (sched["week"] == week)]
    for _, g in wk.iterrows():
        hq = g.get("home_qb_id")
        aq = g.get("away_qb_id")
        out[(g["home_team"], g["away_team"])] = (
            hq if isinstance(hq, str) and hq else None,
            aq if isinstance(aq, str) and aq else None,
        )
    return out


def neutral_games(sched: pd.DataFrame | None, season: int) -> set[tuple[int, str, str]]:
    if sched is None or "location" not in sched.columns:
        return set()
    s = sched[(sched["season"] == season) & (sched["location"] == "Neutral")]
    return {(int(r.week), r.home_team, r.away_team) for r in s.itertuples()}


async def slate_predictions(season: int, week: int) -> dict[tuple[str, str], dict[str, Any]]:
    """{(home, away): v5 prediction dict} for one week. Empty on any failure."""
    wm = await week_model(season, week)
    if wm is None:
        return {}
    sched = await schedules()
    starters = starters_for_week(sched, season, week)
    neutral = neutral_games(sched, season)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if sched is None:
        return out
    wk = sched[(sched["season"] == season) & (sched["week"] == week)]
    for g in wk.itertuples():
        h, a = g.home_team, g.away_team
        if not (wm.has_team(h) and wm.has_team(a)):
            continue
        hq, aq = starters.get((h, a), (None, None))
        pred = wm.predict(h, a, neutral=(int(g.week), h, a) in neutral, home_qb=hq, away_qb=aq)
        pred["home_qb_id"] = hq or wm.qb.last_qb.get(h)
        pred["away_qb_id"] = aq or wm.qb.last_qb.get(a)
        pred["home_qb_name"] = getattr(g, "home_qb_name", None) if hq else None
        pred["away_qb_name"] = getattr(g, "away_qb_name", None) if aq else None
        out[(h, a)] = pred
    return out


def predict_any(wm: v5.WeekModel | None, home: str, away: str, neutral: bool = False) -> dict[str, Any] | None:
    """Prediction for a matchup outside the slate (season sims, envs)."""
    if wm is None or not (wm.has_team(home) and wm.has_team(away)):
        return None
    return wm.predict(home, away, neutral=neutral)
