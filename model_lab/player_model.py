#!/usr/bin/env python3
"""Standalone player-projection model — exact port of the nfl-app backend.

Mirrors: app/services/player_projection_engine.py (pure math) plus the
weather/injury multipliers from player_predictions_service.py.
Pure Python (stdlib only). Run `python player_model.py` for a full demo:
prior -> Bayesian update -> game coupling -> props/anytime-TD -> season bands.

Imports norm_cdf/norm_ppf/predict_game from game_model.py (same folder).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from game_model import norm_cdf, norm_ppf, predict_game

MODEL_VERSION = "player-proj-v2 (standalone)"

# ---- Prior construction (production values) ----------------------------------

PRIOR_SEASON_WEIGHTS: tuple[float, ...] = (1.0, 0.55, 0.30)

PRIOR_EFFECTIVE_GAMES = {"volume": 5.0, "yardage": 8.0, "scoring": 12.0}

STAT_CLASS = {
    "attempts": "volume", "completions": "volume", "carries": "volume",
    "targets": "volume", "receptions": "volume",
    "passing_yards": "yardage", "rushing_yards": "yardage",
    "receiving_yards": "yardage", "fantasy_points_ppr": "yardage",
    "passing_tds": "scoring", "rushing_tds": "scoring",
    "receiving_tds": "scoring", "interceptions": "scoring",
}

TD_STATS = {"passing_tds", "rushing_tds", "receiving_tds"}

ROOKIE_ARCHETYPES = {
    "QB": {
        "day1": {"attempts": (30.0, 7.0), "completions": (19.0, 5.0),
                 "passing_yards": (205.0, 65.0), "passing_tds": (1.1, 1.0),
                 "interceptions": (0.8, 0.9), "carries": (4.5, 2.5),
                 "rushing_yards": (20.0, 18.0), "rushing_tds": (0.15, 0.4),
                 "fantasy_points_ppr": (14.0, 7.0)},
        "day2": {"attempts": (26.0, 8.0), "completions": (16.0, 5.5),
                 "passing_yards": (170.0, 65.0), "passing_tds": (0.9, 0.9),
                 "interceptions": (0.8, 0.9), "carries": (3.5, 2.5),
                 "rushing_yards": (14.0, 15.0), "rushing_tds": (0.1, 0.3),
                 "fantasy_points_ppr": (11.0, 6.5)},
        "day3": {"attempts": (20.0, 9.0), "completions": (12.0, 6.0),
                 "passing_yards": (130.0, 60.0), "passing_tds": (0.6, 0.8),
                 "interceptions": (0.7, 0.8), "carries": (2.5, 2.0),
                 "rushing_yards": (9.0, 12.0), "rushing_tds": (0.05, 0.25),
                 "fantasy_points_ppr": (8.0, 6.0)},
    },
    "RB": {
        "day1": {"carries": (13.0, 5.0), "rushing_yards": (55.0, 30.0),
                 "rushing_tds": (0.40, 0.6), "targets": (3.2, 2.0),
                 "receptions": (2.5, 1.7), "receiving_yards": (18.0, 16.0),
                 "receiving_tds": (0.08, 0.28), "fantasy_points_ppr": (11.5, 6.0)},
        "day2": {"carries": (9.5, 5.0), "rushing_yards": (40.0, 26.0),
                 "rushing_tds": (0.28, 0.5), "targets": (2.4, 1.8),
                 "receptions": (1.9, 1.5), "receiving_yards": (13.0, 14.0),
                 "receiving_tds": (0.06, 0.24), "fantasy_points_ppr": (8.5, 5.5)},
        "day3": {"carries": (5.5, 4.5), "rushing_yards": (23.0, 22.0),
                 "rushing_tds": (0.15, 0.4), "targets": (1.4, 1.4),
                 "receptions": (1.1, 1.2), "receiving_yards": (8.0, 11.0),
                 "receiving_tds": (0.04, 0.2), "fantasy_points_ppr": (5.0, 4.5)},
    },
    "WR": {
        "day1": {"targets": (6.5, 2.8), "receptions": (4.2, 2.1),
                 "receiving_yards": (52.0, 32.0), "receiving_tds": (0.32, 0.55),
                 "fantasy_points_ppr": (11.0, 6.5)},
        "day2": {"targets": (4.8, 2.6), "receptions": (3.0, 1.9),
                 "receiving_yards": (37.0, 28.0), "receiving_tds": (0.22, 0.45),
                 "fantasy_points_ppr": (8.0, 5.5)},
        "day3": {"targets": (2.8, 2.2), "receptions": (1.8, 1.6),
                 "receiving_yards": (21.0, 22.0), "receiving_tds": (0.12, 0.35),
                 "fantasy_points_ppr": (4.8, 4.5)},
    },
    "TE": {
        "day1": {"targets": (4.6, 2.4), "receptions": (3.2, 1.8),
                 "receiving_yards": (34.0, 24.0), "receiving_tds": (0.25, 0.5),
                 "fantasy_points_ppr": (7.8, 5.0)},
        "day2": {"targets": (3.2, 2.2), "receptions": (2.2, 1.6),
                 "receiving_yards": (23.0, 20.0), "receiving_tds": (0.16, 0.4),
                 "fantasy_points_ppr": (5.5, 4.5)},
        "day3": {"targets": (1.8, 1.7), "receptions": (1.2, 1.2),
                 "receiving_yards": (12.0, 14.0), "receiving_tds": (0.08, 0.28),
                 "fantasy_points_ppr": (3.2, 3.5)},
    },
}

ROLE_MULTIPLIERS = {
    "QB": {1: 1.0, 2: 0.05, 3: 0.02},
    "RB": {1: 1.0, 2: 0.85, 3: 0.45, 4: 0.15},
    "WR": {1: 1.0, 2: 1.0, 3: 0.85, 4: 0.50, 5: 0.20},
    "TE": {1: 1.0, 2: 0.60, 3: 0.25},
}
_ROLE_FLOOR = {"QB": 0.02, "RB": 0.10, "WR": 0.12, "TE": 0.15}
ROLE_LEADERBOARD_MIN = 0.30

POSITION_SHRINK_K = 3.0


def role_multiplier(position: str, depth_order: int | None) -> float:
    if depth_order is None:
        return 1.0
    pos = (position or "").upper()
    table = ROLE_MULTIPLIERS.get(pos)
    if not table:
        return 1.0
    order = max(1, int(depth_order))
    return table.get(order, _ROLE_FLOOR.get(pos, 0.15))


def age_multiplier(position: str, age: int | None) -> float:
    if age is None:
        return 1.0
    pos = (position or "").upper()
    peak_start, peak_end, decline = {
        "RB": (23, 26, 0.05), "WR": (24, 28, 0.035),
        "TE": (25, 29, 0.03), "QB": (26, 34, 0.02),
    }.get(pos, (24, 28, 0.03))
    if age < peak_start:
        mult = 1.0 - 0.03 * (peak_start - age)
    elif age <= peak_end:
        mult = 1.0
    else:
        mult = 1.0 - decline * (age - peak_end)
    return max(0.72, min(1.10, mult))


@dataclass(frozen=True)
class StatPosterior:
    mean: float
    game_sd: float
    talent_sd: float
    prior_n: float
    obs_n: float


def scale_posterior(post: StatPosterior, scale: float) -> StatPosterior:
    s = max(0.0, scale)
    return StatPosterior(mean=post.mean * s, game_sd=max(post.game_sd * s, 1e-6),
                         talent_sd=post.talent_sd * s,
                         prior_n=post.prior_n, obs_n=post.obs_n)


def build_prior(stat: str, seasons: list[dict], *, position: str = "",
                age: int | None = None,
                position_mean: float | None = None) -> tuple[float, float, float] | None:
    """seasons most-recent-first: [{"mean":…, "sd":…, "games":…}, …]
    -> (mean, game_sd, prior_n)."""
    pairs = [(w, s) for w, s in zip(PRIOR_SEASON_WEIGHTS, seasons)
             if s and s.get("games", 0) > 0]
    if not pairs:
        return None
    wsum = sum(w * min(float(s["games"]), 17.0) for w, s in pairs)
    if wsum <= 0:
        return None
    mean = sum(w * min(float(s["games"]), 17.0) * float(s["mean"]) for w, s in pairs) / wsum

    sd_within = sum(w * min(float(s["games"]), 17.0) * float(s.get("sd") or 0.0)
                    for w, s in pairs) / wsum
    if len(pairs) > 1:
        means = [float(s["mean"]) for _, s in pairs]
        mu = sum(means) / len(means)
        sd_between = math.sqrt(sum((m - mu) ** 2 for m in means) / len(means))
    else:
        sd_between = 0.0
    game_sd = math.sqrt(sd_within ** 2 + 0.5 * sd_between ** 2)

    mean *= age_multiplier(position, age)

    total_games = sum(min(float(s["games"]), 17.0) for _, s in pairs)
    if position_mean is not None:
        mean = (total_games * mean + POSITION_SHRINK_K * position_mean) / (
            total_games + POSITION_SHRINK_K)

    n0 = PRIOR_EFFECTIVE_GAMES.get(STAT_CLASS.get(stat, "yardage"), 8.0)
    n0 *= min(1.0, total_games / 12.0)
    return mean, game_sd, max(1.0, n0)


def rookie_prior(stat: str, position: str, tier: str = "day2"):
    arch = ROOKIE_ARCHETYPES.get((position or "").upper(), {})
    kit = arch.get(tier) or arch.get("day2")
    if not kit or stat not in kit:
        return None
    mean, sd = kit[stat]
    return mean, sd, 3.0


def bayesian_update(prior_mean: float, prior_game_sd: float, prior_n: float,
                    obs_mean: float | None, obs_game_sd: float | None,
                    obs_n: float) -> StatPosterior:
    n0 = max(0.5, prior_n)
    n = max(0.0, obs_n)
    if obs_mean is None or n <= 0:
        post_mean, post_sd = prior_mean, prior_game_sd
    else:
        post_mean = (n0 * prior_mean + n * obs_mean) / (n0 + n)
        o_sd = obs_game_sd if obs_game_sd is not None else prior_game_sd
        post_sd = math.sqrt((n0 * prior_game_sd ** 2 + n * o_sd ** 2) / (n0 + n))
    post_sd = max(post_sd, 0.35 * math.sqrt(max(post_mean, 0.0)), 1e-6)
    talent_sd = post_sd / math.sqrt(n0 + n)
    return StatPosterior(mean=max(0.0, post_mean), game_sd=post_sd,
                         talent_sd=talent_sd, prior_n=n0, obs_n=n)


# ---- Game coupling -----------------------------------------------------------

LEAGUE_AVG_TEAM_POINTS = 22.0
_SCORING_ELASTICITY = {"volume": 0.30, "yardage": 0.55, "scoring": 1.0}
_SCRIPT_PASS_PER_PT = -0.008
_SCRIPT_RUSH_PER_PT = 0.012
_SCRIPT_CAP = 0.12

_PASS_STATS = {"attempts", "completions", "passing_yards", "passing_tds", "interceptions"}
_RUSH_STATS = {"carries", "rushing_yards", "rushing_tds"}
_RECV_STATS = {"targets", "receptions", "receiving_yards", "receiving_tds"}


def game_environment_multiplier(stat: str, *, team_expected_pts: float,
                                opp_expected_pts: float,
                                defense_factor: float = 1.0) -> float:
    env = 1.0
    ratio = max(0.60, min(1.45, team_expected_pts / LEAGUE_AVG_TEAM_POINTS))
    elast = _SCORING_ELASTICITY.get(STAT_CLASS.get(stat, "yardage"), 0.5)
    env *= ratio ** elast

    margin = team_expected_pts - opp_expected_pts
    if stat in _PASS_STATS or stat in _RECV_STATS:
        tilt = _SCRIPT_PASS_PER_PT * margin
    elif stat in _RUSH_STATS:
        tilt = _SCRIPT_RUSH_PER_PT * margin
    else:
        tilt = 0.0
    env *= 1.0 + max(-_SCRIPT_CAP, min(_SCRIPT_CAP, tilt))

    env *= max(0.75, min(1.30, defense_factor))
    return max(0.75, min(1.30, env))


# ---- Weather / injury multipliers (player_predictions_service.py) ------------

def weather_multiplier(weather: dict | None, stat: str) -> float:
    if not weather or not weather.get("available") or weather.get("is_indoor"):
        return 1.0
    wind = float(weather.get("wind_mph") or 0)
    precip = float(weather.get("precipitation_in") or 0)
    temp = weather.get("temperature_f")
    temp = float(temp) if temp is not None else 65.0
    mult = 1.0
    if stat in _PASS_STATS:
        if wind >= 25: mult *= 0.85
        elif wind >= 15: mult *= 0.92
        if precip >= 0.4: mult *= 0.85
        elif precip >= 0.15: mult *= 0.93
        if temp <= 25: mult *= 0.95
    elif stat in _RUSH_STATS:
        if wind >= 20 or precip >= 0.4: mult *= 1.04
    elif stat in _RECV_STATS:
        if wind >= 25: mult *= 0.88
        elif wind >= 15: mult *= 0.94
        if precip >= 0.4: mult *= 0.88
        elif precip >= 0.15: mult *= 0.95
    return mult


def injury_multiplier(injury_status: str | None) -> float:
    if not injury_status:
        return 1.0
    s = injury_status.strip().upper()
    if s in ("OUT", "IR", "PUP", "NFI", "SUSPENDED"): return 0.0
    if s == "DOUBTFUL": return 0.3
    if s == "QUESTIONABLE": return 0.85
    return 1.0


# ---- Market anchor (next game, volume/yardage only, >=2 books) ---------------

_ANCHOR_WEIGHT_PER_BOOK = 0.12
_ANCHOR_WEIGHT_CAP = 0.40


def apply_market_anchor(mean: float, line: float, books: int) -> tuple[float, float]:
    """Returns (anchored_mean, weight)."""
    k = min(_ANCHOR_WEIGHT_CAP, _ANCHOR_WEIGHT_PER_BOOK * books)
    return mean + k * (line - mean), k


# ---- Distribution outputs -----------------------------------------------------

def stat_over_prob(mean: float, sd: float, line: float) -> float:
    """P(stat > line) for Normal(mean, sd) truncated at 0."""
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    if line < 0:
        return 1.0
    p_above_line = 1.0 - norm_cdf((line - mean) / sd)
    p_above_zero = 1.0 - norm_cdf((0.0 - mean) / sd)
    if p_above_zero <= 1e-12:
        return 0.0
    return min(1.0, p_above_line / p_above_zero)


def stat_interval(mean: float, sd: float, level: float = 0.8) -> tuple[float, float]:
    z = norm_ppf(0.5 + level / 2.0)
    return max(0.0, mean - z * sd), max(0.0, mean + z * sd)


def stat_quantile(mean: float, sd: float, q: float) -> float:
    return max(0.0, mean + norm_ppf(q) * sd)


def anytime_td_prob(expected_tds: float) -> float:
    return 1.0 - math.exp(-max(0.0, expected_tds))


# ---- Season aggregation --------------------------------------------------------

def aggregate_season(per_game_means: list[float], game_sd: float,
                     talent_sd: float) -> dict:
    g = len(per_game_means)
    total = sum(per_game_means)
    if g == 0:
        return {"mean": 0.0, "sd": 0.0, "games": 0}
    sd = math.sqrt((g ** 2) * (talent_sd ** 2) + g * (game_sd ** 2))
    return {"mean": total, "sd": sd, "games": g}


SEASON_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def season_quantiles(mean: float, sd: float) -> dict:
    return {f"p{int(q*100)}": stat_quantile(mean, sd, q) for q in SEASON_QUANTILES}


# ---- Fantasy scoring ------------------------------------------------------------

SCORING_FORMATS = ("ppr", "half_ppr", "standard")


def fantasy_points(stats: dict[str, float], scoring: str = "ppr") -> float:
    rec_bonus = {"ppr": 1.0, "half_ppr": 0.5, "standard": 0.0}.get(scoring, 1.0)
    return (0.04 * stats.get("passing_yards", 0.0)
            + 4.0 * stats.get("passing_tds", 0.0)
            - 2.0 * stats.get("interceptions", 0.0)
            + 0.1 * stats.get("rushing_yards", 0.0)
            + 6.0 * stats.get("rushing_tds", 0.0)
            + 0.1 * stats.get("receiving_yards", 0.0)
            + 6.0 * stats.get("receiving_tds", 0.0)
            + rec_bonus * stats.get("receptions", 0.0))


def fantasy_sd(stat_sds: dict[str, float], scoring: str = "ppr") -> float:
    rec_bonus = {"ppr": 1.0, "half_ppr": 0.5, "standard": 0.0}.get(scoring, 1.0)
    weights = {"passing_yards": 0.04, "passing_tds": 4.0, "interceptions": 2.0,
               "rushing_yards": 0.1, "rushing_tds": 6.0,
               "receiving_yards": 0.1, "receiving_tds": 6.0, "receptions": rec_bonus}
    return math.sqrt(sum((w * stat_sds.get(k, 0.0)) ** 2 for k, w in weights.items()))


# ---- End-to-end helper -----------------------------------------------------------

def project_stat_for_game(post: StatPosterior, stat: str, *,
                          team_expected_pts: float, opp_expected_pts: float,
                          defense_factor: float = 1.0, weather: dict | None = None,
                          injury_status: str | None = None,
                          market_line: float | None = None,
                          market_books: int = 0) -> dict:
    """One stat x one game -> the same fields the API ships."""
    env_mult = game_environment_multiplier(
        stat, team_expected_pts=team_expected_pts,
        opp_expected_pts=opp_expected_pts, defense_factor=defense_factor)
    w_mult = weather_multiplier(weather, stat)
    inj_mult = injury_multiplier(injury_status)
    mean = post.mean * env_mult * w_mult * inj_mult
    sd = post.game_sd

    anchor = None
    if market_line is not None and market_books >= 2 and mean > 0 \
            and STAT_CLASS.get(stat) in ("volume", "yardage"):
        raw = mean
        mean, k = apply_market_anchor(mean, market_line, market_books)
        anchor = {"line": market_line, "books": market_books,
                  "weight": round(k, 2), "raw_mean": round(raw, 2)}

    lo50, hi50 = stat_interval(mean, sd, 0.50)
    lo80, hi80 = stat_interval(mean, sd, 0.80)
    out = {"mean": round(mean, 2), "sd": round(sd, 2),
           "predicted": round(mean, 1), "low": round(lo50, 1), "high": round(hi50, 1),
           "interval_80": [round(lo80, 1), round(hi80, 1)],
           "env_multiplier": round(env_mult, 3),
           "weather_multiplier": round(w_mult, 3),
           "injury_multiplier": round(inj_mult, 2)}
    if anchor:
        out["market_anchor"] = anchor
    if stat in TD_STATS:
        out["anytime_prob"] = round(anytime_td_prob(mean), 3)
    return out


# ---- Demo -------------------------------------------------------------------------

def _demo():
    print("=" * 70)
    print("PLAYER MODEL DEMO — veteran WR1, week 5, favorable matchup")
    print("=" * 70)

    # 1. Prior: three seasons of receiving yards (most recent first)
    seasons = [
        {"mean": 78.0, "sd": 31.0, "games": 17},   # last season
        {"mean": 71.0, "sd": 29.0, "games": 16},
        {"mean": 62.0, "sd": 27.0, "games": 15},
    ]
    prior = build_prior("receiving_yards", seasons, position="WR", age=26,
                        position_mean=48.0)
    p_mean, p_sd, p_n = prior
    print(f"\n-- prior: mean {p_mean:.1f} yds, game_sd {p_sd:.1f}, "
          f"worth {p_n:.1f} pseudo-games")

    # 2. Bayesian update with 4 observed games this season (hot start)
    post = bayesian_update(p_mean, p_sd, p_n, obs_mean=92.0, obs_game_sd=35.0, obs_n=4.0)
    print(f"-- posterior after 4 games at 92 y/g: mean {post.mean:.1f}, "
          f"game_sd {post.game_sd:.1f}, talent_sd {post.talent_sd:.1f}")

    # 3. Game env from the GAME model: his team implied 27.1, opponent 20.3
    g = predict_game(1620, 1480, home_off_ppg=26.0, away_off_ppg=20.0,
                     home_def_ppg_allowed=19.0, away_def_ppg_allowed=24.5)
    print(f"-- game model: implied {g['predicted_home_score']} - "
          f"{g['predicted_away_score']}, script '{g['game_script']}'")

    # 4. Full per-game projection: leaky pass defense (1.12), light wind, healthy,
    #    market line 74.5 across 5 books
    proj = project_stat_for_game(
        post, "receiving_yards",
        team_expected_pts=g["predicted_home_score"],
        opp_expected_pts=g["predicted_away_score"],
        defense_factor=1.12,
        weather={"available": True, "is_indoor": False, "wind_mph": 8,
                 "precipitation_in": 0.0, "temperature_f": 58},
        injury_status=None, market_line=74.5, market_books=5)
    print(f"-- projection: {proj['predicted']} yds  "
          f"(50%: {proj['low']}-{proj['high']}, 80%: {proj['interval_80']})")
    print(f"   env x{proj['env_multiplier']}, anchor {proj.get('market_anchor')}")

    # 5. Prop probability off the SAME distribution
    over = stat_over_prob(proj["mean"], proj["sd"], 74.5)
    print(f"-- P(over 74.5 yds): {over:.3f}   P(under): {1-over:.3f}")

    # 6. Anytime TD: posterior 0.45 rec TD + 0.03 rush TD per game
    lam = 0.45 + 0.03
    print(f"-- anytime TD (lambda={lam}): {anytime_td_prob(lam):.3f}")

    # 7. Season aggregation: 13 remaining games, per-game means vary by matchup
    game_means = [post.mean * m for m in
                  (1.05, 0.97, 1.10, 0.92, 1.00, 1.03, 0.95, 1.08, 0.99, 1.01, 0.94, 1.06, 1.00)]
    agg = aggregate_season(game_means, post.game_sd, post.talent_sd)
    qs = season_quantiles(agg["mean"], agg["sd"])
    print(f"-- season (remaining {agg['games']} games): {agg['mean']:.0f} yds "
          f"± {agg['sd']:.0f}")
    print("   quantiles: " + ", ".join(f"{k}={v:.0f}" for k, v in qs.items()))

    # 8. Fantasy
    means = {"receiving_yards": agg["mean"], "receptions": 5.6 * 13,
             "receiving_tds": 0.45 * 13}
    sds = {"receiving_yards": agg["sd"], "receptions": 8.0, "receiving_tds": 2.2}
    for fmt in SCORING_FORMATS:
        print(f"-- fantasy {fmt}: {fantasy_points(means, fmt):.1f} "
              f"± {fantasy_sd(sds, fmt):.1f}")

    # 9. Role scaling: same player as a WR4
    backup = scale_posterior(post, role_multiplier("WR", 4))
    print(f"-- as WR4 (x0.50): mean {backup.mean:.1f}, game_sd {backup.game_sd:.1f}")

    # 10. Rookie archetype
    rk = rookie_prior("receiving_yards", "WR", "day1")
    print(f"-- day-1 rookie WR prior: mean {rk[0]}, sd {rk[1]}, n0 {rk[2]}")

    # Sanity checks
    assert 0.0 <= over <= 1.0
    even = stat_over_prob(50.0, 20.0, 50.0)
    assert 0.49 < even < 0.53, even  # slightly >0.5 due to zero-truncation
    assert bayesian_update(10, 5, 8, None, None, 0).mean == 10.0
    z = bayesian_update(10, 5, 8, 20, 5, 8)
    assert abs(z.mean - 15.0) < 1e-9  # equal weight -> midpoint
    assert aggregate_season([10.0]*4, 2.0, 1.0)["sd"] == math.sqrt(16*1 + 4*4)
    print("\nsanity checks OK — demo complete.")


if __name__ == "__main__":
    _demo()
    sys.exit(0)
