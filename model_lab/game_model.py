#!/usr/bin/env python3
"""Standalone game-projection model — exact port of the nfl-app backend.

Mirrors: app/services/elo_service.py, prediction_dist.py, predictions_service.py
Pure Python (stdlib only). Run `python game_model.py` for a demo, or feed a
schedule CSV to rebuild Elo and simulate a season:

    python game_model.py --csv schedule.csv --season-sims 10000

CSV columns: week,home_team,away_team,home_score,away_score[,neutral]
(leave scores blank for unplayed games; team ids like KC, BUF, ...)
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from collections import defaultdict

# ---- Constants (production values) -----------------------------------------

K_FACTOR = 20.0
HOME_FIELD_ADVANTAGE = 55.0      # Elo points (~ +2.2 spread points)
SEASON_REGRESSION = 0.75
INITIAL_RATING = 1500.0
ELO_PER_POINT = 25.0

NFL_MARGIN_SIGMA = 13.5          # SD of final margin around expectation
NFL_TOTAL_SIGMA = 10.0           # SD of total points around expectation
LEAGUE_AVG_POINTS_PER_TEAM = 22.0
RATING_SIGMA_ELO = 55.0          # season-long latent-strength draw (Monte Carlo)

KEY_NUMBER_PUSH = {3: 0.094, 7: 0.058, 6: 0.037, 10: 0.034, 4: 0.034,
                   14: 0.025, 1: 0.027, 17: 0.013, 8: 0.024, 13: 0.012}

DIVISIONS = {
    "BUF": ("AFC", "East"), "MIA": ("AFC", "East"), "NE": ("AFC", "East"), "NYJ": ("AFC", "East"),
    "BAL": ("AFC", "North"), "CIN": ("AFC", "North"), "CLE": ("AFC", "North"), "PIT": ("AFC", "North"),
    "HOU": ("AFC", "South"), "IND": ("AFC", "South"), "JAX": ("AFC", "South"), "TEN": ("AFC", "South"),
    "DEN": ("AFC", "West"), "KC": ("AFC", "West"), "LV": ("AFC", "West"), "LAC": ("AFC", "West"),
    "DAL": ("NFC", "East"), "NYG": ("NFC", "East"), "PHI": ("NFC", "East"), "WAS": ("NFC", "East"),
    "CHI": ("NFC", "North"), "DET": ("NFC", "North"), "GB": ("NFC", "North"), "MIN": ("NFC", "North"),
    "ATL": ("NFC", "South"), "CAR": ("NFC", "South"), "NO": ("NFC", "South"), "TB": ("NFC", "South"),
    "ARI": ("NFC", "West"), "LA": ("NFC", "West"), "SF": ("NFC", "West"), "SEA": ("NFC", "West"),
}

_SQRT2 = math.sqrt(2.0)
_SQRT_PI = math.sqrt(math.pi)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ---- Normal primitives (prediction_dist.py) ---------------------------------

def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def norm_pdf(z: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * z * z)


def norm_ppf(p: float) -> float:
    """Acklam's rational approximation for the inverse normal CDF."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


def win_prob(expected_margin: float, sigma: float = NFL_MARGIN_SIGMA) -> float:
    if sigma <= 0:
        return 1.0 if expected_margin > 0 else (0.0 if expected_margin < 0 else 0.5)
    return norm_cdf(expected_margin / sigma)


def cover_prob_home(expected_margin: float, home_line: float,
                    sigma: float = NFL_MARGIN_SIGMA) -> float:
    """P(home covers). Sportsbook convention: negative line = home favored."""
    return norm_cdf((expected_margin + home_line) / sigma)


def over_prob(expected_total: float, line: float, sigma: float = NFL_TOTAL_SIGMA) -> float:
    return norm_cdf((expected_total - line) / sigma)


def push_prob(home_line: float) -> float:
    if abs(home_line - round(home_line)) > 1e-9:
        return 0.0
    return KEY_NUMBER_PUSH.get(abs(int(round(home_line))), 0.0)


