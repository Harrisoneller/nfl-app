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
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.game import Game
from ..models.seed import NFL_TEAMS
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import (
    analytics_service,
    artifact_cache,
    backtest_service,
    elo_service,
    prediction_dist,
    uncertainty_service,
)

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 minutes
PREDICTION_MODEL_VERSION = "elo-v2"


# Total scoring: league avg points/game per team is ~22; vary with team scoring.
# Registry-backed ("game_model" / "distribution" categories): the module
# constants are import-safe fallbacks, the helpers below resolve live values.
LEAGUE_AVG_POINTS_PER_TEAM = 22.0

# Game-margin SD used to turn the point spread into an outcome distribution.
GAME_SIGMA = prediction_dist.NFL_MARGIN_SIGMA  # ~13.5 points


def _league_avg() -> float:
    from . import param_registry
    return param_registry.value("game.league_avg_points")


def _game_sigma() -> float:
    return prediction_dist.margin_sigma()

# Season-long latent-strength uncertainty per team (Elo points), drawn once per
# Monte Carlo trial and held across that team's whole slate. This is what makes
# the season win-total distribution correlated and realistically wide instead of
# an over-tight sum of independent coin flips. TUNABLE: validate against the
# backtest PIT histogram / observed win-total dispersion (see PREDICTION_MODEL_SPEC).
RATING_SIGMA_ELO = 55.0


def _rating_sigma() -> float:
    from . import param_registry
    return param_registry.value("game.rating_sigma_elo")


def _build_explainability(
    *,
    home_rating: float,
    away_rating: float,
    neutral_site: bool,
    home_off_ppg: float,
    away_off_ppg: float,
    home_def_ppg_allowed: float,
    away_def_ppg_allowed: float,
) -> dict[str, Any]:
    """Transparent v1 feature contribution heuristic for prediction UI."""
    hfa = 0.0 if neutral_site else elo_service.HOME_FIELD_ADVANTAGE
    rating_edge = home_rating + hfa - away_rating
    home_matchup_edge = home_off_ppg - away_def_ppg_allowed
    away_matchup_edge = away_off_ppg - home_def_ppg_allowed
    scoring_edge = home_matchup_edge - away_matchup_edge
    pace_bias = (home_off_ppg + away_off_ppg) - (2 * _league_avg())
    defense_resistance_edge = away_def_ppg_allowed - home_def_ppg_allowed

    contributors = [
        {
            "feature": "elo_rating_gap",
            "label": "Elo + home-field gap",
            "impact": round(rating_edge / 28.0, 2),
            "direction": "home" if rating_edge >= 0 else "away",
        },
        {
            "feature": "offense_vs_defense_gap",
            "label": "Offense vs opposing defense",
            "impact": round(scoring_edge / 2.8, 2),
            "direction": "home" if scoring_edge >= 0 else "away",
        },
        {
            "feature": "defensive_resistance_gap",
            "label": "Defensive resistance edge",
            "impact": round(defense_resistance_edge / 2.2, 2),
            "direction": "home" if defense_resistance_edge >= 0 else "away",
        },
        {
            "feature": "game_pace_environment",
            "label": "Expected scoring environment",
            "impact": round(pace_bias / 6.0, 2),
            "direction": "home" if pace_bias >= 0 else "away",
        },
    ]
    contributors.sort(key=lambda x: abs(float(x["impact"])), reverse=True)
    return {
        "method": "heuristic_inputs_v1",
        "summary": "Directional feature impacts estimated from Elo and scoring tendency inputs.",
        "top_contributors": contributors[:3],
    }


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
    spread = elo_service.predicted_spread(home_rating, away_rating, neutral_site)
    expected_margin = -spread  # home perspective: positive = home favored
    # Win probability is derived FROM the margin distribution (not the Elo
    # logistic) so spread, win prob, and score ranges are mutually consistent.
    game_sigma = _game_sigma()
    win_p = prediction_dist.win_prob(expected_margin, game_sigma)

    league_avg = _league_avg()
    h_off = home_off_ppg if home_off_ppg is not None else league_avg
    a_off = away_off_ppg if away_off_ppg is not None else league_avg
    h_def = home_def_ppg_allowed if home_def_ppg_allowed is not None else league_avg
    a_def = away_def_ppg_allowed if away_def_ppg_allowed is not None else league_avg
    # Matchup-adjusted scoring relative to the league baseline (replaces the
    # midpoint average): a team's own pace, nudged by how much more/less than a
    # league-average defense the opponent allows. Same correction as h2h_service.
    expected_home_pts = h_off + (a_def - league_avg)
    expected_away_pts = a_off + (h_def - league_avg)
    total = expected_home_pts + expected_away_pts

    # Reconcile the (well-calibrated) Elo margin with the total for displayed scores.
    predicted_home_score = (total + expected_margin) / 2
    predicted_away_score = (total - expected_margin) / 2

    # Outcome distribution — the "likely outcomes" view. Margin credible
    # intervals translate to score ranges (total held at its expectation).
    m_lo80, m_hi80 = prediction_dist.margin_interval(expected_margin, game_sigma, 0.80)
    m_lo50, m_hi50 = prediction_dist.margin_interval(expected_margin, game_sigma, 0.50)

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
        "predicted_home_score": round(predicted_home_score, 1),
        "predicted_away_score": round(predicted_away_score, 1),
        "game_script": script,
        "margin_sd": game_sigma,
        # Full outcome distribution so the UI can show honest ranges, not just a point.
        "distribution": {
            "expected_margin": round(expected_margin, 1),
            "margin_sd": game_sigma,
            "home_win_prob": round(win_p, 3),
            "margin_interval_50": [round(m_lo50, 1), round(m_hi50, 1)],
            "margin_interval_80": [round(m_lo80, 1), round(m_hi80, 1)],
            "home_score_range_80": [round((total + m_lo80) / 2, 1), round((total + m_hi80) / 2, 1)],
            "away_score_range_80": [round((total - m_hi80) / 2, 1), round((total - m_lo80) / 2, 1)],
        },
        # Surface every input so the UI can render an "explain this" popover
        "inputs": {
            "home_elo": round(home_rating, 1),
            "away_elo": round(away_rating, 1),
            "home_field_advantage_elo": 0 if neutral_site else elo_service.HOME_FIELD_ADVANTAGE,
            "neutral_site": neutral_site,
            "home_off_ppg": round(h_off, 1),
            "away_off_ppg": round(a_off, 1),
            "home_def_ppg_allowed": round(h_def, 1),
            "away_def_ppg_allowed": round(a_def, 1),
            "league_avg_points": league_avg,
            "expected_home_pts": round(expected_home_pts, 1),
            "expected_away_pts": round(expected_away_pts, 1),
        },
        "explainability": _build_explainability(
            home_rating=home_rating,
            away_rating=away_rating,
            neutral_site=neutral_site,
            home_off_ppg=h_off,
            away_off_ppg=a_off,
            home_def_ppg_allowed=h_def,
            away_def_ppg_allowed=a_def,
        ),
    }


