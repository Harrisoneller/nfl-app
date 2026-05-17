"""Head-to-head matchup composition.

Single endpoint returns everything the H2H page needs in one shot — avoids
the request waterfall of fetching 6 separate endpoints and helps the page
feel instant once warmed.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import analytics_service, betting_service, elo_service, predictions_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 min


async def head_to_head(
    db: Session, team_a: str, team_b: str, season: int | None = None,
) -> dict[str, Any]:
    """Composite of two teams: ratings, profiles, predicted matchup, history, trends."""
    a, b = team_a.upper(), team_b.upper()
    if a == b:
        return {"error": "Pick two different teams"}
    season = season or current_or_upcoming_season()

    cache_key = f"h2h:{a}:{b}:{season}"
    if (v := cache.get(cache_key)) is not None:
        return v

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
    aggs = await analytics_service._team_pbp_aggregates(season)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1)

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
        )
        predicted_game = {
            "home_team": a, "away_team": b, "week": None, "gameday": None,
            "neutral_site": True, "hypothetical": True, "prediction": pred,
        }

    # ---- Profile delta + cross-side matchup breakdown ----------------------
    profile_a = await analytics_service.team_profile(a, season=season - 1 if season > last_completed else season)
    profile_b = await analytics_service.team_profile(b, season=season - 1 if season > last_completed else season)
    deltas = _profile_deltas(profile_a, profile_b)
    matchup_breakdown = _cross_side_matchups(profile_a, profile_b, a, b)

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
        "profile": {
            "a": profile_a, "b": profile_b,
            "deltas": deltas,
        },
        "matchup_breakdown": matchup_breakdown,
        "history": history,
        "elo_history": {"a": elo_history_a, "b": elo_history_b},
    }
    cache.set(cache_key, result, CACHE_TTL)
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
        home_score = int(g["home_score"]); away_score = int(g["away_score"])
        home = g["home_team"]; away = g["away_team"]
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


def _matchup_side(
    off_team: str, off_profile: dict, def_team: str, def_profile: dict,
) -> dict:
    """Build one direction of the matchup ("when [off_team] has the ball")."""
    rows: list[dict] = []
    advantage_count = 0
    if not off_profile or "metrics" not in off_profile:
        return {"offense": off_team, "defense": def_team, "rows": [], "advantage_count": 0}
    if not def_profile or "metrics" not in def_profile:
        return {"offense": off_team, "defense": def_team, "rows": [], "advantage_count": 0}
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
        # Both are "higher means more production". For the offense, advantage
        # if off_val > def_val (offense averages more than this defense allows).
        expected = (off_val + def_val) / 2
        delta = off_val - def_val
        offense_has_edge = delta > 0
        if offense_has_edge:
            advantage_count += 1
        rows.append({
            "metric": off_key,
            "label": label,
            "off_value": round(off_val, 3),
            "def_value": round(def_val, 3),
            "expected": round(expected, 3),
            "delta": round(delta, 3),
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
) -> dict:
    """Two-sided matchup breakdown.

    "When A has the ball" pairs A's offensive metrics against B's defensive
    counterparts and vice versa. This is the analyst-style view — strength
    vs. weakness — instead of comparing offense-to-offense.
    """
    a_offense = _matchup_side(a, profile_a, b, profile_b)
    b_offense = _matchup_side(b, profile_b, a, profile_a)
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


def _safe_int(v) -> int | None:
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None
