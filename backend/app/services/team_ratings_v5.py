"""Team ratings v5 — recency-weighted, opponent-adjusted, QB-aware (pure math).

Why this replaced the Elo × season-to-date-EPA blend (``epa-elo-ctx-v4``)
-----------------------------------------------------------------------
A walk-forward backtest against closing lines (2016–2026, every number built
only from data available before kickoff — see ``docs/MODEL_V5.md``) found
four structural problems with v4:

1. **Season walls.** v4 fit EPA on season-to-date games only, then blended in
   a 50%-regressed copy of last season's *already ridge-shrunk* values. Weeks
   1–4 were double-shrunk noise. v5 fits one ridge over the last three seasons
   with exponential recency weights (half-life in weeks) and a per-season
   carry factor, so evidence fades continuously instead of falling off a cliff.
2. **No QB term.** The single largest driver of an NFL spread was invisible
   unless the injury feed happened to fire. v5 rates every quarterback on his
   own opponent-adjusted dropbacks (shrunk toward a replacement level that
   slides with career volume) and prices the *difference* between this
   week's starter and the QB mix the team's numbers were built on.
3. **Garbage time and outliers.** Raw EPA/play counted kneel-downs, 4th-quarter
   blowouts and 90-yard busts at full weight. v5 down-weights low/high
   win-probability snaps, drops kneels/spikes and winsorizes EPA.
4. **Hand-set scale.** ``points_per_net_epa``/``w_fundamentals`` were guesses
   and the result was compressed (slope 0.71 vs the market). v5's stage-2
   weights are regression coefficients fit on historical outcomes.

Out of sample (2021–2026): margin RMSE 13.14 → 12.92, correlation with the
closing line 0.85 → 0.91, slope 0.73 → 0.90. Totals RMSE 13.45 → 13.22.

Layout
------
* ``team_game_rows``      PBP → one row per (game, offense) with weighted sums.
* ``fit_ratings``         decayed cross-season ridge per metric.
* ``QBBook``              quarterback ratings + each team's recent QB mix.
* ``market_ratings``      power ratings implied by *past* closing spreads.
* ``WeekModel``           everything above for one (season, week); exposes
                          ``features(home, away, home_qb, away_qb)`` and
                          ``predict(...)``.

Everything here is pandas/numpy only — no DB, no network, no registry — so
the backtest script and the service share one implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

MODEL_VERSION = "qb-ridge-v5"

# ---- Hyperparameters (validated on 2016–2020, held out 2021–2026) ----------

DEFAULT_PARAMS: dict[str, float] = {
    # team ratings
    "half_life_weeks": 10.0,     # recency half-life, in game weeks
    "season_carry": 0.60,        # extra weight multiplier per season boundary
    "ridge_lambda_plays": 5.0,   # per-play metrics: pseudo-games of shrinkage
    "ridge_lambda_games": 8.0,   # per-game metrics (points, ST): pseudo-games
    "seasons_back": 2.0,         # prior seasons in the window (plus current)
    # play filters
    "garbage_wp_lo": 0.10,
    "garbage_wp_hi": 0.90,
    "garbage_weight": 0.35,      # weight of snaps outside the WP band
    "blowout_weight": 0.20,      # 4th quarter, margin >= 17
    "epa_clip": 4.5,             # winsorize per-play EPA at ±this
    # quarterback layer
    "qb_half_life_weeks": 40.0,
    "qb_prior_dropbacks": 250.0, # pseudo-dropbacks of prior in each QB rating
    "qb_replacement_epa": -0.10, # prior for a QB with no volume (EPA/db vs avg)
    "qb_vet_dropbacks": 600.0,   # career volume at which the prior reaches avg
    "qb_change_threshold": 0.015,  # |starter − mix| that counts as a change
    # market prior (power ratings from past closing spreads)
    "mkt_half_life_weeks": 6.0,
    "mkt_season_carry": 0.50,
    "mkt_ridge": 2.0,
}

# ---- Stage-2 coefficients (OLS on 2015–2026 wk3 outcomes; see docs/MODEL_V5.md) ---
# Margin is home − away. Feature "x_d" = (home off + away def) − (away off +
# home def) for metric x; "qb_d" = home QB delta − away QB delta (EPA/db);
# "mkt_d" = market-implied rating difference (points).
MARGIN_COEFS: dict[str, float] = {
    "mkt_d": 0.669, "epa_d": 6.266, "pts_d": 0.644, "st_d": 0.961, "qb_d": 34.977,
    "hfa": 1.824,
}
# Fallback when no historical lines are available (no market prior).
MARGIN_COEFS_NO_MKT: dict[str, float] = {
    "epa_d": 19.114, "pts_d": 0.928, "st_d": 0.927, "qb_d": 34.326, "hfa": 1.833,
}
# Total = const + Σ coef·feature; "x_s" = sum of both sides, "env2" =
# 2 × the weighted league points intercept (scoring environment drift).
TOTAL_COEFS: dict[str, float] = {
    "const": 26.259, "pts_s": 0.417, "epa_s": 20.335, "env2": 0.446,
    "plays_s": 0.297, "qb_s": 28.654,
}
# Residual SD of the margin fit (points) — the honest game-level noise floor.
MARGIN_RESID_SD = 12.89

METRICS = ("epa", "pass", "pts", "st", "plays")

_PBP_REQUIRED = (
    "game_id", "posteam", "defteam", "home_team", "epa", "week",
)


def _p(params: dict[str, float] | None, key: str) -> float:
    if params and key in params and params[key] is not None:
        return float(params[key])
    return float(DEFAULT_PARAMS[key])


def _identity(x: Any) -> Any:
    return x


# =============================================================================
# 1. PBP → team-game rows
# =============================================================================


def team_game_rows(
    pbp: pd.DataFrame,
    season: int,
    *,
    team_map: Callable[[Any], Any] = _identity,
    params: dict[str, float] | None = None,
) -> pd.DataFrame:
    """One row per (game_id, posteam) with weighted sums for the ridge fits.

    Scrimmage plays only (``pass``/``rush`` flags), kneels and spikes dropped.
    Each snap gets a garbage-time weight: full inside the win-probability band,
    ``garbage_weight`` outside it, ``blowout_weight`` in a 4th-quarter rout.
    EPA is winsorized at ±``epa_clip`` so one busted coverage doesn't define a
    defense. Special-teams EPA is summed separately (it is real but noisy).

    Output columns: game_id, season, week, posteam, defteam, home_team,
    is_home, n, gw, w_epa, p_w, p_epa, st_epa, qb.
    """
    if pbp is None or len(pbp) == 0 or not set(_PBP_REQUIRED).issubset(pbp.columns):
        return pd.DataFrame()
    df = pbp[pbp["posteam"].notna() & pbp["epa"].notna()].copy()
    for c in ("posteam", "defteam", "home_team"):
        df[c] = df[c].map(lambda v: team_map(v) if isinstance(v, str) else v)

    is_pass = df["pass"].fillna(0).astype(float) if "pass" in df.columns else (
        df.get("play_type", pd.Series(index=df.index, dtype=object)).eq("pass").astype(float)
    )
    is_rush = df["rush"].fillna(0).astype(float) if "rush" in df.columns else (
        df.get("play_type", pd.Series(index=df.index, dtype=object)).eq("run").astype(float)
    )
    kneel = df["qb_kneel"].fillna(0).astype(float) if "qb_kneel" in df.columns else 0.0
    spike = df["qb_spike"].fillna(0).astype(float) if "qb_spike" in df.columns else 0.0
    scrim_mask = ((is_pass == 1) | (is_rush == 1)) & (kneel != 1) & (spike != 1)

    # Special teams EPA (posteam perspective) before filtering to scrimmage.
    if "special_teams_play" in df.columns:
        st = (
            df[df["special_teams_play"].fillna(0) == 1]
            .groupby(["game_id", "posteam"])["epa"].sum().rename("st_epa")
        )
    else:
        st = pd.Series(dtype=float, name="st_epa")

    scr = df[scrim_mask].copy()
    scr["isp"] = is_pass[scrim_mask].astype(float)
    wp = scr["wp"].fillna(0.5) if "wp" in scr.columns else pd.Series(0.5, index=scr.index)
    lo, hi = _p(params, "garbage_wp_lo"), _p(params, "garbage_wp_hi")
    gw = np.where((wp >= lo) & (wp <= hi), 1.0, _p(params, "garbage_weight"))
    if {"qtr", "score_differential"}.issubset(scr.columns):
        blow = (scr["qtr"].fillna(0) >= 4) & (scr["score_differential"].abs().fillna(0) >= 17)
        gw = np.where(blow, _p(params, "blowout_weight"), gw)
    scr["gw"] = gw
    clip = _p(params, "epa_clip")
    scr["w_epa"] = scr["gw"] * scr["epa"].clip(-clip, clip)
    scr["p_w"] = scr["gw"] * scr["isp"]
    scr["p_epa"] = scr["w_epa"] * scr["isp"]

    g = scr.groupby(["game_id", "posteam", "defteam"], as_index=False).agg(
        week=("week", "first"),
        home_team=("home_team", "first"),
        n=("epa", "size"),
        gw=("gw", "sum"),
        w_epa=("w_epa", "sum"),
        p_w=("p_w", "sum"),
        p_epa=("p_epa", "sum"),
    )
    g = g.merge(st, left_on=["game_id", "posteam"], right_index=True, how="left")
    g["st_epa"] = g["st_epa"].fillna(0.0)

    # Primary passer per team-game (most dropbacks) — the QB the pass numbers belong to.
    if {"passer_player_id", "qb_dropback"}.issubset(df.columns):
        qb = (
            df[df["qb_dropback"].fillna(0) == 1]
            .dropna(subset=["passer_player_id"])
            .groupby(["game_id", "posteam", "passer_player_id"]).size()
            .rename("db").reset_index()
            .sort_values("db", ascending=False)
            .drop_duplicates(["game_id", "posteam"])
            .rename(columns={"passer_player_id": "qb"})[["game_id", "posteam", "qb"]]
        )
        g = g.merge(qb, on=["game_id", "posteam"], how="left")
    else:
        g["qb"] = None
    g["season"] = int(season)
    g["is_home"] = (g["posteam"] == g["home_team"]).astype(float)
    g["week"] = g["week"].astype(int)
    return g


def attach_points(rows: pd.DataFrame, schedule: pd.DataFrame | None) -> pd.DataFrame:
    """Add points scored (``pts``) from a schedule with game_id/home/away scores."""
    if rows is None or len(rows) == 0:
        return rows
    out = rows.copy()
    if schedule is None or len(schedule) == 0 or "game_id" not in schedule.columns:
        out["pts"] = np.nan
        return out
    sc = schedule[["game_id", "home_score", "away_score"]].drop_duplicates("game_id")
    out = out.drop(columns=[c for c in ("home_score", "away_score", "pts") if c in out.columns])
    out = out.merge(sc, on="game_id", how="left")
    out["pts"] = np.where(out["is_home"] == 1, out["home_score"], out["away_score"])
    return out.drop(columns=["home_score", "away_score"])


# =============================================================================
# 2. Decayed cross-season ridge
# =============================================================================


def _week_index(season: Any, week: Any) -> np.ndarray:
    return np.asarray(season, dtype=float) * 18.0 + np.minimum(np.asarray(week, dtype=float), 18.0)


def _team_ridge(
    rows: pd.DataFrame, val: np.ndarray, wt: np.ndarray, lam: float, teams: list[str],
) -> tuple[dict[str, float], dict[str, float], float, float]:
    """Weighted ridge ``y = ic + off_i + def_j + hfa·home``; effects centered."""
    ti = {t: i for i, t in enumerate(teams)}
    n, k = len(rows), len(teams)
    x = np.zeros((n, 2 * k + 2))
    x[np.arange(n), rows["posteam"].map(ti).to_numpy()] = 1.0
    x[np.arange(n), k + rows["defteam"].map(ti).to_numpy()] = 1.0
    x[:, 2 * k] = 1.0
    x[:, 2 * k + 1] = rows["is_home"].to_numpy(dtype=float)
    pen = np.full(2 * k + 2, float(lam))
    pen[2 * k:] = 1e-6
    w = np.asarray(wt, dtype=float)
    y = np.nan_to_num(np.asarray(val, dtype=float))
    xtw = x.T * w
    a = xtw @ x + np.diag(pen)
    try:
        c = np.linalg.solve(a, xtw @ y)
    except np.linalg.LinAlgError:  # pragma: no cover — penalty keeps A PD
        c = np.linalg.lstsq(a, xtw @ y, rcond=None)[0]
    off, de = c[:k], c[k:2 * k]
    ic = float(c[2 * k] + off.mean() + de.mean())
    off, de = off - off.mean(), de - de.mean()
    return (
        {t: float(v) for t, v in zip(teams, off)},
        {t: float(v) for t, v in zip(teams, de)},
        ic,
        float(c[2 * k + 1]),
    )


Ratings = dict[str, tuple[dict[str, float], dict[str, float], float, float]]


def _metric_table(rows: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray | None]]:
    """(per-row value, per-row play weight or None for per-game metrics)."""
    gw = rows["gw"].clip(lower=1.0).to_numpy(dtype=float)
    pw = rows["p_w"].clip(lower=1.0).to_numpy(dtype=float)
    return {
        "epa": (rows["w_epa"].to_numpy(dtype=float) / gw, rows["gw"].to_numpy(dtype=float)),
        "pass": (rows["p_epa"].to_numpy(dtype=float) / pw, rows["p_w"].to_numpy(dtype=float)),
        "pts": (rows["pts"].to_numpy(dtype=float), None),
        "st": (rows["st_epa"].to_numpy(dtype=float), None),
        "plays": (rows["n"].to_numpy(dtype=float), None),
    }


def fit_ratings(
    rows: pd.DataFrame,
    season: int,
    week: int,
    teams: Iterable[str],
    params: dict[str, float] | None = None,
) -> Ratings:
    """Ratings using only games before (season, week).

    Each team-game row is weighted by ``0.5^(age/half_life) ·
    season_carry^(season gap)`` (times plays for per-play metrics). The ridge
    penalty is in pseudo-games, so a team needs about that much decayed
    evidence before its effect is trusted at full strength — which is also the
    whole early-season story: in week 1 the "evidence" is last year's games at
    reduced weight, shrunk toward average by the same penalty.
    """
    teams = sorted(set(teams))
    back = int(_p(params, "seasons_back"))
    if rows is None or len(rows) == 0:
        zero = {t: 0.0 for t in teams}
        return {m: (zero, zero, 0.0, 0.0) for m in METRICS}
    r = rows[
        (rows["season"] >= season - back)
        & ((rows["season"] < season) | ((rows["season"] == season) & (rows["week"] < week)))
        & rows["posteam"].isin(teams) & rows["defteam"].isin(teams)
    ]
    r = r[r["pts"].notna()] if "pts" in r.columns else r
    if len(r) == 0:
        zero = {t: 0.0 for t in teams}
        return {m: (zero, zero, 0.0, 0.0) for m in METRICS}
    age = _week_index(season, week) - _week_index(r["season"], r["week"])
    dec = np.power(0.5, age / _p(params, "half_life_weeks")) * np.power(
        _p(params, "season_carry"), season - r["season"].to_numpy(dtype=float)
    )
    out: Ratings = {}
    for m, (val, plays) in _metric_table(r).items():
        if plays is not None:
            wt = plays * dec
            lam = _p(params, "ridge_lambda_plays") * float(np.mean(plays))
        else:
            wt = dec
            lam = _p(params, "ridge_lambda_games")
        out[m] = _team_ridge(r, val, wt, lam, teams)
    return out


# =============================================================================
# 3. Quarterbacks
# =============================================================================


@dataclass
class QBBook:
    """QB ratings and each team's recent QB mix as of one (season, week).

    A QB's rating is his decayed, play-weighted mean of opponent-adjusted pass
    EPA per dropback, shrunk toward a prior of ``qb_prior_dropbacks``
    pseudo-dropbacks. The prior *slides with career volume*: replacement level
    for a QB with no history, league average once he has ``qb_vet_dropbacks``.
    Shrinking every QB toward the league mean is the classic error — it makes
    a 25-attempt backup look starter-average and erases the swing.

    ``delta(team, qb)`` = rating(qb) − the dropback-weighted rating of the QBs
    whose snaps built the team's pass rating. That is exactly the part of a
    QB change the team ratings cannot see.
    """

    ratings: dict[str, float] = field(default_factory=dict)
    dropbacks: dict[str, float] = field(default_factory=dict)
    team_mix: dict[str, float] = field(default_factory=dict)
    last_qb: dict[str, str] = field(default_factory=dict)
    replacement: float = DEFAULT_PARAMS["qb_replacement_epa"]
    vet_dropbacks: float = DEFAULT_PARAMS["qb_vet_dropbacks"]

    def rating(self, qb: str | None) -> float:
        if not qb or not isinstance(qb, str):
            return self.replacement
        if qb in self.ratings:
            return self.ratings[qb]
        return self.replacement  # never seen: a true unknown sits at replacement

    def delta(self, team: str, qb: str | None) -> float:
        qb = qb if isinstance(qb, str) and qb else self.last_qb.get(team)
        return self.rating(qb) - self.team_mix.get(team, self.replacement)


def pass_defense_by_season(
    rows: pd.DataFrame,
    season: int,
    week: int,
    teams: Iterable[str],
    params: dict[str, float] | None = None,
) -> dict[int, tuple[dict[str, float], float, float]]:
    """Per-season pass-EPA ridge (defense effects, intercept, hfa).

    Past seasons use the full season, equally weighted; the current season uses
    games before ``week``. Used only to opponent-adjust each QB game.
    """
    teams = sorted(set(teams))
    out: dict[int, tuple[dict[str, float], float, float]] = {}
    for s in sorted(set(rows["season"].astype(int))):
        if s > season:
            continue
        r = rows[(rows["season"] == s) & ((s < season) | (rows["week"] < week))]
        r = r[r["posteam"].isin(teams) & r["defteam"].isin(teams)]
        if len(r) < 20:
            continue
        pw = r["p_w"].clip(lower=1.0).to_numpy(dtype=float)
        val = r["p_epa"].to_numpy(dtype=float) / pw
        lam = _p(params, "ridge_lambda_plays") * float(np.mean(pw))
        _off, de, ic, hfa = _team_ridge(r, val, r["p_w"].to_numpy(dtype=float), lam, teams)
        out[s] = (de, ic, hfa)
    return out


def build_qb_book(
    rows: pd.DataFrame,
    season: int,
    week: int,
    teams: Iterable[str],
    params: dict[str, float] | None = None,
) -> QBBook:
    teams = sorted(set(teams))
    rep = _p(params, "qb_replacement_epa")
    vet = _p(params, "qb_vet_dropbacks")
    book = QBBook(replacement=rep, vet_dropbacks=vet)
    if rows is None or len(rows) == 0 or "qb" not in rows.columns:
        return book
    hist = rows[
        rows["qb"].notna()
        & ((rows["season"] < season) | ((rows["season"] == season) & (rows["week"] < week)))
    ]
    if len(hist) == 0:
        return book
    pdef = pass_defense_by_season(hist, season, week, teams, params)
    h = hist[hist["season"].astype(int).isin(pdef.keys())].copy()
    if len(h) == 0:
        return book
    pw = h["p_w"].clip(lower=1.0).to_numpy(dtype=float)
    raw = h["p_epa"].to_numpy(dtype=float) / pw
    adj = np.empty(len(h))
    for i, (s, d, home) in enumerate(zip(h["season"].astype(int), h["defteam"], h["is_home"])):
        de, ic, hfa = pdef[s]
        adj[i] = raw[i] - ic - de.get(d, 0.0) - (hfa if home == 1 else 0.0)
    h["adj"] = adj
    now = float(_week_index(season, week))
    h["dec"] = np.power(0.5, (now - _week_index(h["season"], h["week"])) / _p(params, "qb_half_life_weeks")) * h["p_w"]

    k = _p(params, "qb_prior_dropbacks")
    for qb, g in h.groupby("qb"):
        db = float(g["p_w"].sum())
        prior = rep * max(0.0, 1.0 - db / vet)
        book.ratings[qb] = float((np.sum(g["dec"] * g["adj"]) + k * prior) / (g["dec"].sum() + k))
        book.dropbacks[qb] = db

    # Team mix over the same decayed window the team ratings use.
    back = int(_p(params, "seasons_back"))
    win = hist[hist["season"] >= season - back]
    if len(win):
        dw = np.power(0.5, (now - _week_index(win["season"], win["week"])) / _p(params, "half_life_weeks")) \
            * np.power(_p(params, "season_carry"), season - win["season"].to_numpy(dtype=float)) \
            * win["p_w"].to_numpy(dtype=float)
        w2 = win.assign(_dw=dw, _r=win["qb"].map(lambda q: book.rating(q)))
        for t, g in w2.groupby("posteam"):
            if g["_dw"].sum() > 0:
                book.team_mix[t] = float(np.sum(g["_dw"] * g["_r"]) / g["_dw"].sum())
        last = w2.sort_values(["season", "week"]).groupby("posteam")["qb"].last()
        book.last_qb = {t: q for t, q in last.items() if isinstance(q, str)}
    return book


# =============================================================================
# 4. Market prior: power ratings implied by past closing spreads
# =============================================================================


def market_ratings(
    schedule: pd.DataFrame | None,
    season: int,
    week: int,
    teams: Iterable[str],
    params: dict[str, float] | None = None,
) -> tuple[dict[str, float], float] | None:
    """Least-squares team ratings from *previous* games' closing spreads.

    ``spread_line`` is nflverse's convention (home favored by, positive). Only
    games before (season, week) — this is the market's past opinion, a strong
    prior, never the line for the game being predicted.
    """
    teams = sorted(set(teams))
    if schedule is None or len(schedule) == 0 or "spread_line" not in schedule.columns:
        return None
    s = schedule[
        schedule["spread_line"].notna()
        & (schedule["season"] >= season - 1)
        & ((schedule["season"] < season) | ((schedule["season"] == season) & (schedule["week"] < week)))
        & schedule["home_team"].isin(teams) & schedule["away_team"].isin(teams)
    ]
    if len(s) < 16:
        return None
    ti = {t: i for i, t in enumerate(teams)}
    n, k = len(s), len(teams)
    x = np.zeros((n, k + 1))
    x[np.arange(n), s["home_team"].map(ti).to_numpy()] += 1.0
    x[np.arange(n), s["away_team"].map(ti).to_numpy()] -= 1.0
    neutral = (s["location"] == "Neutral").to_numpy(dtype=float) if "location" in s.columns else np.zeros(n)
    x[:, k] = 1.0 - neutral
    age = _week_index(season, week) - _week_index(s["season"], s["week"])
    w = np.power(0.5, age / _p(params, "mkt_half_life_weeks")) * np.power(
        _p(params, "mkt_season_carry"), season - s["season"].to_numpy(dtype=float)
    )
    pen = np.full(k + 1, _p(params, "mkt_ridge"))
    pen[k] = 1e-6
    xtw = x.T * w
    b = np.linalg.solve(xtw @ x + np.diag(pen), xtw @ s["spread_line"].to_numpy(dtype=float))
    r = b[:k] - b[:k].mean()
    return {t: float(v) for t, v in zip(teams, r)}, float(b[k])


# =============================================================================
# 5. One week's model
# =============================================================================


@dataclass
class WeekModel:
    season: int
    week: int
    ratings: Ratings
    qb: QBBook
    market: tuple[dict[str, float], float] | None
    params: dict[str, float] | None = None
    margin_coefs: dict[str, float] = field(default_factory=lambda: dict(MARGIN_COEFS))
    margin_coefs_no_mkt: dict[str, float] = field(default_factory=lambda: dict(MARGIN_COEFS_NO_MKT))
    total_coefs: dict[str, float] = field(default_factory=lambda: dict(TOTAL_COEFS))

    def has_team(self, team: str) -> bool:
        return team in self.ratings.get("epa", ({}, {}, 0, 0))[0]

    def features(
        self, home: str, away: str, home_qb: str | None = None, away_qb: str | None = None,
    ) -> dict[str, float]:
        f: dict[str, float] = {}
        for m, (off, de, ic, _hfa) in self.ratings.items():
            nh = off.get(home, 0.0) + de.get(away, 0.0)
            na = off.get(away, 0.0) + de.get(home, 0.0)
            f[f"{m}_d"] = nh - na
            f[f"{m}_s"] = nh + na
            f[f"{m}_ic"] = ic
        qh = self.qb.delta(home, home_qb)
        qa = self.qb.delta(away, away_qb)
        f["qb_h"], f["qb_a"] = qh, qa
        f["qb_d"], f["qb_s"] = qh - qa, qh + qa
        f["env2"] = 2.0 * f.get("pts_ic", 0.0)
        if self.market is not None:
            f["mkt_d"] = self.market[0].get(home, 0.0) - self.market[0].get(away, 0.0)
        return f

    def predict(
        self,
        home: str,
        away: str,
        *,
        neutral: bool = False,
        home_qb: str | None = None,
        away_qb: str | None = None,
    ) -> dict[str, Any]:
        f = self.features(home, away, home_qb, away_qb)
        coefs = self.margin_coefs if "mkt_d" in f else self.margin_coefs_no_mkt
        hfa = 0.0 if neutral else coefs.get("hfa", 0.0)
        parts = {k: coefs[k] * f.get(k, 0.0) for k in coefs if k != "hfa"}
        margin = sum(parts.values()) + hfa
        tparts = {k: self.total_coefs[k] * f.get(k, 0.0) for k in self.total_coefs if k != "const"}
        total = self.total_coefs.get("const", 0.0) + sum(tparts.values())
        qbt = _p(self.params, "qb_change_threshold")
        return {
            "model_version": MODEL_VERSION,
            "margin": float(margin),
            "total": float(total),
            "features": {k: round(float(v), 4) for k, v in f.items()},
            "margin_parts": {**{k: round(float(v), 2) for k, v in parts.items()}, "hfa": round(hfa, 2)},
            "total_parts": {k: round(float(v), 2) for k, v in tparts.items()},
            "uses_market_prior": "mkt_d" in f,
            "qb_change": {
                "home": abs(f["qb_h"]) >= qbt,
                "away": abs(f["qb_a"]) >= qbt,
            },
        }


def build_week_model(
    rows: pd.DataFrame,
    schedule: pd.DataFrame | None,
    season: int,
    week: int,
    teams: Iterable[str],
    params: dict[str, float] | None = None,
    *,
    margin_coefs: dict[str, float] | None = None,
    margin_coefs_no_mkt: dict[str, float] | None = None,
    total_coefs: dict[str, float] | None = None,
) -> WeekModel:
    teams = sorted(set(teams))
    wm = WeekModel(
        season=season,
        week=week,
        ratings=fit_ratings(rows, season, week, teams, params),
        qb=build_qb_book(rows, season, week, teams, params),
        market=market_ratings(schedule, season, week, teams, params),
        params=params,
    )
    if margin_coefs:
        wm.margin_coefs = dict(margin_coefs)
    if margin_coefs_no_mkt:
        wm.margin_coefs_no_mkt = dict(margin_coefs_no_mkt)
    if total_coefs:
        wm.total_coefs = dict(total_coefs)
    return wm