# ---- Season-level: read schedule once, simulate ----------------------------


def _schedule_from_db(db: Session, season: int) -> pd.DataFrame | None:
    """Read the schedule from the games table (fast, no network).

    Deduplicates by (week, home_team, away_team) — ESPN scoreboard and nflverse
    can both create records for the same real-world game with different IDs.
    Prefers the nflverse record (game_id like "2026_01_AWAY_HOME") because it
    has accurate NULL scores for unplayed games (ESPN reports 0).
    """
    stmt = select(Game).where(Game.season == season, Game.season_type == 2)
    games = db.execute(stmt).scalars().all()
    if not games:
        return None

    seen: dict[tuple, dict] = {}
    for g in games:
        key = (g.week, g.home_team_id, g.away_team_id)
        is_nflverse_id = g.id and "_" in g.id and g.id[:4].isdigit()
        row = {
            "game_id": g.id,
            "season": g.season,
            "week": g.week,
            "home_team": g.home_team_id,
            "away_team": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "gameday": g.start_time.strftime("%Y-%m-%d") if g.start_time else "",
            "gametime": g.start_time.strftime("%H:%M") if g.start_time else "",
            "game_type": "REG",
            "_is_nflverse": is_nflverse_id,
        }
        existing = seen.get(key)
        if existing is None:
            seen[key] = row
        elif is_nflverse_id and not existing.get("_is_nflverse"):
            seen[key] = row
        elif not is_nflverse_id and existing.get("_is_nflverse"):
            pass  # keep existing nflverse record
        elif g.home_score is None and existing.get("home_score") is not None:
            seen[key] = row

    rows = [{k: v for k, v in r.items() if k != "_is_nflverse"} for r in seen.values()]
    return pd.DataFrame(rows) if rows else None


