"""Game predictions + season Monte Carlo simulator.

Both reads ratings out of `elo_service`. Per-game outputs include win prob,
predicted spread, and predicted total. Season simulation runs ~10k trials of
the remaining schedule and aggregates wins, division winner counts, and
playoff seed odds.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.seed import NFL_TEAMS
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import elo_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 minutes


# Total scoring: league avg points/game per team is ~22; vary with team scoring.
LEAGUE_AVG_POINTS_PER_TEAM = 22.0


def predict_game(
    home_rating: float, away_rating: float,
    home_off_ppg: float | None = None, away_off_ppg: float | None = None,
    neutral_site: bool = False,
) -> dict[str, Any]:
    """Single-game predictor from Elo ratings (and optional scoring tendencies)."""
    win_p = elo_service.win_probability(home_rating, away_rating, neutral_site)
    spread = elo_service.predicted_spread(home_rating, away_rating, neutral_site)
    h_off = home_off_ppg if home_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    a_off = away_off_ppg if away_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    total = h_off + a_off
    return {
        "home_win_prob": round(win_p, 3),
        "away_win_prob": round(1 - win_p, 3),
        "predicted_spread": round(spread, 1),       # negative = home favored
        "predicted_total": round(total, 1),
        "predicted_home_score": round((total / 2) + (-spread / 2), 1),
        "predicted_away_score": round((total / 2) - (-spread / 2), 1),
    }


# ---- Season-level: read schedule once, simulate ----------------------------


async def _season_schedule(season: int) -> pd.DataFrame | None:
    df = await _nfl.schedules_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    return df


async def predict_week(db: Session, season: int, week: int | None = None) -> dict[str, Any]:
    """Predictions for every game in the given week.

    If `week` is None, picks the next upcoming week (lowest week with unplayed games).
    """
    sched = await _season_schedule(season)
    if sched is None:
        return {"season": season, "week": None, "games": []}

    if week is None:
        # First week with at least one unplayed game
        unplayed = sched[sched["home_score"].isna() | sched["away_score"].isna()]
        if len(unplayed) == 0:
            return {"season": season, "week": None, "games": []}
        week = int(unplayed["week"].min())

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    games = sched[sched["week"] == week]
    out = []
    for _, g in games.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        pred = predict_game(hr, ar)
        out.append({
            "id": str(g.get("game_id") or ""),
            "season": season,
            "week": week,
            "gameday": str(g.get("gameday") or ""),
            "home_team_id": h,
            "away_team_id": a,
            "home_score": _safe_int(g.get("home_score")),
            "away_score": _safe_int(g.get("away_score")),
            "home_elo": round(hr, 1),
            "away_elo": round(ar, 1),
            "prediction": pred,
        })
    return {"season": season, "week": week, "games": out}


# ---- Monte Carlo ----------------------------------------------------------

DIVISIONS = {}
for t in NFL_TEAMS:
    DIVISIONS[t["id"]] = (t["conference"], t["division"])


async def simulate_season(
    db: Session, season: int, n_sims: int = 10_000,
) -> dict[str, Any]:
    """Run N simulations of the remaining schedule.

    For each team returns:
      - mean wins, p5/median/p95 wins
      - division winner odds
      - top-7 playoff seed odds (overall make-playoffs %)
      - Super Bowl appearance odds (best-record proxy; we don't simulate the
        bracket — just "best team in conference makes SB" as a rough heuristic)
    """
    key = f"season_sim:{season}:{n_sims}"
    if (v := cache.get(key)) is not None:
        return v

    sched = await _season_schedule(season)
    if sched is None:
        return {"season": season, "n_sims": 0, "teams": {}}

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    if not ratings:
        # Cold start — use defaults
        ratings = {t["id"]: elo_service.INITIAL_RATING for t in NFL_TEAMS}

    # Banked wins/losses from already-completed games
    banked: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [wins, losses]
    pending: list[dict[str, Any]] = []
    for _, g in sched.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if pd.notna(hs) and pd.notna(as_):
            if hs > as_:
                banked[h][0] += 1; banked[a][1] += 1
            elif hs < as_:
                banked[a][0] += 1; banked[h][1] += 1
        else:
            pending.append({"home": h, "away": a, "neutral": bool(g.get("location") == "Neutral") if "location" in sched.columns else False})

    # Counters
    win_distribution: dict[str, list[int]] = defaultdict(list)
    division_wins: dict[str, int] = defaultdict(int)
    playoff_appearances: dict[str, int] = defaultdict(int)
    sb_appearances: dict[str, int] = defaultdict(int)

    rng = random.Random(42)  # deterministic — set None for fresh randomness each call

    for _sim in range(n_sims):
        sim_wins: dict[str, int] = {t: banked[t][0] for t in ratings}
        # Don't actually update Elo within a sim — keeps it light + fast
        for game in pending:
            h, a = game["home"], game["away"]
            wp = elo_service.win_probability(
                ratings.get(h, elo_service.INITIAL_RATING),
                ratings.get(a, elo_service.INITIAL_RATING),
                neutral_site=game["neutral"],
            )
            if rng.random() < wp:
                sim_wins[h] = sim_wins.get(h, 0) + 1
            else:
                sim_wins[a] = sim_wins.get(a, 0) + 1

        # Compute division winners and playoff seeds
        by_div: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
        for team, wins in sim_wins.items():
            div = DIVISIONS.get(team)
            if div:
                by_div[div].append((team, wins))

        # Division winners (4 per conf)
        conf_seeds: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for (conf, division), teams in by_div.items():
            winner = max(teams, key=lambda t: (t[1], rng.random()))
            division_wins[winner[0]] += 1
            conf_seeds[conf].append(winner)

        # Wildcards (3 per conf): top 3 non-division-winners
        for conf, seeds in conf_seeds.items():
            seed_team_ids = {s[0] for s in seeds}
            others = [
                (team, w) for team, w in sim_wins.items()
                if DIVISIONS.get(team, (None,))[0] == conf and team not in seed_team_ids
            ]
            others.sort(key=lambda t: (-t[1], rng.random()))
            conf_seeds[conf] = seeds + others[:3]

        # Mark playoff appearances
        for conf, all_seven in conf_seeds.items():
            for team, _ in all_seven:
                playoff_appearances[team] += 1
            # Crude SB heuristic: best record in conference
            best = max(all_seven, key=lambda t: (t[1], rng.random()))
            sb_appearances[best[0]] += 1

        for team, wins in sim_wins.items():
            win_distribution[team].append(wins)

    # Aggregate
    out: dict[str, Any] = {}
    for team in ratings:
        dist = sorted(win_distribution.get(team, []))
        if not dist:
            continue
        out[team] = {
            "mean_wins": round(sum(dist) / len(dist), 1),
            "p5_wins": dist[int(0.05 * len(dist))],
            "median_wins": dist[len(dist) // 2],
            "p95_wins": dist[int(0.95 * len(dist)) - 1],
            "division_winner_pct": round(100 * division_wins[team] / n_sims, 1),
            "playoff_pct": round(100 * playoff_appearances[team] / n_sims, 1),
            "sb_appearance_pct": round(100 * sb_appearances[team] / n_sims, 1),
        }

    result = {"season": season, "n_sims": n_sims, "teams": out}
    cache.set(key, result, CACHE_TTL)
    return result


async def team_season_outlook(db: Session, team_id: str, season: int | None = None) -> dict[str, Any]:
    season = season or current_or_upcoming_season()
    sim = await simulate_season(db, season)
    return {
        "team_id": team_id,
        "season": season,
        **sim["teams"].get(team_id, {}),
    }


async def projected_standings(db: Session, season: int | None = None) -> dict[str, Any]:
    """Projected division standings: mean wins ordered per division."""
    season = season or current_or_upcoming_season()
    sim = await simulate_season(db, season)
    by_div: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for team_id, m in sim["teams"].items():
        div = DIVISIONS.get(team_id)
        if div is None:
            continue
        by_div[div].append({"team_id": team_id, **m})
    for k in by_div:
        by_div[k].sort(key=lambda t: -t["mean_wins"])
    return {
        "season": season,
        "divisions": [
            {"conference": conf, "division": div, "teams": teams}
            for (conf, div), teams in sorted(by_div.items())
        ],
    }


# ---- Helpers ---------------------------------------------------------------


def _safe_int(v) -> int | None:
    try:
        if pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
