"""Manual data refresh + cache-introspection endpoints. Useful while
developing or after a deploy that needs a warm cache."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..config import get_settings
from ..services import (
    analytics_service,
    artifact_cache,
    endpoint_slo_service,
    experiment_service,
    feature_store_service,
    materialize_service,
    metrics_index_service,
    news_service,
    odds_service,
    players_service,
    scores_service,
    sync_run_service,
    teams_service,
)
from ..utils.seasons import available_seasons, current_or_upcoming_season

router = APIRouter()


@router.post("/refresh/teams")
def refresh_teams(db: Session = Depends(get_db)):
    return {"added": teams_service.ensure_seeded(db)}


@router.post("/refresh/scores")
async def refresh_scores(db: Session = Depends(get_db)):
    return {"events": await scores_service.refresh_scoreboard(db)}


@router.post("/refresh/schedules")
async def refresh_schedules(
    db: Session = Depends(get_db),
    season: int | None = None,
):
    """Pull full-season schedules from nflverse into the Game table.

    Defaults to all dropdown seasons (same as the boot scheduler). Pass
    `?season=2026` to refresh a single year.
    """
    seasons = [season] if season is not None else available_seasons()
    total = 0
    for s in seasons:
        total += await scores_service.refresh_season_schedule(db, s)
    return {
        "games_upserted": total,
        "seasons": seasons,
        "current_or_upcoming": current_or_upcoming_season(),
    }


@router.post("/refresh/players")
async def refresh_players(db: Session = Depends(get_db)):
    return {"players": await players_service.sync_from_sleeper(db)}


@router.post("/refresh/news")
async def refresh_news(db: Session = Depends(get_db)):
    return {"items": await news_service.refresh_news(db)}


@router.post("/refresh/metric-index")
async def refresh_metric_index(
    db: Session = Depends(get_db),
    season: int | None = None,
):
    """Rebuild typed metric rows for SQL leaderboards (Phase D)."""
    seasons = [season] if season is not None else None
    return await metrics_index_service.sync_metric_index(db, seasons)


@router.post("/refresh/materialize")
async def refresh_materialize(
    db: Session = Depends(get_db),
    season: int | None = None,
):
    """Pull nflverse seasonal + PBP aggregates into Postgres (Phase B)."""
    seasons = [season] if season is not None else None
    return await materialize_service.materialize_seasons(db, seasons)


@router.post("/refresh/profiles")
async def refresh_profiles(db: Session = Depends(get_db), season: int | None = None):
    """Rebuild team/player profile artifacts from materialized data."""
    from ..utils.seasons import latest_completed_season

    if season is not None:
        seasons = [season]
    else:
        latest = latest_completed_season()
        upcoming = current_or_upcoming_season()
        seasons = [latest] if latest == upcoming else [latest, upcoming]
    return await analytics_service.build_derived_profiles(seasons)


@router.get("/materialization-status")
def materialization_status(db: Session = Depends(get_db), season: int | None = None):
    return {"seasons": materialize_service.materialization_status(db, season)}


@router.post("/refresh/odds")
async def refresh_odds(force: bool = True, db: Session = Depends(get_db)):
    """Manual odds pull. Defaults to force=True (a hand-triggered refresh is
    intentional) — bypasses the min-interval/offseason guards. Pass
    `?force=false` to respect them (e.g. for a cron-like external trigger)."""
    return await odds_service.refresh_odds(db, force=force)


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


@router.get("/sync-status")
def sync_status(db: Session = Depends(get_db), limit: int = 3):
    """Last ingest/derive job runs per domain (worker scheduler)."""
    s = get_settings()
    return {
        "app_role": s.app_role,
        "scheduler_enabled": s.scheduler_enabled,
        "boot_warmup_level": s.boot_warmup_level,
        "derive_cron_hours_utc": s.derive_cron_hour_list,
        "h2h_cron_hours_utc": s.h2h_cron_hour_list,
        "materialization": materialize_service.materialization_status(db),
        "metric_index": metrics_index_service.index_status(db),
        "cache_backend": s.cache_backend,
        "runs": sync_run_service.last_runs(db, limit_per_domain=limit),
    }


@router.get("/upstream-status")
def upstream_status():
    """Live snapshot of the nfl-data-py circuit breaker.

    Useful when pages are unexpectedly empty: an open circuit means we've
    fast-failed recent requests for a given (fn, season) due to repeated
    upstream errors. Cooldown is in seconds.
    """
    from ..adapters.data.nfl_data_py_adapter import circuit_breaker_status
    return circuit_breaker_status()


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


@router.get("/feature-store/snapshots")
def feature_store_snapshots(
    db: Session = Depends(get_db),
    season: int | None = None,
    week: int | None = None,
    game_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
):
    """Inspect persisted train/serve feature snapshots."""
    rows = feature_store_service.get_snapshots(
        db,
        season=season,
        week=week,
        game_id=game_id,
        entity_id=entity_id,
        limit=limit,
    )
    return {"count": len(rows), "rows": rows}


@router.get("/slo/endpoints")
def slo_endpoints(window_seconds: int = 900):
    """Current rolling per-endpoint latency/cache snapshot."""
    return endpoint_slo_service.current_snapshot(window_seconds=window_seconds)


@router.post("/slo/flush")
def slo_flush(db: Session = Depends(get_db), window_seconds: int = 900):
    """Persist current in-memory SLO stats for deploy history."""
    return endpoint_slo_service.flush_snapshot(db, window_seconds=window_seconds)


@router.get("/slo/history")
def slo_history(
    db: Session = Depends(get_db),
    endpoint: str | None = None,
    limit: int = 200,
):
    return {
        "rows": endpoint_slo_service.recent_history(
            db,
            endpoint=endpoint,
            limit=limit,
        )
    }


@router.get("/experiments/report")
def experiment_report(
    experiment_key: str = "insight_card_order_v1",
    days: int = 7,
    db: Session = Depends(get_db),
):
    """CTR + retention proxy report by variant."""
    return experiment_service.report(db, experiment_key=experiment_key, days=days)
