"""Typed metric rows for SQL leaderboards (Phase D)."""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from sqlalchemy import asc, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.player_metric_value import PlayerMetricValue
from ..models.team_metric_value import TeamMetricValue
from . import analytics_service, materialize_service

log = get_logger(__name__)

EntityKind = Literal["team", "player"]
SortBy = Literal["percentile", "value"]
Order = Literal["asc", "desc"]


def team_metric_names() -> list[str]:
    return [name for name, _, _ in analytics_service.TEAM_METRICS]


def player_metric_names(position: str | None = None) -> list[str]:
    pos = (position or "").upper()
    if pos and pos in analytics_service.POSITION_METRICS:
        return [name for name, _ in analytics_service.POSITION_METRICS[pos]]
    names: list[str] = []
    for defs in analytics_service.POSITION_METRICS.values():
        for name, _ in defs:
            if name not in names:
                names.append(name)
    return names


async def sync_team_metric_index(db: Session, season: int) -> int:
    """Populate team_metric_values from materialized PBP aggregates."""
    aggs = materialize_service.load_team_aggregates(db, season)
    if not aggs:
        aggs = await analytics_service.compute_team_pbp_aggregates(season)
    if not aggs:
        return 0

    n = 0
    for metric, _, higher in analytics_service.TEAM_METRICS:
        distribution = [
            row.get(metric) for row in aggs.values() if row.get(metric) is not None
        ]
        for team_id, row in aggs.items():
            if not team_id:
                continue
            raw = row.get(metric)
            try:
                value = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and isinstance(value, float) and np.isnan(value):
                value = None
            pct = analytics_service.percentile_rank(value, distribution, higher)
            stmt = pg_insert(TeamMetricValue).values(
                team_id=team_id,
                season=season,
                metric=metric,
                value=value,
                percentile=pct,
                higher_is_better=higher,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_team_metric",
                set_={
                    "value": stmt.excluded.value,
                    "percentile": stmt.excluded.percentile,
                    "higher_is_better": stmt.excluded.higher_is_better,
                },
            )
            db.execute(stmt)
            n += 1
    db.commit()
    log.info("metric_index_teams", season=season, rows=n)
    return n


async def sync_player_metric_index(db: Session, season: int) -> int:
    """Populate player_metric_values from materialized seasonal stats."""
    df = materialize_service.load_player_dataframe(db, season)
    if df is None or len(df) == 0:
        df = await analytics_service.build_seasonal_player_dataframe(season)
    if df is None or len(df) == 0:
        return 0

    name_col = "player_display_name" if "player_display_name" in df.columns else "player_name"
    n = 0
    for pos, metric_defs in analytics_service.POSITION_METRICS.items():
        sub = df[df["position"] == pos] if "position" in df.columns else df.iloc[0:0]
        if len(sub) == 0:
            continue
        mins = analytics_service.POSITION_MIN_SAMPLES.get(pos, {})
        for col, threshold in mins.items():
            if col in sub.columns:
                sub = sub[sub[col].fillna(0) >= threshold]

        pct_tables: dict[str, np.ndarray] = {}
        for metric, _ in metric_defs:
            if metric in sub.columns:
                arr = pd.to_numeric(sub[metric], errors="coerce").to_numpy()
                pct_tables[metric] = arr[~np.isnan(arr)]

        for _, row in sub.iterrows():
            pid = row.get("player_id")
            if not pid:
                continue
            pid = str(pid)
            display = row.get(name_col)
            display = (
                str(display)
                if display is not None and not (isinstance(display, float) and np.isnan(display))
                else None
            )
            team = row.get("team")
            team = str(team) if team is not None and str(team) not in ("nan", "") else None

            for metric, higher in metric_defs:
                if metric not in row.index:
                    continue
                raw = row.get(metric)
                try:
                    value = float(raw) if raw is not None else None
                except (TypeError, ValueError):
                    value = None
                if value is not None and isinstance(value, float) and np.isnan(value):
                    value = None
                dist = pct_tables.get(metric, np.array([]))
                pct = analytics_service.percentile_rank(value, dist, higher)
                stmt = pg_insert(PlayerMetricValue).values(
                    player_id=pid,
                    season=season,
                    position=pos,
                    team_id=team,
                    display_name=display,
                    metric=metric,
                    value=value,
                    percentile=pct,
                    higher_is_better=higher,
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_player_metric",
                    set_={
                        "position": stmt.excluded.position,
                        "team_id": stmt.excluded.team_id,
                        "display_name": stmt.excluded.display_name,
                        "value": stmt.excluded.value,
                        "percentile": stmt.excluded.percentile,
                        "higher_is_better": stmt.excluded.higher_is_better,
                    },
                )
                db.execute(stmt)
                n += 1
    db.commit()
    log.info("metric_index_players", season=season, rows=n)
    return n


