"""Player opportunity model (``player-opp-v1``) — opportunity × efficiency, median-first.

What it fixes
-------------
``player-proj-v2`` projected each stat as a shrunk per-game *mean* times a
game-environment multiplier, and priced props off a Normal around that mean.
A walk-forward backtest (2021–2026, see ``docs/MODEL_V5.md``) showed two
problems:

* **Mean ≠ the line.** Yardage is right-skewed: a receiver's typical game is
  well below his average one. Actual results beat the shipped projection only
  ~40% of the time on receiving and rushing yards — the shipped number sat at
  the mean while books hang lines near the median, so every P(over) leaned
  over. Here the headline number is the **median**, and P(over) comes from a
  fitted quantile function rather than a symmetric Normal.
* **Volume and efficiency mixed.** Opportunity (targets, carries, attempts)
  is sticky and predictable; efficiency (yards per target/carry/attempt) is
  noisy and regresses hard. The model projects them separately:

      team volume   = a + b·team EWMA + c·(expected margin) + d·(implied points)
      player volume = player's EWMA share of team volume × team volume
      stat          = player volume × efficiency shrunk toward position average

  Team volume responds to the game model (favourites run, trailing teams
  throw, high totals mean more snaps), which is how a matchup enters.

Opponent defense-vs-position adjustments were tested (residual method,
opponent-adjusted by construction) and **added nothing out of sample**, so they
are deliberately not applied here — matchup grades remain informational.

Calibration out of sample (2025–26): 80% intervals cover 80–83%, medians split
45–50% (integer stats tie at the median).

Pure pandas — the service layer feeds it weekly frames.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd

MODEL_VERSION = "player-opp-v1"

# Half-lives (in the player's games) of the share EWMAs. Carries turn over
# fastest (RB committees and injuries), targets slower.
SHARE_HALF_LIFE = {"tgt_sh": 4.0, "car_sh": 2.0, "att_sh": 3.0}
TEAM_HALF_LIFE = 6.0
EFF_WINDOW_GAMES = 24
# Pseudo-opportunities of position-average efficiency in each player rate.
EFF_PRIOR = {"ypt": 50.0, "cr": 50.0, "ypc": 80.0, "ypa": 150.0, "comp": 100.0}

# Team volume regressions (OLS, 2016–2025 team-games). spread_team is the
# team's expected margin (+ = favoured); imp_pts its implied points.
TEAM_VOLUME = {
    "t_tgt": {"const": 1.4668, "ewm": 0.6372, "spread_team": -0.2128, "imp_pts": 0.4501},
    "t_car": {"const": 19.6224, "ewm": 0.4934, "spread_team": 0.3015, "imp_pts": -0.2724},
    "t_att": {"const": 3.299, "ewm": 0.594, "spread_team": -0.2251, "imp_pts": 0.4593},
}

QUANTILE_LEVELS = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
# Q_q(stat) = a_q + b_q · structural projection (linear quantile regression,
# fit 2016–2024, validated 2025–26).
QUANTILE_COEFS: dict[str, tuple[tuple[float, float], ...]] = {
    "targets": ((-1.1894, 0.4683), (-1.2226, 0.594), (-0.9342, 0.7036), (-0.8198, 0.8166),
                (-0.5325, 0.8897), (-0.1815, 0.9467), (0.1712, 1.0158), (0.6711, 1.0743),
                (1.4159, 1.1174), (2.552, 1.197), (3.5625, 1.2468)),
    "receptions": ((-0.7203, 0.3329), (-0.8965, 0.5125), (-0.8834, 0.6989), (-0.5426, 0.7642),
                   (-0.3942, 0.8624), (-0.224, 0.9559), (0.1711, 1.0002), (0.4638, 1.097),
                   (1.0045, 1.1647), (1.8361, 1.2702), (2.5924, 1.3571)),
    "receiving_yards": ((-3.3417, 0.1321), (-6.3478, 0.3211), (-8.1187, 0.5391),
                        (-8.1811, 0.7001), (-6.5844, 0.8199), (-4.2428, 0.9257),
                        (-1.3361, 1.0381), (2.3757, 1.1756), (8.6323, 1.3075),
                        (19.8182, 1.4662), (30.8514, 1.5853)),
    "carries": ((-1.6534, 0.4), (-1.8825, 0.5562), (-1.7342, 0.7136), (-1.416, 0.8173),
                (-1.0177, 0.9022), (-0.5453, 0.9731), (0.109, 1.0226), (0.791, 1.0924),
                (1.9253, 1.1566), (4.2813, 1.1923), (6.9026, 1.1638)),
    "rushing_yards": ((-5.6134, 0.2364), (-6.6119, 0.3603), (-7.7222, 0.5557),
                      (-7.2187, 0.6871), (-6.1952, 0.8129), (-4.0687, 0.9228),
                      (-0.5626, 1.0128), (4.2106, 1.1065), (10.8772, 1.2088),
                      (22.9135, 1.3624), (35.6594, 1.4511)),
    "attempts": ((-38.9882, 1.6767), (-29.1006, 1.5073), (-19.9523, 1.3626),
                 (-10.3046, 1.1644), (-2.3545, 0.9957), (4.7623, 0.8445), (11.1619, 0.7235),
                 (15.7528, 0.6591), (20.6988, 0.5934), (26.411, 0.545), (31.7068, 0.4878)),
    "completions": ((-20.7592, 1.4372), (-17.7764, 1.4312), (-12.6452, 1.335),
                    (-8.1408, 1.2206), (-2.8836, 1.0581), (1.5037, 0.9245), (5.0915, 0.8305),
                    (9.0214, 0.7275), (11.9591, 0.6851), (14.2854, 0.7108), (17.9949, 0.6452)),
    "passing_yards": ((-213.2449, 1.316), (-177.471, 1.2951), (-135.5468, 1.2632),
                      (-85.6425, 1.1681), (-20.2413, 0.991), (19.9493, 0.9021),
                      (56.3586, 0.8325), (92.8298, 0.7728), (123.6765, 0.744),
                      (160.0803, 0.7343), (198.1509, 0.7056)),
}
SUPPORTED_STATS = tuple(QUANTILE_COEFS)

# Minimum recent role for the fitted population (outside it, fall back).
_MIN_GAMES = 2
_ROLE_GATES = {"rec": ("tgt_sh_e", 0.04), "rush": ("car_sh_e", 0.08), "qb": ("att_sh_e", 0.40)}
_STAT_GROUP = {
    "targets": "rec", "receptions": "rec", "receiving_yards": "rec",
    "carries": "rush", "rushing_yards": "rush",
    "attempts": "qb", "completions": "qb", "passing_yards": "qb",
}
_POSITIONS = ("QB", "RB", "WR", "TE")
_LEAGUE_DEFAULT = {"ypt": 7.6, "cr": 0.65, "ypc": 4.3, "ypa": 6.9, "comp": 0.64}


# ---- profiles -----------------------------------------------------------------


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0) if col in df.columns \
        else pd.Series(0.0, index=df.index)


def prepare_weekly(frames: Iterable[pd.DataFrame], team_map=lambda x: x) -> pd.DataFrame:
    """Concatenate weekly frames (legacy schema), REG only, skill positions."""
    parts = []
    for f in frames:
        if f is None or len(f) == 0:
            continue
        d = f.copy()
        if "season_type" in d.columns:
            d = d[d["season_type"].astype(str) == "REG"]
        team_col = "recent_team" if "recent_team" in d.columns else "team"
        d["team"] = d[team_col].map(lambda x: team_map(x) if isinstance(x, str) else x)
        d = d[d["position"].isin(_POSITIONS)]
        keep = pd.DataFrame({
            "player_id": d["player_id"], "position": d["position"], "team": d["team"],
            "season": pd.to_numeric(d["season"], errors="coerce"),
            "week": pd.to_numeric(d["week"], errors="coerce"),
        })
        for c in ("targets", "receptions", "receiving_yards", "carries", "rushing_yards",
                  "attempts", "completions", "passing_yards"):
            keep[c] = _num(d, c)
        parts.append(keep)
    if not parts:
        return pd.DataFrame()
    w = pd.concat(parts, ignore_index=True).dropna(subset=["season", "week", "team"])
    w = w.drop_duplicates(["player_id", "season", "week"])
    tot = w.groupby(["team", "season", "week"]).agg(
        t_tgt=("targets", "sum"), t_car=("carries", "sum"), t_att=("attempts", "sum"),
    ).reset_index()
    w = w.merge(tot, on=["team", "season", "week"], how="left")
    w["tgt_sh"] = w["targets"] / w["t_tgt"].clip(lower=1)
    w["car_sh"] = w["carries"] / w["t_car"].clip(lower=1)
    w["att_sh"] = w["attempts"] / w["t_att"].clip(lower=1)
    return w.sort_values(["season", "week"]).reset_index(drop=True)


def league_constants(w: pd.DataFrame, season: int) -> dict[str, dict[str, float]]:
    """Per-position efficiency from the most recent completed season present."""
    out: dict[str, dict[str, float]] = {}
    past = w[w["season"] < season]
    ref = past[past["season"] == past["season"].max()] if len(past) else w
    for pos, g in ref.groupby("position"):
        tg, car, att = g["targets"].sum(), g["carries"].sum(), g["attempts"].sum()
        out[pos] = {
            "ypt": g["receiving_yards"].sum() / tg if tg else _LEAGUE_DEFAULT["ypt"],
            "cr": g["receptions"].sum() / tg if tg else _LEAGUE_DEFAULT["cr"],
            "ypc": g["rushing_yards"].sum() / car if car else _LEAGUE_DEFAULT["ypc"],
            "ypa": g["passing_yards"].sum() / att if att else _LEAGUE_DEFAULT["ypa"],
            "comp": g["completions"].sum() / att if att else _LEAGUE_DEFAULT["comp"],
        }
    return out


def team_volume_ewm(w: pd.DataFrame) -> dict[str, dict[str, float]]:
    """{team: {t_tgt, t_car, t_att}} — EWMA of per-game team volume (latest)."""
    if w is None or len(w) == 0:
        return {}
    t = w.groupby(["team", "season", "week"], as_index=False)[["t_tgt", "t_car", "t_att"]].first()
    t = t.sort_values(["season", "week"])
    out: dict[str, dict[str, float]] = {}
    for team, g in t.groupby("team"):
        out[str(team)] = {
            c: float(g[c].ewm(halflife=TEAM_HALF_LIFE, min_periods=1).mean().iloc[-1])
            for c in ("t_tgt", "t_car", "t_att")
        }
    return out


def player_profiles(w: pd.DataFrame, season: int) -> dict[str, dict[str, Any]]:
    """{player_id: profile} using every game in ``w`` (all strictly in the past)."""
    if w is None or len(w) == 0:
        return {}
    lc = league_constants(w, season)
    out: dict[str, dict[str, Any]] = {}
    for pid, g in w.groupby("player_id", sort=False):
        g = g.sort_values(["season", "week"])
        pos = str(g["position"].iloc[-1])
        last = g.tail(EFF_WINDOW_GAMES)
        lg = lc.get(pos) or _LEAGUE_DEFAULT
        tg, car, att = last["targets"].sum(), last["carries"].sum(), last["attempts"].sum()
        prof = {
            "player_id": pid,
            "position": pos,
            "team": str(g["team"].iloc[-1]),
            "games": int(len(g)),
            "last_season": int(g["season"].iloc[-1]),
            "last_week": int(g["week"].iloc[-1]),
        }
        for c, hl in SHARE_HALF_LIFE.items():
            prof[c + "_e"] = float(g[c].ewm(halflife=hl, min_periods=1).mean().iloc[-1])
        prof["ypt"] = float((last["receiving_yards"].sum() + EFF_PRIOR["ypt"] * lg["ypt"]) / (tg + EFF_PRIOR["ypt"]))
        prof["cr"] = float((last["receptions"].sum() + EFF_PRIOR["cr"] * lg["cr"]) / (tg + EFF_PRIOR["cr"]))
        prof["ypc"] = float((last["rushing_yards"].sum() + EFF_PRIOR["ypc"] * lg["ypc"]) / (car + EFF_PRIOR["ypc"]))
        prof["ypa"] = float((last["passing_yards"].sum() + EFF_PRIOR["ypa"] * lg["ypa"]) / (att + EFF_PRIOR["ypa"]))
        prof["comp"] = float((last["completions"].sum() + EFF_PRIOR["comp"] * lg["comp"]) / (att + EFF_PRIOR["comp"]))
        out[str(pid)] = prof
    return out


# ---- projection ----------------------------------------------------------------


def team_volume(kind: str, ewm: float, spread_team: float, imp_pts: float) -> float:
    c = TEAM_VOLUME[kind]
    return max(0.0, c["const"] + c["ewm"] * ewm + c["spread_team"] * spread_team + c["imp_pts"] * imp_pts)


def eligible(profile: dict[str, Any] | None, stat: str) -> bool:
    if not profile or stat not in QUANTILE_COEFS or profile.get("games", 0) < _MIN_GAMES:
        return False
    key, floor = _ROLE_GATES[_STAT_GROUP[stat]]
    return float(profile.get(key) or 0.0) >= floor


def structural(
    profile: dict[str, Any],
    stat: str,
    team_ewm: dict[str, float],
    *,
    imp_pts: float,
    spread_team: float,
) -> float:
    """Opportunity × efficiency point projection (the quantile regressor input)."""
    grp = _STAT_GROUP[stat]
    if grp == "rec":
        vol = profile["tgt_sh_e"] * team_volume("t_tgt", team_ewm.get("t_tgt", 34.0), spread_team, imp_pts)
        return {"targets": vol, "receptions": vol * profile["cr"],
                "receiving_yards": vol * profile["ypt"]}[stat]
    if grp == "rush":
        vol = profile["car_sh_e"] * team_volume("t_car", team_ewm.get("t_car", 26.0), spread_team, imp_pts)
        return {"carries": vol, "rushing_yards": vol * profile["ypc"]}[stat]
    vol = profile["att_sh_e"] * team_volume("t_att", team_ewm.get("t_att", 33.0), spread_team, imp_pts)
    return {"attempts": vol, "completions": vol * profile["comp"],
            "passing_yards": vol * profile["ypa"]}[stat]


def quantiles(stat: str, hat: float, scale: float = 1.0) -> dict[float, float]:
    """Monotone quantile function at the fitted levels, floored at 0.

    ``scale`` multiplies the whole distribution (weather, injury, role).
    """
    vals = sorted(max(0.0, a + b * hat) for a, b in QUANTILE_COEFS[stat])
    return {q: v * scale for q, v in zip(QUANTILE_LEVELS, vals)}


def mean_from_quantiles(q: dict[float, float]) -> float:
    """Trapezoid estimate of E[X] from the quantile grid (tails held flat)."""
    levels = [0.0, *QUANTILE_LEVELS, 1.0]
    vals = [q[QUANTILE_LEVELS[0]], *(q[lvl] for lvl in QUANTILE_LEVELS), q[QUANTILE_LEVELS[-1]]]
    # Extend tails linearly so the mean isn't pulled in by flat ends.
    lo_slope = (vals[2] - vals[1]) / (levels[2] - levels[1])
    hi_slope = (vals[-2] - vals[-3]) / (levels[-2] - levels[-3])
    vals[0] = max(0.0, vals[1] - lo_slope * levels[1])
    vals[-1] = vals[-2] + hi_slope * (1.0 - levels[-2])
    return float(sum((levels[i + 1] - levels[i]) * (vals[i] + vals[i + 1]) / 2 for i in range(len(levels) - 1)))


def over_prob(q: dict[Any, float], line: float) -> float:
    """P(X > line) by linear interpolation of the CDF through the quantile grid."""
    pts = sorted((float(v), float(k)) for k, v in q.items())
    xs = [p[0] for p in pts]
    ps = [p[1] for p in pts]
    if line < xs[0]:
        # Below the 5th percentile: shrink toward 1 linearly to x=0.
        if xs[0] <= 0:
            return 1.0 - ps[0]
        return 1.0 - ps[0] * max(0.0, line) / xs[0]
    if line >= xs[-1]:
        top = 1.0 - ps[-1]
        span = max(xs[-1] - xs[-2], 1e-6) * 2.0
        return max(0.0, top * (1.0 - (line - xs[-1]) / span))
    for i in range(len(xs) - 1):
        if xs[i] <= line < xs[i + 1]:
            # Ties (flat segments at 0) resolve to the upper level.
            frac = 0.0 if xs[i + 1] == xs[i] else (line - xs[i]) / (xs[i + 1] - xs[i])
            return 1.0 - (ps[i] + frac * (ps[i + 1] - ps[i]))
    return 0.0


def project(
    profile: dict[str, Any],
    stat: str,
    team_ewm: dict[str, float],
    *,
    imp_pts: float,
    spread_team: float,
    scale: float = 1.0,
) -> dict[str, Any] | None:
    """Full projection for one stat/game, or None if outside the fitted role."""
    if not eligible(profile, stat):
        return None
    hat = structural(profile, stat, team_ewm, imp_pts=imp_pts, spread_team=spread_team)
    if not math.isfinite(hat):
        return None
    q = quantiles(stat, hat, scale)
    mean = mean_from_quantiles(q)
    return {
        "hat": hat,
        "median": q[0.5],
        "mean": mean,
        "quantiles": q,
        "sd": max(1e-6, (q[0.9] - q[0.1]) / 2.5631),
    }
