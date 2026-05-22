"""Persist nflverse outputs to Postgres (Phase B).

Worker jobs call `sync_*` to materialize data. Analytics reads DB first via
`load_*` helpers so user requests avoid parquet / PBP loads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.player_season_stat import PlayerSeasonStat
from ..models.team_season_aggregate import TeamSeasonAggregate
from ..utils.seasons import current_or_upcoming_season, latest_completed_season

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.floating, np.integer)):
        return float(value) if isinstance(value, np.floating) else int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _row_to_stats_dict(row: pd.Series) -> dict[str, Any]:
    skip = {"player_id", "position", "team", "player_display_name", "player_name"}
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in skip or k.startswith("Unnamed"):
            continue
        safe = _json_safe(v)
        if safe is not None:
            out[k] = safe
    return out


def load_team_aggregates(db: Session, season: int) -> dict[str, dict[str, float]] | None:
    """All team metrics for a season from DB, or None if not materialized."""
    rows = db.execute(
        select(TeamSeasonAggregate).where(TeamSeasonAggregate.season == season)
    ).scalars().all()
    if not rows:
        return None
    return {r.team_id: dict(r.metrics) for r in rows}


def load_player_dataframe(db: Session, season: int) -> pd.DataFrame | None:
    """Reconstruct enriched seasonal table from materialized rows."""
    rows = db.execute(
        select(PlayerSeasonStat).where(PlayerSeasonStat.season == season)
    ).scalars().all()
    if not rows:
        return None
    records: list[dict[str, Any]] = []
    for r in rows:
        rec = {"player_id": r.player_id, "position": r.position, "team": r.team_id}
        if r.display_name:
            rec["player_display_name"] = r.display_name
        rec.update(r.stats or {})
        records.append(rec)
    df = pd.DataFrame(records)
    if "player_id" in df.columns:
        df = df.set_index("player_id", drop=False)
    return df


async def sync_team_season_aggregates(db: Session, season: int) -> int:
    """Compute PBP aggregates and upsert into team_season_aggregates."""
    from . import analytics_service

    aggs = await analytics_service.compute_team_pbp_aggregates(season)
    if not aggs:
        log.warning("materialize_team_aggs_empty", season=season)
        return 0

    now = _now()
    n = 0
    for team_id, metrics in aggs.items():
        if not team_id:
            continue
        clean = {k: _json_safe(v) for k, v in metrics.items() if _json_safe(v) is not None}
        stmt = pg_insert(TeamSeasonAggregate).values(
            team_id=team_id,
            season=season,
            metrics=clean,
            synced_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_team_season_agg",
            set_={"metrics": stmt.excluded.metrics, "synced_at": stmt.excluded.synced_at},
        )
        db.execute(stmt)
        n += 1
    db.commit()
    log.info("materialize_team_aggs", season=season, teams=n)
    return n


async def sync_player_season_stats(db: Session, season: int) -> int:
    """Build enriched seasonal frame and upsert into player_season_stats."""
    from . import analytics_service

    df = await analytics_service.build_seasonal_player_dataframe(season)
    if df is None or len(df) == 0:
        log.warning("materialize_player_stats_empty", season=season)
        return 0

    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    now = _now()
    n = 0
    for _, row in df.iterrows():
        pid = row.get("player_id")
        if not pid or (isinstance(pid, float) and np.isnan(pid)):
            continue
        pid = str(pid)
        display = row.get(name_col)
        display = str(display) if display is not None and not (isinstance(display, float) and np.isnan(display)) else None
        pos = row.get("position")
        pos = str(pos).upper() if pos is not None and str(pos) not in ("nan", "") else None
        team = row.get("team")
        team = str(team) if team is not None and str(team) not in ("nan", "") else None

        stmt = pg_insert(PlayerSeasonStat).values(
            player_id=pid,
            season=season,
            position=pos,
            team_id=team,
            display_name=display,
            stats=_row_to_stats_dict(row),
            synced_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_player_season",
            set_={
                "position": stmt.excluded.position,
                "team_id": stmt.excluded.team_id,
                "display_name": stmt.excluded.display_name,
                "stats": stmt.excluded.stats,
                "synced_at": stmt.excluded.synced_at,
            },
        )
        db.execute(stmt)
        n += 1
    db.commit()
    log.info("materialize_player_stats", season=season, players=n)
    return n


async def materialize_seasons(db: Session, seasons: list[int] | None = None) -> dict[str, int]:
    """Ingest team aggregates + player stats for each season (sequential)."""
    if seasons is None:
        latest = latest_completed_season()
        upcoming = current_or_upcoming_season()
        seasons = sorted({latest, upcoming})
    teams_total = 0
    players_total = 0
    for s in seasons:
        teams_total += await sync_team_season_aggregates(db, s)
        players_total += await sync_player_season_stats(db, s)
    return {"teams": teams_total, "players": players_total, "seasons": len(seasons)}


def materialization_status(db: Session, season: int | None = None) -> list[dict[str, Any]]:
    """Row counts per season for admin."""
    seasons = [season] if season is not None else None
    if seasons is None:
        player_seasons = db.execute(
            select(PlayerSeasonStat.season).distinct().order_by(PlayerSeasonStat.season.desc())
        ).scalars().all()
        team_seasons = db.execute(
            select(TeamSeasonAggregate.season).distinct().order_by(TeamSeasonAggregate.season.desc())
        ).scalars().all()
        seasons = sorted(set(player_seasons) | set(team_seasons), reverse=True)

    out: list[dict[str, Any]] = []
    for s in seasons:
        pc = db.execute(
            select(PlayerSeasonStat).where(PlayerSeasonStat.season == s)
        ).scalars().all()
        tc = db.execute(
            select(TeamSeasonAggregate).where(TeamSeasonAggregate.season == s)
        ).scalars().all()
        last_sync = None
        for r in pc + tc:
            if last_sync is None or r.synced_at > last_sync:
                last_sync = r.synced_at
        out.append({
            "season": s,
            "player_rows": len(pc),
            "team_rows": len(tc),
            "last_synced_at": last_sync.isoformat() if last_sync else None,
        })
    return out
