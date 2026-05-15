"""Elo rating engine.

Classic NFL Elo, following the 538 / Inpredictable approach:

  - K-factor: 20 (mild update rate)
  - Home-field advantage: 55 Elo points (worth ~2.2 game points)
  - Margin-of-victory multiplier: log-based, dampened by Elo diff
  - Season-over-season regression: 0.75 retention of last year's rating
    blended with 1500 mean (so good teams cool off, bad teams heat up)

Public surface:
  - rebuild_history(seasons): compute Elo from scratch for the given seasons
  - update_to_date(): incremental update through latest completed game
  - current_ratings(): { team_id: rating } at the most recent week
  - rating_at(team, season, week): for explainers
  - win_probability(home_rating, away_rating, neutral_site=False)
  - predicted_spread(home_rating, away_rating, neutral_site=False)
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..logging_config import get_logger
from ..models.elo import TeamEloRating
from ..utils.seasons import latest_completed_season
from ..utils.teams import canonical_team

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

# ---- Constants -------------------------------------------------------------

K_FACTOR = 20.0
HOME_FIELD_ADVANTAGE = 55.0      # Elo points (≈ +2.2 spread points)
SEASON_REGRESSION = 0.75         # Carry-over fraction; new season blends toward 1500
INITIAL_RATING = 1500.0
ELO_PER_POINT = 25.0             # Elo diff per game-point on the spread


# ---- Math helpers ---------------------------------------------------------


def win_probability(home_rating: float, away_rating: float, neutral_site: bool = False) -> float:
    diff = home_rating - away_rating + (0 if neutral_site else HOME_FIELD_ADVANTAGE)
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def predicted_spread(home_rating: float, away_rating: float, neutral_site: bool = False) -> float:
    """Negative spread = home team favored. Matches sportsbook convention."""
    diff = home_rating - away_rating + (0 if neutral_site else HOME_FIELD_ADVANTAGE)
    return -(diff / ELO_PER_POINT)


def _mov_multiplier(margin: int, elo_diff: float) -> float:
    """538-style margin-of-victory multiplier.

    Dampens the K-factor by Elo difference so blowouts by strong favorites
    don't over-update.
    """
    return math.log(max(abs(margin), 1) + 1) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))


def update_rating(
    home_rating: float, away_rating: float, home_margin: int, neutral_site: bool = False
) -> tuple[float, float]:
    """Returns (new_home, new_away) given a played game."""
    expected_home = win_probability(home_rating, away_rating, neutral_site)
    actual_home = 1.0 if home_margin > 0 else (0.0 if home_margin < 0 else 0.5)
    diff_for_mov = home_rating - away_rating + (0 if neutral_site else HOME_FIELD_ADVANTAGE)
    if home_margin < 0:
        diff_for_mov = -diff_for_mov  # winning team's perspective for MOV
    mov = _mov_multiplier(home_margin, diff_for_mov)
    delta = K_FACTOR * mov * (actual_home - expected_home)
    return home_rating + delta, away_rating - delta


# ---- Persistence ---------------------------------------------------------


def _persist_week(db: Session, season: int, week: int, ratings: dict[str, float]) -> None:
    """Upsert all teams' ratings for a single week. Idempotent."""
    existing = {
        (r.team_id, r.season, r.week): r
        for r in db.query(TeamEloRating)
        .filter(and_(TeamEloRating.season == season, TeamEloRating.week == week))
        .all()
    }
    for team_id, rating in ratings.items():
        key = (team_id, season, week)
        if key in existing:
            existing[key].rating = rating
        else:
            db.add(TeamEloRating(team_id=team_id, season=season, week=week, rating=rating))
    db.commit()


# ---- History build ---------------------------------------------------------