def margin_interval(expected_margin: float, sigma: float = NFL_MARGIN_SIGMA,
                    level: float = 0.8) -> tuple[float, float]:
    z = norm_ppf(0.5 + level / 2.0)
    return expected_margin - z * sigma, expected_margin + z * sigma


# ---- Scoring rules (backtest metrics) ---------------------------------------

def crps_normal(expected: float, sigma: float, actual: float) -> float:
    if sigma <= 0:
        return abs(actual - expected)
    z = (actual - expected) / sigma
    return sigma * (z * (2.0 * norm_cdf(z) - 1.0) + 2.0 * norm_pdf(z) - 1.0 / _SQRT_PI)


def log_loss(prob: float, outcome: int, eps: float = 1e-12) -> float:
    p = min(max(prob, eps), 1.0 - eps)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))


def brier(prob: float, outcome: int) -> float:
    return (prob - outcome) ** 2


# ---- Elo engine (elo_service.py) --------------------------------------------

def elo_win_probability(home: float, away: float, neutral: bool = False) -> float:
    diff = home - away + (0 if neutral else HOME_FIELD_ADVANTAGE)
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def predicted_spread(home: float, away: float, neutral: bool = False) -> float:
    """Negative = home favored (sportsbook convention)."""
    diff = home - away + (0 if neutral else HOME_FIELD_ADVANTAGE)
    return -(diff / ELO_PER_POINT)


def _mov_multiplier(margin: int, elo_diff: float) -> float:
    return math.log(max(abs(margin), 1) + 1) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))


def update_rating(home: float, away: float, home_margin: int,
                  neutral: bool = False) -> tuple[float, float]:
    expected_home = elo_win_probability(home, away, neutral)
    actual_home = 1.0 if home_margin > 0 else (0.0 if home_margin < 0 else 0.5)
    diff_for_mov = home - away + (0 if neutral else HOME_FIELD_ADVANTAGE)
    if home_margin < 0:
        diff_for_mov = -diff_for_mov
    mov = _mov_multiplier(home_margin, diff_for_mov)
    delta = K_FACTOR * mov * (actual_home - expected_home)
    return home + delta, away - delta


def regress_for_new_season(ratings: dict[str, float]) -> dict[str, float]:
    return {t: SEASON_REGRESSION * r + (1 - SEASON_REGRESSION) * INITIAL_RATING
            for t, r in ratings.items()}


def build_elo_from_games(games: list[dict], ratings: dict[str, float] | None = None,
                         ) -> dict[str, float]:
    """Walk played games in (week) order, updating ratings. `games` rows:
    {week, home_team, away_team, home_score, away_score, neutral?}."""
    ratings = dict(ratings or {})
    played = [g for g in games if g.get("home_score") is not None
              and g.get("away_score") is not None]
    played.sort(key=lambda g: g.get("week", 0))
    for g in played:
        h, a = g["home_team"], g["away_team"]
        rh = ratings.get(h, INITIAL_RATING)
        ra = ratings.get(a, INITIAL_RATING)
        nh, na = update_rating(rh, ra, int(g["home_score"]) - int(g["away_score"]),
                               neutral=bool(g.get("neutral")))
        ratings[h], ratings[a] = nh, na
    return ratings


# ---- Single-game predictor (predictions_service.predict_game) ----------------

