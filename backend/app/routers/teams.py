from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.game import GameOut
from ..schemas.news import NewsItemOut
from ..schemas.player import PlayerOut
from ..schemas.team import TeamOut
from ..services import analytics_service, news_service, players_service, scores_service, teams_service, weather_service
from ..utils.seasons import current_or_upcoming_season, latest_completed_season

router = APIRouter()


@router.get("", response_model=list[TeamOut])
def list_teams(db: Session = Depends(get_db)):
    teams_service.ensure_seeded(db)
    return teams_service.list_teams(db)


@router.get("/{team_id}", response_model=TeamOut)
def get_team(team_id: str, db: Session = Depends(get_db)):
    t = teams_service.get_team(db, team_id)
    if not t:
        raise HTTPException(404, "team not found")
    return t


@router.get("/{team_id}/roster", response_model=list[PlayerOut])
def get_roster(team_id: str, db: Session = Depends(get_db)):
    return players_service.get_team_roster(db, team_id)


@router.get("/{team_id}/schedule", response_model=list[GameOut])
def get_schedule(
    team_id: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    season = season or latest_completed_season()
    return scores_service.list_team_schedule(db, team_id.upper(), season)


@router.get("/{team_id}/profile")
async def get_team_profile(team_id: str, season: int | None = None):
    season = season or latest_completed_season()
    return await analytics_service.team_profile(team_id.upper(), season)


@router.get("/{team_id}/trend")
async def get_team_trend(
    team_id: str,
    metric: str = Query(..., description="Metric key, e.g. off_epa_per_play"),
    start: int | None = None,
    end: int | None = None,
):
    end = end or latest_completed_season()
    start = start or (end - 4)
    seasons = list(range(start, end + 1))
    return await analytics_service.team_trend(team_id.upper(), seasons, metric)


@router.get("/{team_id}/news")
async def get_team_news(
    team_id: str,
    limit: int = 25,
    include_subreddit: bool = True,
    db: Session = Depends(get_db),
):
    """Combined team feed: tagged headlines + team subreddit hot.

    Returns items sorted newest-first with `source_label` set to either an
    RSS feed name, 'r/nfl' (when tagged), or the team-specific subreddit.
    """
    tid = team_id.upper()
    db_items = news_service.list_news(db, limit=limit, team_id=tid)
    out = [_news_dict(n) for n in db_items]
    if include_subreddit:
        try:
            reddit_items = await news_service.fetch_team_reddit(tid, limit=20)
            out.extend(reddit_items)
        except Exception:  # noqa: BLE001
            pass
    # Sort by published_at desc (None last)
    out.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return out[:limit]


@router.get("/{team_id}/upcoming")
async def get_upcoming_season(team_id: str):
    """Preview information for the current/upcoming season.

    Returns the team's upcoming schedule with opponent-strength annotations
    pulled from the previous completed season's analytics.
    """
    tid = team_id.upper()
    upcoming = current_or_upcoming_season()
    previous = upcoming - 1

    # Pull the schedule from nfl-data-py (it's released in May)
    sched_rows = await _team_upcoming_schedule(tid, upcoming)

    # Build per-opponent rating from the previous season's profile
    prev_aggs = await analytics_service._team_pbp_aggregates(previous, allow_live_fallback=False)
    opponents = []
    for g in sched_rows:
        opp = g["away_team_id"] if g["home_team_id"] == tid else g["home_team_id"]
        opp_metrics = (prev_aggs.get(opp) or {}) if opp else {}
        opponents.append({
            **g,
            "opponent": opp,
            "opponent_prev_off_epa": opp_metrics.get("off_epa_per_play"),
            "opponent_prev_def_epa": opp_metrics.get("def_epa_per_play"),
            "opponent_prev_points_per_game": opp_metrics.get("points_per_game"),
        })

    # Weather enrichment (free via Open-Meteo)
    try:
        forecasts = await weather_service.forecasts_for_games(opponents)
        for g in opponents:
            g["weather"] = forecasts.get(g["id"], {"available": False})
    except Exception:  # noqa: BLE001
        for g in opponents:
            g["weather"] = {"available": False}

    # Strength of schedule = average opponent off+def EPA
    sos_off = _safe_mean([o["opponent_prev_off_epa"] for o in opponents])
    sos_def = _safe_mean([o["opponent_prev_def_epa"] for o in opponents])

    return {
        "team_id": tid,
        "season": upcoming,
        "is_upcoming": True,
        "previous_season": previous,
        "schedule": opponents,
        "strength_of_schedule": {
            "avg_opponent_off_epa": sos_off,
            "avg_opponent_def_epa": sos_def,
            "n_games": len(opponents),
        },
    }


async def _team_upcoming_schedule(team_id: str, season: int) -> list[dict]:
    """Pull a team's schedule directly from nfl-data-py for an upcoming season."""
    from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
    from ..utils.teams import canonical_team

    adapter = NflDataPyAdapter()
    df = await adapter.schedules_df(season)
    if df is None or len(df) == 0:
        return []
    df = df.copy()
    df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    df = df[(df["home_team"] == team_id) | (df["away_team"] == team_id)]
    df = df.sort_values("week")

    out = []
    for _, row in df.iterrows():
        out.append({
            "id": str(row.get("game_id") or ""),
            "season": int(season),
            "week": _safe_int(row.get("week")),
            "home_team_id": row.get("home_team"),
            "away_team_id": row.get("away_team"),
            "gameday": str(row.get("gameday") or ""),
            "venue": str(row.get("stadium") or ""),
            "network": str(row.get("network") or ""),
        })
    return out


def _safe_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _safe_mean(values: list) -> float | None:
    vs = [v for v in values if isinstance(v, (int, float))]
    if not vs:
        return None
    return round(sum(vs) / len(vs), 4)


def _news_dict(n) -> dict:
    return {
        "id": n.id, "source": n.source, "source_label": n.source_label,
        "title": n.title, "summary": n.summary, "link": n.link, "author": n.author,
        "image_url": n.image_url,
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "team_tags": n.team_tags,
    }