async def rebuild_history(db: Session, seasons: list[int]) -> int:
    """Compute Elo from scratch for the given seasons (in order).

    Returns the total number of week-rows persisted. Existing rows for those
    seasons are overwritten. Safe to call repeatedly.
    """
    if not seasons:
        return 0
    seasons = sorted(seasons)

    ratings: dict[str, float] = defaultdict(lambda: INITIAL_RATING)

    # Carry forward from the season immediately preceding the first season,
    # if we already have ratings for it.
    prior_year = seasons[0] - 1
    prev = (
        db.query(TeamEloRating)
        .filter(TeamEloRating.season == prior_year)
        .all()
    )
    if prev:
        latest_by_team: dict[str, TeamEloRating] = {}
        for r in prev:
            cur = latest_by_team.get(r.team_id)
            if cur is None or r.week > cur.week:
                latest_by_team[r.team_id] = r
        for team_id, r in latest_by_team.items():
            ratings[team_id] = r.rating

    total_rows = 0
    for season in seasons:
        # Season regression toward 1500
        for t in list(ratings.keys()):
            ratings[t] = SEASON_REGRESSION * ratings[t] + (1 - SEASON_REGRESSION) * INITIAL_RATING

        # Persist Week 0 (pre-season baseline)
        _persist_week(db, season, 0, dict(ratings))
        total_rows += len(ratings)

        # Walk through completed games in order
        sched = await _nfl.schedules_df(season)
        if sched is None or len(sched) == 0:
            continue
        sched = sched.copy()
        sched["home_team"] = sched["home_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        sched["away_team"] = sched["away_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        # Only games with final scores
        played = sched.dropna(subset=["home_score", "away_score"])
        played = played.sort_values(["week", "gameday"])

        by_week = played.groupby("week", sort=True)
        for week_num, week_games in by_week:
            for _, g in week_games.iterrows():
                home = g["home_team"]; away = g["away_team"]
                if not home or not away:
                    continue
                home_score = int(g["home_score"]); away_score = int(g["away_score"])
                # Detect neutral-site games (Super Bowl, international); not strict
                neutral = bool(g.get("location") == "Neutral") if "location" in week_games.columns else False
                home_new, away_new = update_rating(
                    ratings[home], ratings[away], home_score - away_score, neutral_site=neutral,
                )
                ratings[home] = home_new
                ratings[away] = away_new
            _persist_week(db, season, int(week_num), dict(ratings))
            total_rows += len(ratings)

    log.info("elo_history_rebuilt", seasons=seasons, rows=total_rows)
    return total_rows


# ---- Public reads ----------------------------------------------------------


def current_ratings(db: Session, season: int | None = None) -> dict[str, float]:
    """Returns { team_id: latest rating }, latest within `season` (or overall)."""
    q = db.query(TeamEloRating)
    if season is not None:
        q = q.filter(TeamEloRating.season == season)
    rows = q.all()
    if not rows:
        return {}
    # Pick the row with the highest (season, week) per team
    latest: dict[str, TeamEloRating] = {}
    for r in rows:
        cur = latest.get(r.team_id)
        if cur is None or (r.season, r.week) > (cur.season, cur.week):
            latest[r.team_id] = r
    return {t: r.rating for t, r in latest.items()}


def rating_at(db: Session, team_id: str, season: int, week: int) -> float | None:
    row = (
        db.query(TeamEloRating)
        .filter_by(team_id=team_id, season=season, week=week)
        .one_or_none()
    )
    return row.rating if row else None


def rating_history(db: Session, team_id: str, seasons: list[int] | None = None) -> list[dict[str, Any]]:
    q = db.query(TeamEloRating).filter(TeamEloRating.team_id == team_id)
    if seasons:
        q = q.filter(TeamEloRating.season.in_(seasons))
    rows = q.order_by(TeamEloRating.season.asc(), TeamEloRating.week.asc()).all()
    return [
        {"season": r.season, "week": r.week, "rating": round(r.rating, 1)}
        for r in rows
    ]


# ---- Letter grade ----------------------------------------------------------


def rating_to_grade(rating: float) -> str:
    """Approximate a casual-fan-friendly letter grade from rating."""
    if rating >= 1660: return "A+"
    if rating >= 1620: return "A"
    if rating >= 1580: return "A-"
    if rating >= 1550: return "B+"
    if rating >= 1520: return "B"
    if rating >= 1490: return "B-"
    if rating >= 1460: return "C+"
    if rating >= 1430: return "C"
    if rating >= 1400: return "C-"
    if rating >= 1370: return "D"
    return "F"
