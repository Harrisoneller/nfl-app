"""Admin-triggered model reruns — recompute game + player outputs on demand.

Why this exists
---------------
Tuning a parameter is only half the job; the numbers on the site have to move.
Most already do automatically:

* **Player boards / leaderboards** embed a compound param+override *version
  token* in their cache keys (see ``overrides_service.version``), so any param
  write busts them and the next read recomputes. They're live within seconds.
* **Game predictions** (``predict_week``) recompute per request and read param
  values live, so market-blend / prior / weather / injury / output-pin changes
  are also picked up on the next read.

Two things do **not** self-heal after a tuning change, and that's the gap this
service closes:

1. **Monte-Carlo season sim** (``monte_carlo_sim`` artifact) is cached 24h and
   is *not* param-versioned — playoff / division / Super-Bowl odds stay stale
   until the TTL lapses or the nightly cron runs. A rerun evicts it (L1 **and**
   L2) and recomputes.
2. **Elo ratings** are only rebuilt by the batch job. K-factor, home-field, and
   Elo↔spread-conversion params therefore don't move spreads until a rebuild.
   The ``full`` scope rebuilds Elo (and derived profiles) before rewarming.

Beyond correctness, a rerun *warms* the L1/L2 caches so the first public
visitor after a tuning session gets fast, already-fresh numbers instead of
eating a cold 10-25s compute.

Execution model
---------------
Reruns can take from seconds (``quick``) to minutes (``full``: Elo over six
seasons + 10k-sim Monte Carlo). They run in the **background** as an asyncio
task tracked by a ``DataSyncRun`` row (domain ``model_rerun``) so the HTTP
request returns immediately and the web role never risks a request timeout. A
process-level guard allows only one rerun at a time.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger

log = get_logger(__name__)

# Scope → human label. `quick` is the default post-tuning action.
SCOPES: dict[str, str] = {
    "quick": "Quick recompute (invalidate season sim, rewarm games + players)",
    "games": "Games only (season sim + game slate)",
    "players": "Players only (projection board + leaderboard)",
    "full": "Full rebuild (Elo + profiles, then games + players)",
}

# Default Monte-Carlo key uses n=10_000 (see predictions_service.simulate_season).
_MC_KIND = "monte_carlo_sim"
_MC_N = 10_000

# Single-flight guard. Only one rerun runs at a time across the process; a
# second trigger while one is active is rejected with a clear "busy" signal.
_active: dict[str, Any] | None = None
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Step bodies (each returns a short "key=value" fragment for the run message)
# --------------------------------------------------------------------------- #


async def _evict_season_sim(db, season: int) -> str:
    from . import artifact_cache

    n = artifact_cache.evict(db, _MC_KIND, f"season:{season}:n:{_MC_N}")
    return f"mc_evicted={n}"


async def _rewarm_games(db, season: int, week: int | None) -> str:
    """Recompute the game slate and the (now-evicted) season simulation."""
    from . import predictions_service

    games = await predictions_service.predict_week(db, season, week)
    sim = await predictions_service.simulate_season(db, season)
    return (
        f"games={len(games.get('games', []))} "
        f"sim_teams={len(sim.get('teams', {}))}"
    )


async def _rewarm_players(db, season: int, week: int | None) -> str:
    from . import player_predictions_service

    board = await player_predictions_service.weekly_projection_board(
        db, season=season, week=week,
    )
    board_n = board.get("count") or len(board.get("players", []))
    lb = await player_predictions_service.projection_leaderboard(db, season=season)
    lb_n = lb.get("count") or len(lb.get("players", []))
    return f"board={board_n} leaderboard={lb_n}"


async def _rebuild_elo_and_profiles(db, season: int) -> str:
    """The heavy part of a full rerun: rebuild Elo history + derived profiles.

    Mirrors the nightly derive pipeline so K-factor / home-field / spread-
    conversion tuning actually lands on future spreads.
    """
    from ..utils.seasons import current_or_upcoming_season, latest_completed_season
    from . import analytics_service, elo_service

    latest = latest_completed_season()
    elo_rows = await elo_service.rebuild_history(db, list(range(latest - 5, latest + 1)))

    upcoming = current_or_upcoming_season()
    prof_seasons = [latest] if latest == upcoming else [latest, upcoming]
    prof = await analytics_service.build_derived_profiles(prof_seasons)
    return f"elo_rows={elo_rows} profiles_teams={prof.get('teams', 0)}"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


async def _perform(db, scope: str, season: int, week: int | None) -> str:
    parts: list[str] = [f"scope={scope}", f"season={season}"]
    if week is not None:
        parts.append(f"week={week}")

    if scope == "full":
        parts.append(await _rebuild_elo_and_profiles(db, season))

    if scope in ("quick", "games", "full"):
        parts.append(await _evict_season_sim(db, season))
        parts.append(await _rewarm_games(db, season, week))

    if scope in ("quick", "players", "full"):
        parts.append(await _rewarm_players(db, season, week))

    return "; ".join(parts)


async def _run_bg(run_id: int, scope: str, season: int, week: int | None) -> None:
    """Background task body: execute the rerun and finalize its DataSyncRun."""
    global _active
    from ..db import SessionLocal
    from ..models.data_sync_run import DataSyncRun
    from . import sync_run_service

    db = SessionLocal()
    try:
        run = db.get(DataSyncRun, run_id)
        if run is None:  # pragma: no cover — defensive
            return
        try:
            message = await _perform(db, scope, season, week)
            sync_run_service.finish(db, run, status="ok", message=message)
            log.info("model_rerun_done", run_id=run_id, scope=scope, message=message)
        except Exception as e:  # noqa: BLE001
            sync_run_service.finish(db, run, status="error", message=str(e)[:4000])
            log.warning("model_rerun_failed", run_id=run_id, scope=scope, error=str(e)[:200])
    finally:
        db.close()
        _active = None


async def trigger(
    scope: str,
    *,
    actor: str = "",
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Kick off a background rerun. Returns immediately with the run id.

    Raises ``ValueError`` on an unknown scope and ``RuntimeError`` if a rerun
    is already in flight (the router maps these to 422 / 409).
    """
    global _active
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope '{scope}'. Valid: {', '.join(SCOPES)}")

    from ..db import SessionLocal
    from ..utils.seasons import current_or_upcoming_season
    from . import sync_run_service

    season = season or current_or_upcoming_season()

    async with _lock:
        if _active is not None:
            raise RuntimeError(
                f"A rerun is already running (scope={_active.get('scope')}, "
                f"run_id={_active.get('run_id')}). Wait for it to finish."
            )
        db = SessionLocal()
        try:
            run = sync_run_service.begin(db, "model_rerun", season=season)
            run_id = run.id
        finally:
            db.close()
        _active = {
            "run_id": run_id,
            "scope": scope,
            "actor": actor,
            "season": season,
            "week": week,
            "started_at": _now().isoformat(),
        }

    asyncio.create_task(_run_bg(run_id, scope, season, week))
    log.info("model_rerun_started", run_id=run_id, scope=scope, actor=actor, season=season)
    return {
        "status": "started",
        "run_id": run_id,
        "scope": scope,
        "label": SCOPES[scope],
        "season": season,
        "week": week,
    }


def status(db, *, limit: int = 15) -> dict[str, Any]:
    """Current running rerun (if any) + recent rerun history for the UI poll."""
    from sqlalchemy import desc, select

    from ..models.data_sync_run import DataSyncRun
    from . import sync_run_service

    rows = (
        db.execute(
            select(DataSyncRun)
            .where(DataSyncRun.domain == "model_rerun")
            .order_by(desc(DataSyncRun.started_at))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "running": _active is not None,
        "active": _active,
        "scopes": SCOPES,
        "runs": [sync_run_service._serialize(r) for r in rows],
    }