async def _season_schedule(season: int, db: Session | None = None) -> pd.DataFrame | None:
    """Get schedule from DB first (synced by worker), fall back to nflverse."""
    if db is not None:
        df = _schedule_from_db(db, season)
        if df is not None and len(df) > 0:
            df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
            df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
            return df

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
    sched = await _season_schedule(season, db=db)
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
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)
    # Admin model-input levers (pace / yards-per-play / pass-rate / PPG) adjust
    # the scoring inputs BEFORE prediction — a coaching-change lever moves
    # totals, scores, and game scripts through the normal pipeline.
    from . import model_inputs_service

    aggs = model_inputs_service.adjusted_team_aggregates(db, season, aggs or {})
    games = sched[sched["week"] == week]
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper() == "REG"]
    out = []
    calibration_score, expected_calibration_error = await _calibration_context(db)
    # Market-aware layer: de-vigged multi-book consensus (+ Kalshi when
    # reachable) fetched once for the whole slate. Best-effort — an empty
    # context leaves every game on model-only numbers.
    from . import market_service  # late import: market_service ← prediction_dist only

    try:
        market_ctx = await market_service.week_market_context(db)
    except Exception as e:  # noqa: BLE001 — market context must never take down predictions
        log.warning("market_context_failed", error=str(e)[:200])
        market_ctx = {}
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
        pred = uncertainty_service.attach_uncertainty(
            pred,
            model_version=PREDICTION_MODEL_VERSION,
            expected_calibration_error=expected_calibration_error,
        )
        # Headline numbers become the market blend when consensus exists;
        # pure-model values move to pred["model_only"] with pred["edge"]
        # exposing the disagreement. Admin overrides (below) still win.
        market_service.apply_market_blend(
            pred, market_service.context_for_game(market_ctx, h, a),
        )
        explainability = pred.get("explainability")
        if isinstance(explainability, dict):
            explainability["confidence_context"] = {
                "tier": pred.get("confidence_tier"),
                "calibration_score": pred.get("calibration_score"),
                "expected_calibration_error": pred.get("expected_calibration_error"),
                "interval_80_home_win_prob": pred.get("home_win_prob_interval_80"),
            }
        pred["global_calibration_score"] = calibration_score
        out.append({
            "id": str(g.get("game_id") or ""),
            "season": season,
            "week": week,
            "gameday": str(g.get("gameday") or ""),
            "gametime": str(g.get("gametime") or ""),
            "home_team_id": h,
            "away_team_id": a,
            "home_score": _safe_int(g.get("home_score")),
            "away_score": _safe_int(g.get("away_score")),
            "home_elo": round(hr, 1),
            "away_elo": round(ar, 1),
            "prediction": pred,
        })
    # Admin override layer — hand-set spread/total/win-prob supersede the
    # model at read time (see services/overrides_service.py). Late import:
    # overrides_service ← player_projection_engine only, no cycle, but keep
    # this module importable standalone in scripts.
    from . import overrides_service

    overrides_service.apply_week_game_overrides(db, season, week, out)
    return {"season": season, "week": week, "games": out}


