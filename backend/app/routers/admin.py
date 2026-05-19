"""Manual data refresh + cache-introspection endpoints. Useful while
developing or after a deploy that needs a warm cache."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import artifact_cache, news_service, odds_service, players_service, scores_service, teams_service

router = APIRouter()


@router.post("/refresh/teams")
def refresh_teams(db: Session = Depends(get_db)):
    return {"added": teams_service.ensure_seeded(db)}


@router.post("/refresh/scores")
async def refresh_scores(db: Session = Depends(get_db)):
    return {"events": await scores_service.refresh_scoreboard(db)}


@router.post("/refresh/players")
async def refresh_players(db: Session = Depends(get_db)):
    return {"players": await players_service.sync_from_sleeper(db)}


@router.post("/refresh/news")
async def refresh_news(db: Session = Depends(get_db)):
    return {"items": await news_service.refresh_news(db)}


@router.post("/refresh/odds")
async def refresh_odds(db: Session = Depends(get_db)):
    return {"lines": await odds_service.refresh_odds(db)}


# ============================================================================
# Persistent model-cache introspection + maintenance
# ============================================================================


@router.get("/cache/stats")
def cache_stats():
    """Per-kind row counts + freshness for the model_artifacts L2 cache."""
    return artifact_cache.stats()


@router.post("/cache/vacuum")
def cache_vacuum(older_than_days: int = 7):
    """Delete artifacts that expired more than N days ago."""
    return {"deleted": artifact_cache.vacuum_expired(older_than_days)}


@router.post("/cache/invalidate")
def cache_invalidate(kind: str, key: str | None = None, db: Session = Depends(get_db)):
    """Force-recompute on next read."""
    return {"deleted": artifact_cache.invalidate(db, kind, key)}


@router.get("/data-availability")
async def data_availability():
    """Diagnostic: which seasons currently have data from nflverse.

    Use this when player or team pages are unexpectedly empty — it tells you
    whether the upstream data source has what we're asking for. Returns a row
    per season + dataset with the row count (or null if the fetch failed).
    """
    from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
    from ..utils.seasons import latest_completed_season
    adapter = NflDataPyAdapter()
    latest = latest_completed_season()
    seasons = list(range(latest - 4, latest + 2))  # last 5 + upcoming
    out = []
    for s in seasons:
        row = {"season": s}
        try:
            df = await adapter.schedules_df(s)
            row["schedules_rows"] = int(len(df)) if df is not None else None
        except Exception:  # noqa: BLE001
            row["schedules_rows"] = None
        try:
            df = await adapter.weekly_df(s)
            row["weekly_rows"] = int(len(df)) if df is not None else None
        except Exception:  # noqa: BLE001
            row["weekly_rows"] = None
        try:
            df = await adapter.seasonal_df(s)
            row["seasonal_rows"] = int(len(df)) if df is not None else None
        except Exception:  # noqa: BLE001
            row["seasonal_rows"] = None
        out.append(row)
    return {"seasons": out, "latest_completed": latest}
