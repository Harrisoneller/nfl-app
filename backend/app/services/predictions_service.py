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
from . import artifact_cache, elo_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 minutes


# Total scoring: league avg points/game per team is ~22; vary with team scoring.
LEAGUE_AVG_POINTS_PER_TEAM = 22.0


def predict_game(
    home_rating: float, away_rating: float,
    home_off_ppg: float | None = None, away_off_ppg: float | None = None,
    home_def_ppg_allowed: float | None = None, away_def_ppg_allowed: float | None = None,
    neutral_site: bool = False,
) -> dict[str, Any]:
    """Single-game predictor from Elo + scoring tendencies.

    Total = (home_off + away_def_allowed) / 2 + (away_off + home_def_allowed) / 2
    This blends each team's offensive output with what their opponent typically
    allows, instead of a fixed league average.
    """
    win_p = elo_service.win_probability(home_rating, away_rating, neutral_site)
    spread = elo_service.predicted_spread(home_rating, away_rating, neutral_site)
    h_off = home_off_ppg if home_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    a_off = away_off_ppg if away_off_ppg is not None else LEAGUE_AVG_POINTS_PER_TEAM
    h_def = home_def_ppg_allowed if home_def_ppg_allowed is not None else LEAGUE_AVG_POINTS_PER_TEAM
    a_def = away_def_ppg_allowed if away_def_ppg_allowed is not None else LEAGUE_AVG_POINTS_PER_TEAM
    expected_home_pts = (h_off + a_def) / 2
    expected_away_pts = (a_off + h_def) / 2
    total = expected_home_pts + expected_away_pts
    # Game-script label: shootout / methodical / defensive based on total + spread.
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
        "predicted_spread": round(spread, 1),       # negative = home favored
        "predicted_total": round(total, 1),
        "predicted_home_score": round((total / 2) + (-spread / 2), 1),
        "predicted_away_score": round((total / 2) - (-spread / 2), 1),
        "game_script": script,
        # Surface every input so the UI can render an "explain this" popover
        "inputs": {
            "home_elo": round(home_rating, 1),
            "away_elo": round(away_rating, 1),
            "home_field_advantage_elo": 0 if neutral_site else 55.0,
            "neutral_site": neutral_site,
            "home_off_ppg": round(h_off, 1),
            "away_off_ppg": round(a_off, 1),
            "home_def_ppg_allowed": round(h_def, 1),
            "away_def_ppg_allowed": round(a_def, 1),
            "expected_home_pts": round(expected_home_pts, 1),
            "expected_away_pts": round(expected_away_pts, 1),
        },
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
    # Pull team scoring tendencies once for the season; fall back to previous if empty.
    aggs = await analytics_service._team_pbp_aggregates(season)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1)
    games = sched[sched["week"] == week]
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper() == "REG"]
    out = []
    for _, g in games.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        h_off = (aggs.get(h) or {}).get("points_per_game")
        a_off = (aggs.get(a) or {}).get("points_per_game")
        h_def = (aggs.get(h) or {}).get("points_allowed_per_game")
        a_def = (aggs.get(a) or {}).get("points_allowed_per_game")
        pred = predict_game(hr, ar, home_off_ppg=h_off, away_off_ppg=a_off,
                            home_def_ppg_allowed=h_def, away_def_ppg_allowed=a_def)
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

    Persisted to model_artifacts so the same simulation result is shared
    across all workers/processes and survives backend restarts. Refreshed
    daily by the scheduler.
    """
    # Two-layer cached fetch — L1 in-process, L2 Postgres
    artifact_key = f"season:{season}:n:{n_sims}"

    async def _compute() -> dict[str, Any]:
        return await _simulate_season_compute(db, season, n_sims)

    return await artifact_cache.get_or_compute(
        kind="monte_carlo_sim",
        key=artifact_key,
        compute=_compute,
        ttl_seconds=60 * 60 * 24,  # 24h
        l1_ttl_seconds=60 * 30,
    )


async def _simulate_season_compute(
    db: Session, season: int, n_sims: int,
) -> dict[str, Any]:
    """The real Monte Carlo. Separated so artifact_cache can wrap it."""
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

    return {"season": season, "n_sims": n_sims, "teams": out}


async def team_season_outlook(db: Session, team_id: str, season: int | None = None) -> dict[str, Any]:
    season = season or current_or_upcoming_season()
    sim = await simulate_season(db, season)
    return {
        "team_id": team_id,
        "season": season,
        **sim["teams"].get(team_id, {}),
    }


async def team_remaining_schedule_predictions(
    db: Session, team_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Predicted spread + win prob for every remaining game in the team's season.

    Returns a list ordered by week with cumulative-wins projection so the UI
    can chart the expected trajectory.
    """
    season = season or current_or_upcoming_season()
    sched = await _season_schedule(season)
    if sched is None:
        return {"team_id": team_id, "season": season, "games": []}

    team_games = sched[(sched["home_team"] == team_id) | (sched["away_team"] == team_id)]
    team_games = team_games.sort_values("week")

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    aggs = await analytics_service._team_pbp_aggregates(season)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1)

    out_games = []
    cumulative_expected_wins = 0.0
    banked_wins = 0
    for _, g in team_games.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        is_home = h == team_id
        opp = a if is_home else h
        hs, as_ = g.get("home_score"), g.get("away_score")
        played = pd.notna(hs) and pd.notna(as_)

        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        h_off = (aggs.get(h) or {}).get("points_per_game")
        a_off = (aggs.get(a) or {}).get("points_per_game")
        h_def = (aggs.get(h) or {}).get("points_allowed_per_game")
        a_def = (aggs.get(a) or {}).get("points_allowed_per_game")
        pred = predict_game(hr, ar, home_off_ppg=h_off, away_off_ppg=a_off,
                            home_def_ppg_allowed=h_def, away_def_ppg_allowed=a_def)
        my_win_prob = pred["home_win_prob"] if is_home else pred["away_win_prob"]

        outcome: str | None = None
        if played:
            if (hs > as_ and is_home) or (as_ > hs and not is_home):
                outcome = "W"
                banked_wins += 1
            elif hs == as_:
                outcome = "T"
                banked_wins += 0.5
            else:
                outcome = "L"

        if not played:
            cumulative_expected_wins += my_win_prob
        out_games.append({
            "id": str(g.get("game_id") or ""),
            "week": _safe_int(g.get("week")),
            "gameday": str(g.get("gameday") or ""),
            "opponent": opp,
            "is_home": is_home,
            "played": played,
            "outcome": outcome,
            "my_score": _safe_int(hs if is_home else as_),
            "opp_score": _safe_int(as_ if is_home else hs),
            "win_prob": round(my_win_prob, 3),
            "predicted_spread_for_team": round(
                pred["predicted_spread"] if is_home else -pred["predicted_spread"], 1
            ),
            "predicted_total": pred["predicted_total"],
            "cumulative_projected_wins": round(banked_wins + cumulative_expected_wins, 2),
            "opp_elo": round(ar if is_home else hr, 0),
        })

    final_projected = banked_wins + cumulative_expected_wins
    return {
        "team_id": team_id,
        "season": season,
        "games": out_games,
        "banked_wins": banked_wins,
        "projected_remaining_wins": round(cumulative_expected_wins, 2),
        "projected_total_wins": round(final_projected, 2),
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
