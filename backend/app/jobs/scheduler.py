"""APScheduler bootstrap (worker role only).

Phase B/C layout:
- **Boot (minimal):** seed, players, current schedule, news — no PBP at boot.
- **Derive cron (06:15 + 18:15 UTC):** schedules → materialize nflverse → elo
  → profiles → MC → awards.
- **H2H cron (03:30 UTC nightly):** prewarm matchups only.
- **Live (Sep–Feb):** ESPN scores on an interval.

Set `APP_ROLE=web` on the public Railway service; `APP_ROLE=worker` on a
second service. See docs/DEPLOY.md.
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
    endpoint_slo_service,
    elo_service,
    h2h_service,
    materialize_service,
    metrics_index_service,
    model_lifecycle_service,
    news_service,
    odds_service,
    players_service,
    predictions_service,
    scores_service,
    sparky_service,
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
# Job bodies
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


async def _materialize_nflverse(db: Session) -> dict[str, int]:
    return await materialize_service.materialize_seasons(db)


async def _build_profiles(db: Session) -> dict[str, int]:
    latest = latest_completed_season()
    upcoming = current_or_upcoming_season()
    seasons = [latest] if latest == upcoming else [latest, upcoming]
    return await analytics_service.build_derived_profiles(seasons)


async def _warmup_predictions(db: Session) -> dict[str, str]:
    season = current_or_upcoming_season()
    await predictions_service.simulate_season(db, season)
    await awards_service.award_leaderboards()
    return {"status": "ok", "season": str(season)}


async def _warmup_h2h(db: Session) -> int:
    return await h2h_service.prewarm_h2h(db)


async def _refresh_scores(db: Session) -> int:
    n = await scores_service.refresh_scoreboard(db)
    # Best-effort: settle any newly-final games into Sparky historical accuracy.
    # This is what makes the accuracy dashboard reflect real outcomes over time.
    try:
        res = sparky_service.settle_sparky_results(db, lookback_days=10)
        if res.get("settled_picks") or res.get("settled_parlays"):
            log.info("sparky_auto_settled", **{k: res[k] for k in ("settled_picks", "settled_parlays", "skipped")})
    except Exception as e:  # noqa: BLE001
        log.warning("sparky_auto_settle_failed", error=str(e)[:160])
    return n


async def _refresh_news(db: Session) -> int:
    return await news_service.refresh_news(db)


async def _refresh_odds(db: Session) -> dict:
    result = await odds_service.refresh_odds(db)
    # After odds (and their snapshots) are refreshed, rebuild Sparky's slate so
    # predictions/signals/parlays stay in sync with the latest lines. Best-effort:
    # never let a Sparky error fail the odds job.
    try:
        slate = await sparky_service.build_slate(db)
        result = {**result, "sparky_slate_games": slate.get("count", 0)}
    except Exception as e:  # noqa: BLE001
        log.warning("sparky_slate_build_failed", error=str(e)[:160])
    return result


async def _sync_metric_index(db: Session) -> dict[str, int]:
    return await metrics_index_service.sync_metric_index(db)


async def _run_model_lifecycle(db: Session) -> dict[str, str]:
    result = await model_lifecycle_service.run_weekly_lifecycle(db)
    return {"status": str(result.get("status", "ok"))}


async def _flush_endpoint_slo(db: Session) -> dict[str, int]:
    return endpoint_slo_service.flush_snapshot(db, window_seconds=15 * 60)


async def _daily_derive_pipeline(db: Session) -> dict[str, str]:
    """schedules → materialize → metric index → elo → profiles → MC → awards → H2H."""
    parts: list[str] = []
    n_sched = await _sync_schedules_all(db)
    parts.append(f"schedules={n_sched}")
    mat = await _materialize_nflverse(db)
    parts.append(f"materialize_teams={mat.get('teams', 0)}")
    parts.append(f"materialize_players={mat.get('players', 0)}")
    idx = await _sync_metric_index(db)
    parts.append(f"metric_index_teams={idx.get('team_rows', 0)}")
    parts.append(f"metric_index_players={idx.get('player_rows', 0)}")
    n_elo = await _rebuild_elo(db)
    parts.append(f"elo_rows={n_elo}")
    prof = await _build_profiles(db)
    parts.append(f"profiles_teams={prof.get('teams', 0)}")
    pred = await _warmup_predictions(db)
    parts.append(f"predictions={pred.get('season', 'ok')}")
    h2h = await _warmup_h2h(db)
    parts.append(f"h2h_pairs={h2h}")
    return {"status": "ok", "message": "; ".join(parts)}


# --------------------------------------------------------------------------- #
# Scheduler entrypoints
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


async def _job_nightly_h2h() -> None:
    await sync_run_service.run_job("h2h_prewarm", _warmup_h2h)


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


async def _job_materialize_only() -> None:
    await sync_run_service.run_job("materialize", _materialize_nflverse)


async def _job_profiles_only() -> None:
    await sync_run_service.run_job("profiles", _build_profiles)


async def _job_model_lifecycle() -> None:
    await sync_run_service.run_job("model_lifecycle", _run_model_lifecycle)


async def _job_endpoint_slo_snapshot() -> None:
    await sync_run_service.run_job("endpoint_slo_snapshot", _flush_endpoint_slo)


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

    if boot != "none":
        sched.add_job(_job_seed_teams, "date", run_date=_soon(1), id="boot_teams")
        sched.add_job(_job_sync_players, "date", run_date=_soon(3), id="boot_players")
        sched.add_job(
            _job_sync_schedules_current, "date", run_date=_soon(6), id="boot_schedules_current",
        )
        sched.add_job(_job_refresh_news, "date", run_date=_soon(12), id="boot_news")

    if boot == "full":
        sched.add_job(_job_materialize_only, "date", run_date=_soon(20), id="boot_materialize")
        sched.add_job(_job_rebuild_elo_boot, "date", run_date=_soon(45), id="boot_elo")
        sched.add_job(_job_profiles_only, "date", run_date=_soon(60), id="boot_profiles")
        sched.add_job(_job_warmup_predictions_boot, "date", run_date=_soon(75), id="boot_predictions")
        sched.add_job(_job_nightly_h2h, "date", run_date=_soon(90), id="boot_h2h")

    sched.add_job(
        _job_daily_derive,
        CronTrigger(hour=settings.derive_cron_hours_expr, minute=15, timezone="UTC"),
        id="derive_pipeline",
        coalesce=True,
        max_instances=1,
    )

    sched.add_job(
        _job_nightly_h2h,
        CronTrigger(hour=settings.h2h_cron_hours_expr, minute=30, timezone="UTC"),
        id="h2h_nightly",
        coalesce=True,
        max_instances=1,
    )

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
    sched.add_job(
        _job_model_lifecycle,
        CronTrigger(day_of_week="mon", hour=7, minute=45, timezone="UTC"),
        id="model_lifecycle_weekly",
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        _job_endpoint_slo_snapshot,
        IntervalTrigger(minutes=5),
        next_run_time=_soon(60 * 3),
        id="endpoint_slo_snapshot",
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
        h2h_cron_utc=settings.h2h_cron_hours_expr,
    )


async def _job_rebuild_elo_boot() -> None:
    await sync_run_service.run_job("elo_rebuild", _rebuild_elo)


async def _job_warmup_predictions_boot() -> None:
    await sync_run_service.run_job("predictions_warmup", _warmup_predictions)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("scheduler_stopped")


def _soon(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
