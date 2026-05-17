"""APScheduler bootstrap.

Runs in the same process as FastAPI. Each job opens its own DB session
and calls a service method. Intervals come from settings.

Startup jobs (run-once on boot, scheduled as `date` triggers a few seconds out):
    - Seed teams
    - Sync Sleeper player metadata
    - Refresh current scoreboard
    - Refresh last 6 seasons' schedules (powers team schedule pages)

Recurring jobs (intervals from settings):
    - Scores  : SCHEDULE_SCORES_SECONDS
    - News    : SCHEDULE_NEWS_SECONDS
    - Odds    : SCHEDULE_ODDS_SECONDS
    - Players : every 24h
    - Sched   : every 24h (for live-season updates)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import get_settings
from ..db import SessionLocal
from ..logging_config import get_logger
from ..services import (
    analytics_service,
    awards_service,
    elo_service,
    news_service,
    odds_service,
    players_service,
    predictions_service,
    scores_service,
    teams_service,
)
from ..utils.seasons import available_seasons, current_or_upcoming_season, latest_completed_season

log = get_logger(__name__)
_scheduler: AsyncIOScheduler | None = None


# --------------------------------------------------------------------------- #
# Job bodies
# --------------------------------------------------------------------------- #


async def _job_seed_teams() -> None:
    db = SessionLocal()
    try:
        added = teams_service.ensure_seeded(db)
        if added:
            log.info("scheduler_seeded_teams", added=added)
    finally:
        db.close()


async def _job_sync_players() -> None:
    db = SessionLocal()
    try:
        n = await players_service.sync_from_sleeper(db)
        log.info("scheduler_synced_players", count=n)
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_players_failed", error=str(e))
    finally:
        db.close()


async def _job_sync_schedules() -> None:
    db = SessionLocal()
    try:
        total = 0
        for season in available_seasons():
            try:
                total += await scores_service.refresh_season_schedule(db, season)
            except Exception as e:  # noqa: BLE001
                log.warning("scheduler_schedule_season_failed", season=season, error=str(e))
        log.info("scheduler_synced_schedules", games=total)
    finally:
        db.close()


async def _job_rebuild_elo() -> None:
    """Compute Elo for the last 6 seasons. Idempotent; runs once after boot."""
    db = SessionLocal()
    try:
        latest = latest_completed_season()
        await elo_service.rebuild_history(db, list(range(latest - 5, latest + 1)))
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_elo_rebuild_failed", error=str(e))
    finally:
        db.close()


async def _job_warmup_predictions() -> None:
    """Pre-run the heavy Monte Carlo and award computations so cache is hot.

    First /standings/projected request after a cold start does ~10k sims;
    pre-warming during boot moves that cost off the user's request path.
    """
    db = SessionLocal()
    try:
        await predictions_service.simulate_season(db, current_or_upcoming_season())
        await awards_service.award_leaderboards()
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_predictions_warmup_failed", error=str(e))
    finally:
        db.close()


async def _job_warmup_analytics() -> None:
    """Pre-warm analytics caches for the two most recent completed seasons.

    Without this, the first user to load a team or player page eats the
    PBP download (~30s for 2 seasons). With it, the work happens during
    boot while you're still spinning up the frontend.
    """
    latest = latest_completed_season()
    try:
        await analytics_service.warmup([latest, latest - 1])
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_warmup_failed", error=str(e))


async def _job_refresh_scores() -> None:
    db = SessionLocal()
    try:
        await scores_service.refresh_scoreboard(db)
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_scores_failed", error=str(e))
    finally:
        db.close()


async def _job_refresh_news() -> None:
    db = SessionLocal()
    try:
        await news_service.refresh_news(db)
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_news_failed", error=str(e))
    finally:
        db.close()


async def _job_refresh_odds() -> None:
    db = SessionLocal()
    try:
        await odds_service.refresh_odds(db)
    except Exception as e:  # noqa: BLE001
        log.warning("scheduler_odds_failed", error=str(e))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    sched = AsyncIOScheduler(timezone="UTC")

    # ---- Startup one-shots (staggered so we don't hammer at once) ----
    sched.add_job(_job_seed_teams, "date", run_date=_soon(1))
    sched.add_job(_job_sync_players, "date", run_date=_soon(3))
    sched.add_job(_job_sync_schedules, "date", run_date=_soon(6))
    sched.add_job(_job_warmup_analytics, "date", run_date=_soon(10))
    # Elo rebuild after analytics warmup — uses schedules already loaded by warmup
    sched.add_job(_job_rebuild_elo, "date", run_date=_soon(45))
    # Predictions cache warmup after Elo is built
    sched.add_job(_job_warmup_predictions, "date", run_date=_soon(75))
    # Weekly re-run of Monte Carlo + awards in case rosters/standings shift
    sched.add_job(
        _job_warmup_predictions,
        IntervalTrigger(hours=24),
        next_run_time=_soon(60 * 60 * 24),
        id="predictions_daily", coalesce=True, max_instances=1,
    )
    # Weekly recompute during the season
    sched.add_job(
        _job_rebuild_elo,
        IntervalTrigger(hours=24 * 7),
        next_run_time=_soon(60 * 60 * 24 * 7),
        id="elo_weekly", coalesce=True, max_instances=1,
    )

    # ---- Recurring ----
    sched.add_job(
        _job_refresh_scores,
        IntervalTrigger(seconds=settings.schedule_scores_seconds),
        next_run_time=_soon(8),
        id="scores", coalesce=True, max_instances=1,
    )
    sched.add_job(
        _job_refresh_news,
        IntervalTrigger(seconds=settings.schedule_news_seconds),
        next_run_time=_soon(12),
        id="news", coalesce=True, max_instances=1,
    )
    sched.add_job(
        _job_refresh_odds,
        IntervalTrigger(seconds=settings.schedule_odds_seconds),
        next_run_time=_soon(18),
        id="odds", coalesce=True, max_instances=1,
    )
    sched.add_job(
        _job_sync_players,
        IntervalTrigger(hours=24),
        next_run_time=_soon(60 * 60 * 24),  # ~24h after boot
        id="players_daily", coalesce=True, max_instances=1,
    )
    sched.add_job(
        _job_sync_schedules,
        IntervalTrigger(hours=24),
        next_run_time=_soon(60 * 60 * 24 + 60),
        id="schedules_daily", coalesce=True, max_instances=1,
    )

    sched.start()
    _scheduler = sched
    log.info(
        "scheduler_started",
        scores_s=settings.schedule_scores_seconds,
        news_s=settings.schedule_news_seconds,
        odds_s=settings.schedule_odds_seconds,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("scheduler_stopped")


def _soon(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