def predict_game(home_rating: float, away_rating: float,
                 home_off_ppg: float | None = None, away_off_ppg: float | None = None,
                 home_def_ppg_allowed: float | None = None,
                 away_def_ppg_allowed: float | None = None,
                 neutral_site: bool = False) -> dict:
    """Exact port: Elo margin + matchup-adjusted total, win prob from N(margin, 13.5)."""
    spread = predicted_spread(home_rating, away_rating, neutral_site)
    expected_margin = -spread
    win_p = win_prob(expected_margin, NFL_MARGIN_SIGMA)

    h_off = home_off_ppg if home_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    a_off = away_off_ppg if away_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    h_def = home_def_ppg_allowed if home_def_ppg_allowed is not None else LEAGUE_AVG_POINTS_PER_TEAM
    a_def = away_def_ppg_allowed if away_def_ppg_allowed is not None else LEAGUE_AVG_POINTS_PER_TEAM

    expected_home_pts = h_off + (a_def - LEAGUE_AVG_POINTS_PER_TEAM)
    expected_away_pts = a_off + (h_def - LEAGUE_AVG_POINTS_PER_TEAM)
    total = expected_home_pts + expected_away_pts

    predicted_home_score = (total + expected_margin) / 2
    predicted_away_score = (total - expected_margin) / 2

    m_lo80, m_hi80 = margin_interval(expected_margin, NFL_MARGIN_SIGMA, 0.80)
    m_lo50, m_hi50 = margin_interval(expected_margin, NFL_MARGIN_SIGMA, 0.50)

    abs_spread = abs(spread)
    if total >= 48:
        script = "Shootout"
    elif total <= 40:
        script = "Defensive grind"
    elif abs_spread >= 7:
        script = "Blowout potential"
    elif abs_spread <= 2.5:
        script = "Toss-up"
    else:
        script = "Methodical"

    return {
        "home_win_prob": round(win_p, 3),
        "away_win_prob": round(1 - win_p, 3),
        "predicted_spread": round(spread, 1),
        "predicted_total": round(total, 1),
        "predicted_home_score": round(predicted_home_score, 1),
        "predicted_away_score": round(predicted_away_score, 1),
        "game_script": script,
        "margin_sd": NFL_MARGIN_SIGMA,
        "distribution": {
            "expected_margin": round(expected_margin, 1),
            "margin_interval_50": [round(m_lo50, 1), round(m_hi50, 1)],
            "margin_interval_80": [round(m_lo80, 1), round(m_hi80, 1)],
            "home_score_range_80": [round((total + m_lo80) / 2, 1), round((total + m_hi80) / 2, 1)],
            "away_score_range_80": [round((total - m_hi80) / 2, 1), round((total - m_lo80) / 2, 1)],
        },
    }


# ---- Season Monte Carlo (predictions_service._simulate_season_compute) -------