async def _calibration_context(db: Session) -> tuple[float, float | None]:
    """Load calibration metadata from backtest artifacts."""
    try:
        backtest = await backtest_service.backtest_elo(db)
        overall = backtest.get("overall", {})
        ece = overall.get("expected_calibration_error")
        score = uncertainty_service.calibration_score_from_ece(ece)
        return score, ece
    except Exception as e:  # noqa: BLE001
        log.warning("prediction_calibration_lookup_failed", error=str(e)[:200])
        return 0.5, None


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
    sched = await _season_schedule(season, db=db)
    if sched is None:
        return {"season": season, "n_sims": 0, "teams": {}}

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    if not ratings:
        # Cold start — use defaults
        ratings = {t["id"]: elo_service.INITIAL_RATING for t in NFL_TEAMS}

    # Banked results from completed games (wins + point differential).
    banked_wins: dict[str, int] = defaultdict(int)
    banked_pd: dict[str, float] = defaultdict(float)
    pending: list[dict[str, Any]] = []
    for _, g in sched.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if pd.notna(hs) and pd.notna(as_):
            margin = float(hs) - float(as_)
            banked_pd[h] += margin; banked_pd[a] -= margin
            if margin > 0:
                banked_wins[h] += 1
            elif margin < 0:
                banked_wins[a] += 1
        else:
            pending.append({
                "home": h, "away": a,
                "neutral": bool(g.get("location") == "Neutral") if "location" in sched.columns else False,
            })

    # Counters
    win_distribution: dict[str, list[int]] = defaultdict(list)
    division_wins: dict[str, int] = defaultdict(int)
    playoff_appearances: dict[str, int] = defaultdict(int)
    sb_appearances: dict[str, int] = defaultdict(int)

    rng = random.Random(42)  # deterministic — set None for fresh randomness each call
    hfa = elo_service.HOME_FIELD_ADVANTAGE
    elo_per_point = elo_service.ELO_PER_POINT

    for _sim in range(n_sims):
        # --- Correlated team strength (the variance fix) ----------------------
        # Draw a single season-long latent-strength offset per team and hold it
        # across that team's entire remaining slate. Because the offset persists,
        # a team that is "secretly good" in this trial wins across ALL its games,
        # which is what makes simulated win totals over-dispersed (realistically
        # wide) instead of an over-tight sum of independent coin flips. The draws
        # are mean-zero, so the central projection is unchanged — only the spread
        # widens.
        rating_sigma = _rating_sigma()
        offset = {t: rng.gauss(0.0, rating_sigma) for t in ratings}
        sim_wins: dict[str, int] = {t: banked_wins.get(t, 0) for t in ratings}
        sim_pd: dict[str, float] = {t: banked_pd.get(t, 0.0) for t in ratings}

        for game in pending:
            h, a = game["home"], game["away"]
            rh = ratings.get(h, elo_service.INITIAL_RATING) + offset.get(h, 0.0)
            ra = ratings.get(a, elo_service.INITIAL_RATING) + offset.get(a, 0.0)
            diff = rh - ra + (0.0 if game["neutral"] else hfa)
            expected_margin = diff / elo_per_point
            # Simulate an actual margin (not just W/L) so point differential is
            # available for tiebreakers and the win prob is consistent with the
            # game-level distribution model.
            margin = rng.gauss(expected_margin, GAME_SIGMA)
            if margin >= 0:
                sim_wins[h] = sim_wins.get(h, 0) + 1
            else:
                sim_wins[a] = sim_wins.get(a, 0) + 1
            sim_pd[h] = sim_pd.get(h, 0.0) + margin
            sim_pd[a] = sim_pd.get(a, 0.0) - margin

        # Division winners + playoff seeds. Ties broken by point differential,
        # then a coin flip (previously a pure coin flip).
        by_div: dict[tuple, list[tuple[str, int, float]]] = defaultdict(list)
        for team, wins in sim_wins.items():
            div = DIVISIONS.get(team)
            if div:
                by_div[div].append((team, wins, sim_pd.get(team, 0.0)))

        conf_seeds: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
        for (conf, _division), teams in by_div.items():
            winner = max(teams, key=lambda t: (t[1], t[2], rng.random()))
            division_wins[winner[0]] += 1
            conf_seeds[conf].append(winner)

        # Wildcards (3 per conf): top 3 non-division-winners by wins, then PD.
        for conf, seeds in conf_seeds.items():
            seed_team_ids = {s[0] for s in seeds}
            others = [
                (team, sim_wins[team], sim_pd.get(team, 0.0))
                for team in sim_wins
                if DIVISIONS.get(team, (None,))[0] == conf and team not in seed_team_ids
            ]
            others.sort(key=lambda t: (-t[1], -t[2], rng.random()))
            conf_seeds[conf] = seeds + others[:3]

        # Mark playoff appearances
        for conf, all_seven in conf_seeds.items():
            for team, *_rest in all_seven:
                playoff_appearances[team] += 1
            # Crude SB heuristic: best seed in conference (wins, then PD)
            best = max(all_seven, key=lambda t: (t[1], t[2], rng.random()))
            sb_appearances[best[0]] += 1

        for team, wins in sim_wins.items():
            win_distribution[team].append(wins)

    # Aggregate
    out: dict[str, Any] = {}
    for team in ratings:
        dist = sorted(win_distribution.get(team, []))
        if not dist:
            continue
        mean_wins = sum(dist) / len(dist)
        var = sum((x - mean_wins) ** 2 for x in dist) / len(dist)
        out[team] = {
            "mean_wins": round(mean_wins, 1),
            "std_wins": round(var ** 0.5, 2),   # spread of the win-total distribution
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
    sched = await _season_schedule(season, db=db)
    if sched is None:
        return {"team_id": team_id, "season": season, "games": []}

    team_games = sched[(sched["home_team"] == team_id) | (sched["away_team"] == team_id)]
    team_games = team_games.sort_values("week")

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)
    from . import model_inputs_service

    aggs = model_inputs_service.adjusted_team_aggregates(db, season, aggs or {})

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