async def sync_metric_index(db: Session, seasons: list[int] | None = None) -> dict[str, int]:
    from ..utils.seasons import current_or_upcoming_season, latest_completed_season

    if seasons is None:
        latest = latest_completed_season()
        upcoming = current_or_upcoming_season()
        seasons = sorted({latest, upcoming})
    team_rows = 0
    player_rows = 0
    for s in seasons:
        team_rows += await sync_team_metric_index(db, s)
        player_rows += await sync_player_metric_index(db, s)
    return {"team_rows": team_rows, "player_rows": player_rows, "seasons": len(seasons)}


def query_leaders(
    db: Session,
    *,
    season: int,
    metric: str,
    entity: EntityKind = "team",
    position: str | None = None,
    team_id: str | None = None,
    sort_by: SortBy = "percentile",
    limit: int = 25,
    order: Order = "desc",
    min_value: float | None = None,
) -> dict[str, Any]:
    """Filter/sort leaders from typed metric tables."""
    limit = max(1, min(limit, 100))
    metric = metric.strip()
    order_fn = desc if order == "desc" else asc

    if entity == "team":
        if metric not in team_metric_names():
            return {"error": f"unknown team metric: {metric}", "leaders": []}
        col = TeamMetricValue.percentile if sort_by == "percentile" else TeamMetricValue.value
        q = (
            select(TeamMetricValue)
            .where(TeamMetricValue.season == season, TeamMetricValue.metric == metric)
            .where(TeamMetricValue.value.isnot(None))
        )
        if min_value is not None:
            q = q.where(TeamMetricValue.value >= min_value)
        q = q.order_by(order_fn(col).nullslast()).limit(limit)
        rows = db.execute(q).scalars().all()
        leaders = [
            {
                "team_id": r.team_id,
                "metric": r.metric,
                "value": r.value,
                "percentile": r.percentile,
                "higher_is_better": r.higher_is_better,
            }
            for r in rows
        ]
        return {"entity": "team", "season": season, "metric": metric, "sort_by": sort_by, "leaders": leaders}

    pos = position.upper() if position else None
    allowed = player_metric_names(pos)
    if metric not in allowed:
        return {"error": f"unknown player metric for position={pos or 'any'}: {metric}", "leaders": []}
    col = PlayerMetricValue.percentile if sort_by == "percentile" else PlayerMetricValue.value
    q = (
        select(PlayerMetricValue)
        .where(PlayerMetricValue.season == season, PlayerMetricValue.metric == metric)
        .where(PlayerMetricValue.value.isnot(None))
    )
    if pos:
        q = q.where(PlayerMetricValue.position == pos)
    if team_id:
        q = q.where(PlayerMetricValue.team_id == team_id.upper())
    if min_value is not None:
        q = q.where(PlayerMetricValue.value >= min_value)
    q = q.order_by(order_fn(col).nullslast()).limit(limit)
    rows = db.execute(q).scalars().all()
    leaders = [
        {
            "player_id": r.player_id,
            "display_name": r.display_name,
            "position": r.position,
            "team_id": r.team_id,
            "metric": r.metric,
            "value": r.value,
            "percentile": r.percentile,
            "higher_is_better": r.higher_is_better,
        }
        for r in rows
    ]
    return {
        "entity": "player",
        "season": season,
        "metric": metric,
        "position": pos,
        "sort_by": sort_by,
        "leaders": leaders,
    }


def index_status(db: Session, season: int | None = None) -> list[dict[str, Any]]:
    seasons: list[int]
    if season is not None:
        seasons = [season]
    else:
        ts = db.execute(select(TeamMetricValue.season).distinct()).scalars().all()
        ps = db.execute(select(PlayerMetricValue.season).distinct()).scalars().all()
        seasons = sorted(set(ts) | set(ps), reverse=True)
    out = []
    for s in seasons:
        tc = len(db.execute(
            select(TeamMetricValue).where(TeamMetricValue.season == s)
        ).scalars().all())
        pc = len(db.execute(
            select(PlayerMetricValue).where(PlayerMetricValue.season == s)
        ).scalars().all())
        out.append({"season": s, "team_metric_rows": tc, "player_metric_rows": pc})
    return out