def simulate_season(games: list[dict], ratings: dict[str, float],
                    n_sims: int = 10_000, seed: int | None = 42) -> dict:
    """Exact port. `games` = full season schedule (played rows carry scores)."""
    banked_wins: dict[str, int] = defaultdict(int)
    banked_pd: dict[str, float] = defaultdict(float)
    pending = []
    for g in games:
        h, a = g["home_team"], g["away_team"]
        hs, as_ = g.get("home_score"), g.get("away_score")
        if hs is not None and as_ is not None:
            margin = float(hs) - float(as_)
            banked_pd[h] += margin
            banked_pd[a] -= margin
            if margin > 0:
                banked_wins[h] += 1
            elif margin < 0:
                banked_wins[a] += 1
        else:
            pending.append({"home": h, "away": a, "neutral": bool(g.get("neutral"))})

    win_distribution: dict[str, list[int]] = defaultdict(list)
    division_wins: dict[str, int] = defaultdict(int)
    playoff_appearances: dict[str, int] = defaultdict(int)
    sb_appearances: dict[str, int] = defaultdict(int)

    rng = random.Random(seed)
    for _ in range(n_sims):
        # Correlated latent strength: one draw per team, held all season.
        offset = {t: rng.gauss(0.0, RATING_SIGMA_ELO) for t in ratings}
        sim_wins = {t: banked_wins.get(t, 0) for t in ratings}
        sim_pd = {t: banked_pd.get(t, 0.0) for t in ratings}

        for game in pending:
            h, a = game["home"], game["away"]
            rh = ratings.get(h, INITIAL_RATING) + offset.get(h, 0.0)
            ra = ratings.get(a, INITIAL_RATING) + offset.get(a, 0.0)
            diff = rh - ra + (0.0 if game["neutral"] else HOME_FIELD_ADVANTAGE)
            expected_margin = diff / ELO_PER_POINT
            margin = rng.gauss(expected_margin, NFL_MARGIN_SIGMA)
            if margin >= 0:
                sim_wins[h] = sim_wins.get(h, 0) + 1
            else:
                sim_wins[a] = sim_wins.get(a, 0) + 1
            sim_pd[h] = sim_pd.get(h, 0.0) + margin
            sim_pd[a] = sim_pd.get(a, 0.0) - margin

        by_div: dict[tuple, list[tuple[str, int, float]]] = defaultdict(list)
        for team, wins in sim_wins.items():
            div = DIVISIONS.get(team)
            if div:
                by_div[div].append((team, wins, sim_pd.get(team, 0.0)))

        conf_seeds: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
        for (conf, _d), teams in by_div.items():
            winner = max(teams, key=lambda t: (t[1], t[2], rng.random()))
            division_wins[winner[0]] += 1
            conf_seeds[conf].append(winner)

        for conf, seeds in conf_seeds.items():
            seed_ids = {s[0] for s in seeds}
            others = [(t, sim_wins[t], sim_pd.get(t, 0.0)) for t in sim_wins
                      if DIVISIONS.get(t, (None,))[0] == conf and t not in seed_ids]
            others.sort(key=lambda t: (-t[1], -t[2], rng.random()))
            conf_seeds[conf] = seeds + others[:3]

        for conf, all_seven in conf_seeds.items():
            for team, *_ in all_seven:
                playoff_appearances[team] += 1
            best = max(all_seven, key=lambda t: (t[1], t[2], rng.random()))
            sb_appearances[best[0]] += 1

        for team, wins in sim_wins.items():
            win_distribution[team].append(wins)

    out = {}
    for team in ratings:
        dist = sorted(win_distribution.get(team, []))
        if not dist:
            continue
        mean_wins = sum(dist) / len(dist)
        var = sum((x - mean_wins) ** 2 for x in dist) / len(dist)
        out[team] = {
            "mean_wins": round(mean_wins, 1),
            "std_wins": round(var ** 0.5, 2),
            "p5_wins": dist[int(0.05 * len(dist))],
            "median_wins": dist[len(dist) // 2],
            "p95_wins": dist[int(0.95 * len(dist)) - 1],
            "division_winner_pct": round(100 * division_wins[team] / n_sims, 1),
            "playoff_pct": round(100 * playoff_appearances[team] / n_sims, 1),
            "sb_appearance_pct": round(100 * sb_appearances[team] / n_sims, 1),
        }
    return {"n_sims": n_sims, "teams": out}


# ---- CSV I/O + CLI -----------------------------------------------------------

def load_schedule_csv(path: str) -> list[dict]:
    games = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            def _num(v):
                v = (v or "").strip()
                return int(float(v)) if v else None
            games.append({
                "week": _num(row.get("week")) or 0,
                "home_team": row["home_team"].strip(),
                "away_team": row["away_team"].strip(),
                "home_score": _num(row.get("home_score")),
                "away_score": _num(row.get("away_score")),
                "neutral": (row.get("neutral") or "").strip().lower() in ("1", "true", "yes"),
            })
    return games


def _demo():
    print("=" * 70)
    print("GAME MODEL DEMO (no CSV supplied)")
    print("=" * 70)

    # 1. Single game: strong home team vs average visitor
    print("\n-- predict_game: KC (1650, 27.5 off / 19.5 def) vs LV (1450, 18 off / 25 def)")
    p = predict_game(1650, 1450, home_off_ppg=27.5, away_off_ppg=18.0,
                     home_def_ppg_allowed=19.5, away_def_ppg_allowed=25.0)
    for k in ("home_win_prob", "predicted_spread", "predicted_total",
              "predicted_home_score", "predicted_away_score", "game_script"):
        print(f"   {k}: {p[k]}")
    print(f"   80% margin interval: {p['distribution']['margin_interval_80']}")

    # 2. Betting math off the same distribution
    em = -p["predicted_spread"]
    print(f"\n-- betting layer (expected margin {em:.1f})")
    print(f"   P(home covers -7.5): {cover_prob_home(em, -7.5):.3f}")
    print(f"   P(over 47.5):        {over_prob(p['predicted_total'], 47.5):.3f}")
    print(f"   push prob at -7:     {push_prob(-7.0):.3f}")

    # 3. Elo update after a real result
    print("\n-- Elo update: home 1650 beats away 1450 by 10")
    nh, na = update_rating(1650, 1450, 10)
    print(f"   home 1650 -> {nh:.1f}   away 1450 -> {na:.1f}")

    # 4. Toy Monte Carlo: 4-team divisions with a spread of strengths
    print("\n-- Monte Carlo (2,000 sims, toy 8-game schedule, AFC West + NFC West)")
    teams = {"KC": 1640, "LAC": 1540, "DEN": 1500, "LV": 1430,
             "SF": 1620, "SEA": 1530, "LA": 1510, "ARI": 1440}
    sched = []
    ids = list(teams)
    for wk in range(1, 9):
        for i in range(0, 8, 2):
            h = ids[(i + wk) % 8]
            a = ids[(i + wk + 4) % 8]
            if h != a:
                sched.append({"week": wk, "home_team": h, "away_team": a,
                              "home_score": None, "away_score": None})
    sim = simulate_season(sched, teams, n_sims=2000)
    print(f"   {'team':5} {'mean_w':>6} {'std':>5} {'div%':>6} {'playoff%':>9}")
    for t, m in sorted(sim["teams"].items(), key=lambda kv: -kv[1]["mean_wins"]):
        print(f"   {t:5} {m['mean_wins']:>6} {m['std_wins']:>5} "
              f"{m['division_winner_pct']:>6} {m['playoff_pct']:>9}")

    # 5. Sanity checks
    print("\n-- sanity checks")
    even = predict_game(1500, 1500)
    assert abs(even["home_win_prob"] - norm_cdf(2.2 / 13.5)) < 1e-3, "HFA win prob"
    print(f"   equal teams, home edge from HFA only: {even['home_win_prob']} "
          f"(expected {norm_cdf(2.2/13.5):.3f})  OK")
    assert abs(win_prob(0.0) - 0.5) < 1e-9
    assert abs(norm_cdf(norm_ppf(0.8)) - 0.8) < 1e-6
    print("   norm_ppf/cdf round-trip OK; even-margin win prob = 0.500 OK")
    print("\nDemo complete.")


def main():
    ap = argparse.ArgumentParser(description="Standalone NFL game model")
    ap.add_argument("--csv", help="schedule CSV (week,home_team,away_team,home_score,away_score[,neutral])")
    ap.add_argument("--season-sims", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.csv:
        _demo()
        return

    games = load_schedule_csv(args.csv)
    ratings = build_elo_from_games(games)
    print(f"Built Elo from {sum(1 for g in games if g['home_score'] is not None)} "
          f"played games; {len(ratings)} teams.")
    top = sorted(ratings.items(), key=lambda kv: -kv[1])[:10]
    for t, r in top:
        print(f"   {t:5} {r:7.1f}")

    sim = simulate_season(games, ratings, n_sims=args.season_sims, seed=args.seed)
    print(f"\nSeason simulation ({sim['n_sims']} trials):")
    print(f"{'team':5} {'mean_w':>6} {'std':>5} {'p5':>4} {'p95':>4} {'div%':>6} {'playoff%':>9} {'sb%':>5}")
    for t, m in sorted(sim["teams"].items(), key=lambda kv: -kv[1]["mean_wins"]):
        print(f"{t:5} {m['mean_wins']:>6} {m['std_wins']:>5} {m['p5_wins']:>4} "
              f"{m['p95_wins']:>4} {m['division_winner_pct']:>6} "
              f"{m['playoff_pct']:>9} {m['sb_appearance_pct']:>5}")


if __name__ == "__main__":
    sys.exit(main())
