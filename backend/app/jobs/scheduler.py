"""APScheduler bootstrap (worker role only).

Phase A layout:
- **Boot (minimal):** seed teams, Sleeper players, current-season schedule, news.
  No PBP / Elo / Monte Carlo / H2H at boot (avoids Railway OOM crash-loops).
- **Derive cron (default 06:00 + 18:00 UTC):** full schedule sync, analytics,
  Elo, predictions, H2H — 1–2×/day, aligned with nflverse cadence.
- **Live interval (Sep–Feb only):** ESPN scoreboard.
- **Always on worker:** news, odds cron, daily player sync.

Set `APP_ROLE=web` on the public Railway service; `APP_ROLE=worker` on a
second service (same repo, same DATABASE_URL). See docs/DEPLOY.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging_config import get_logger
from ..services import (
    analytics_service,
    artifact_cache,
    awards_service,
    elo_service,
    h2h_service,
    news_service,
    odds_service,
    players_service,
    predictions_service,
    scores_service,
    sync_run_service,
    teams_service,
)
from ..utils.seasons import (
    available_seasons,
    current_or_upcoming_season,
    is_nfl_live_period,
    latest_completed_season,
)

log = get_logger(__name__)
_scheduler: AsyncIOScheduler | None = None


# --------------------------------------------------------------------------- #
# Job bodies (accept an open Session — wrapped by sync_run_service.run_job)
# --------------------------------------------------------------------------- #


async def _seed_teams(db: Session) -> int:
    return teams_service.ensure_seeded(db)


async def _sync_players(db: Session) -> int:
    return await players_service.sync_from_sleeper(db)


async def _sync_schedules_all(db: Session) -> int:
    total = 0
    for season in available_seasons():
        try:
            total += await scores_service.refresh_season_schedule(db, season)
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler_schedule_season_failed", season=season, error=str(e))
    return total


async def _sync_schedules_current(db: Session) -> int:
    season = current_or_upcoming_season()
    return await scores_service.refresh_season_schedule(db, season)


async def _rebuild_elo(db: Session) -> int:
    latest = latest_completed_season()
    return await elo_service.rebuild_history(db, list(range(latest - 5, latest + 1)))


async def _vacuum_cache(_db: Session) -> int:
    return artifact_cache.vacuum_expired(older_than_days=7)


async def _warmup_predictions(db: Session) -> dict[str, str]:
    season = current_or_upcoming_season()
    await predictions_service.simulate_season(db, season)
    await awards_service.award_leaderboards()
    return {"status": "ok", "season": str(season)}


async def _warmup_h2h(db: Session) -> int:
    return await h2h_service.prewarm_h2h(db)


async def _warmup_analytics(_db: Session) -> int:
    latest = latest_completed_season()
    await analytics_service.warmup([latest])
    return 1


async def _refresh_scores(db: Session) -> int:
    return await scores_service.refresh_scoreboard(db)


async def _refresh_news(db: Session) -> int:
    return await news_service.refresh_news(db)


async def _refresh_odds(db: Session) -> dict:
    return await odds_service.refresh_odds(db)


async def _daily_derive_pipeline(db: Session) -> dict[str, str]:
    """Heavy chain: schedules → analytics → elo → predictions → h2h."""
    parts: list[str] = []
    n_sched = await _sync_schedules_all(db)
    parts.append(f"schedules={n_sched}")
    await _warmup_analytics(db)
    parts.append("analytics=ok")
    n_elo = await _rebuild_elo(db)
    parts.append(f"elo_rows={n_elo}")
    pred = await _warmup_predictions(db)
    parts.append(f"predictions={pred.get('season', 'ok')}")
    n_h2h = await _warmup_h2h(db)
    parts.append(f"h2h_pairs={n_h2h}")
    return {"status": "ok", "message": "; ".join(parts)}


# --------------------------------------------------------------------------- #
# Scheduler entrypoints (logging wrappers)
# --------------------------------------------------------------------------- #


async def _job_seed_teams() -> None:
    await sync_run_service.run_job("teams_seed", _seed_teams)


async def _job_sync_players() -> None:
    await sync_run_service.run_job("players", _sync_players)


async def _job_sync_schedules() -> None:
    await sync_run_service.run_job("schedules", _sync_schedules_all)


async def _job_sync_schedules_current() -> None:
    season = current_or_upcoming_season()
    await sync_run_service.run_job("schedules_current", _sync_schedules_current, season=season)


async def _job_daily_derive() -> None:
    await sync_run_service.run_job("derive_pipeline", _daily_derive_pipeline)


async def _job_vacuum_cache() -> None:
    await sync_run_service.run_job("cache_vacuum", _vacuum_cache)


async def _job_refresh_scores() -> None:
    await sync_run_service.run_job("scores", _refresh_scores)


async def _job_refresh_news() -> None:
    await sync_run_service.run_job("news", _refresh_news)


async def _job_refresh_odds() -> None:
    result = await sync_run_service.run_job("odds", _refresh_odds)
    if isinstance(result, dict):
        if result.get("status") in ("skipped_fresh", "skipped_offseason"):
            log.info("scheduler_odds_skipped", status=result["status"], message=result.get("message"))
        elif result.get("status") not in ("ok", "disabled") and result.get("lines_in_db", 0) == 0:
            log.warning(
                "scheduler_odds_empty",
                status=result.get("status"),
                message=result.get("message"),
            )


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    if not settings.scheduler_enabled:
        log.info("scheduler_not_started", reason="app_role is not worker")
        return

    sched = AsyncIOScheduler(timezone="UTC")
    live = is_nfl_live_period()
    boot = settings.boot_warmup_level

    # ---- Boot (minimal by default) -----------------------------------------
    if boot != "none":
        sched.add_job(_job_seed_teams, "date", run_date=_soon(1), id="boot_teams")
        sched.add_job(_job_sync_players, "date", run_date=_soon(3), id="boot_players")
        sched.add_job(
            _job_sync_schedules_current, "date", run_date=_soon(6), id="boot_schedules_current",
        )
        sched.add_job(_job_refresh_news, "date", run_date=_soon(12), id="boot_news")

    if boot == "full":
        # Local dev convenience — mirrors pre-Phase-A warmups (high memory).
        sched.add_job(_job_warmup_analytics, "date", run_date=_soon(20), id="boot_analytics")
        sched.add_job(_job_rebuild_elo_boot, "date", run_date=_soon(45), id="boot_elo")
        sched.add_job(_job_warmup_predictions_boot, "date", run_date=_soon(75), id="boot_predictions")
        sched.add_job(_job_warmup_h2h_boot, "date", run_date=_soon(90), id="boot_h2h")

    # ---- Derive pipeline (cron, not at boot) ---------------------------------
    sched.add_job(
        _job_daily_derive,
        CronTrigger(hour=settings.derive_cron_hours_expr, minute=15, timezone="UTC"),
        id="derive_pipeline",
        coalesce=True,
        max_instances=1,
    )

    # ---- Recurring ingest ----------------------------------------------------
    if live:
        sched.add_job(
            _job_refresh_scores,
            IntervalTrigger(seconds=settings.schedule_scores_seconds),
            next_run_time=_soon(8),
            id="scores",
            coalesce=True,
            max_instances=1,
        )
    else:
        log.info("scheduler_scores_disabled", reason="offseason")

    news_seconds = settings.schedule_news_seconds if live else 60 * 60 * 24
    sched.add_job(
        _job_refresh_news,
        IntervalTrigger(seconds=news_seconds),
        next_run_time=_soon(12),
        id="news",
        coalesce=True,
        max_instances=1,
    )

    sched.add_job(
        _job_refresh_odds,
        CronTrigger(hour=settings.odds_refresh_hours_utc, minute=0, timezone="UTC"),
        id="odds",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        _job_sync_players,
        IntervalTrigger(hours=24),
        next_run_time=_soon(60 * 60 * 6),
        id="players_daily",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        _job_sync_schedules,
        IntervalTrigger(hours=24),
        next_run_time=_soon(60 * 60 * 6 + 60),
        id="schedules_daily",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        _job_vacuum_cache,
        IntervalTrigger(hours=24 * 7),
        next_run_time=_soon(60 * 60 * 24),
        id="cache_vacuum_weekly",
        coalesce=True,
        max_instances=1,
    )

    sched.start()
    _scheduler = sched
    log.info(
        "scheduler_started",
        boot_warmup=boot,
        live_period=live,
        scores_s=settings.schedule_scores_seconds if live else None,
        news_s=news_seconds,
        odds_cron_utc=settings.odds_refresh_hours_utc,
        derive_cron_utc=settings.derive_cron_hours_expr,
    )


async def _job_warmup_analytics() -> None:
    await sync_run_service.run_job("analytics_warmup", _warmup_analytics)


async def _job_rebuild_elo_boot() -> None:
    await sync_run_service.run_job("elo_rebuild", _rebuild_elo)


async def _job_warmup_predictions_boot() -> None:
    await sync_run_service.run_job("predictions_warmup", _warmup_predictions)


async def _job_warmup_h2h_boot() -> None:
    await sync_run_service.run_job("h2h_warmup", _warmup_h2h)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("scheduler_stopped")


def _soon(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
