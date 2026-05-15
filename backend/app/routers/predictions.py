"""Predictions API — Elo + ML, game-level + season-level."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import elo_service, ml_predictions_service, predictions_service
from ..utils.seasons import current_or_upcoming_season, latest_completed_season

router = APIRouter()


@router.get("/games")
async def games(
    season: int | None = None,
    week: int | None = None,
    include_ml: bool = True,
    db: Session = Depends(get_db),
):
    """Predicted spread + win prob for each game in a week.

    Defaults to the next upcoming week. Returns both Elo and (optionally)
    XGBoost ML predictions side-by-side.
    """
    season = season or current_or_upcoming_season()
    base = await predictions_service.predict_week(db, season, week)
    if include_ml and base["week"] is not None:
        ml = await ml_predictions_service.predict_week_ml(db, season, base["week"])
        if ml.get("available"):
            ml_by_id = {g["game_id"]: g for g in ml["games"]}
            for g in base["games"]:
                ml_g = ml_by_id.get(g["id"])
                if ml_g:
                    g["ml_prediction"] = {
                        "predicted_spread": ml_g["predicted_spread"],
                        "predicted_home_margin": ml_g["predicted_home_margin"],
                    }
    return base


@router.get("/teams/{team_id}/season")
async def team_season(
    team_id: str,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Predicted record, division/playoff/SB odds for one team."""
    tid = team_id.upper()
    outlook = await predictions_service.team_season_outlook(db, tid, season)
    cur = elo_service.current_ratings(db).get(tid, elo_service.INITIAL_RATING)
    return {
        **outlook,
        "current_elo": round(cur, 1),
        "grade": elo_service.rating_to_grade(cur),
    }


@router.get("/teams/{team_id}/elo-history")
def elo_history(team_id: str, seasons: str | None = None, db: Session = Depends(get_db)):
    """Per-week Elo for one team across one or more seasons.

    `seasons` is a comma-separated list (e.g. "2022,2023,2024"). Default: all.
    """
    season_list = None
    if seasons:
        try:
            season_list = [int(s) for s in seasons.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(400, "seasons must be a comma-separated list of integers")
    return {
        "team_id": team_id.upper(),
        "history": elo_service.rating_history(db, team_id.upper(), season_list),
    }


@router.get("/standings/projected")
async def projected_standings(season: int | None = None, db: Session = Depends(get_db)):
    return await predictions_service.projected_standings(db, season)


@router.get("/elo/current")
def current_ratings(season: int | None = None, db: Session = Depends(get_db)):
    """All 32 teams' current Elo, sorted high to low. Casual users love this view."""
    ratings = elo_service.current_ratings(db, season=season)
    rows = [
        {
            "team_id": t,
            "rating": round(r, 1),
            "grade": elo_service.rating_to_grade(r),
        }
        for t, r in ratings.items()
    ]
    rows.sort(key=lambda x: -x["rating"])
    return {"ratings": rows}


@router.post("/admin/elo/rebuild")
async def rebuild_elo(seasons: str | None = None, db: Session = Depends(get_db)):
    """Recompute Elo from scratch for the given seasons (comma-separated).

    Defaults to last 6 seasons. Run this once after first deploy, or whenever
    you want a clean rebuild.
    """
    if seasons:
        season_list = [int(s) for s in seasons.split(",") if s.strip()]
    else:
        latest = latest_completed_season()
        season_list = list(range(latest - 5, latest + 1))
    rows = await elo_service.rebuild_history(db, season_list)
    return {"seasons": season_list, "rows_written": rows}


@router.post("/admin/ml/train")
async def train_ml(seasons: str | None = None, db: Session = Depends(get_db)):
    """Train the XGBoost margin model. Defaults to last 4 seasons."""
    if seasons:
        season_list = [int(s) for s in seasons.split(",") if s.strip()]
    else:
        latest = latest_completed_season()
        season_list = list(range(latest - 3, latest + 1))
    return await ml_predictions_service.train(db, season_list)
