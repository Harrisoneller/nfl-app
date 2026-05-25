"""Feature store v1: snapshot write/read helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.feature_snapshot import FeatureSnapshot

log = get_logger(__name__)


def default_feature_set_version(season: int) -> str:
    return f"season-{season}-v1"


def upsert_snapshot(
    db: Session,
    *,
    season: int,
    week: int | None,
    game_id: str | None,
    entity_id: str,
    feature_set_version: str,
    model_version: str,
    snapshot: dict[str, Any],
    source: str,
) -> None:
    row = (
        db.query(FeatureSnapshot)
        .filter(
            FeatureSnapshot.season == season,
            FeatureSnapshot.week == week,
            FeatureSnapshot.game_id == game_id,
            FeatureSnapshot.entity_id == entity_id,
            FeatureSnapshot.feature_set_version == feature_set_version,
            FeatureSnapshot.model_version == model_version,
        )
        .first()
    )
    if row is None:
        row = FeatureSnapshot(
            season=season,
            week=week,
            game_id=game_id,
            entity_id=entity_id,
            feature_set_version=feature_set_version,
            model_version=model_version,
            source=source,
            snapshot=snapshot,
            captured_at=datetime.now(timezone.utc),
        )
        db.add(row)
    else:
        row.snapshot = snapshot
        row.source = source
        row.captured_at = datetime.now(timezone.utc)


def store_game_feature_frame(
    db: Session,
    *,
    frame: pd.DataFrame,
    season: int,
    model_version: str,
    feature_set_version: str,
    source: str,
) -> dict[str, int]:
    """Persist one snapshot row per game + side (home/away)."""
    if frame is None or len(frame) == 0:
        return {"written": 0}

    written = 0
    try:
        for _, row in frame.iterrows():
            game_id = str(row.get("game_id") or "")
            week = _safe_int(row.get("week_number"))
            for side in ("home", "away"):
                entity = row.get(f"{side}_team")
                if not entity:
                    continue
                snapshot = {
                    "game_id": game_id,
                    "season": season,
                    "week": week,
                    "side": side,
                    "features": {
                        "elo": _safe_float(row.get(f"{side}_elo")),
                        "off_epa": _safe_float(row.get(f"{side}_off_epa")),
                        "off_epa_recency": _safe_float(row.get(f"{side}_off_epa_recency")),
                        "def_epa": _safe_float(row.get(f"{side}_def_epa")),
                        "def_epa_recency": _safe_float(row.get(f"{side}_def_epa_recency")),
                        "rest_days": _safe_float(row.get(f"rest_days_{side}")),
                        "short_week": _safe_int(row.get(f"short_week_{side}")),
                        "qb_change_proxy": _safe_float(row.get(f"qb_change_proxy_{side}")),
                        "offense_continuity_proxy": _safe_float(row.get(f"offense_continuity_{side}")),
                        "ol_injury_proxy": _safe_float(row.get(f"ol_injury_proxy_{side}")),
                        "is_division_game": _safe_int(row.get("is_division_game")),
                        "travel_proxy": _safe_float(row.get("away_travel_proxy")) if side == "away" else 0.0,
                        "weather_class_code": _safe_int(row.get("weather_class_code")),
                        "weather_is_bad": _safe_int(row.get("weather_is_bad")),
                        "market_spread_home": _safe_float(row.get("market_spread_home")),
                        "market_home_win_prob": _safe_float(row.get("market_home_win_prob")),
                        "market_available": _safe_int(row.get("market_available")),
                    },
                }
                upsert_snapshot(
                    db,
                    season=season,
                    week=week,
                    game_id=game_id or None,
                    entity_id=str(entity),
                    feature_set_version=feature_set_version,
                    model_version=model_version,
                    snapshot=snapshot,
                    source=source,
                )
                written += 1
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        # Feature store is additive in v1; don't block predictions if unavailable.
        log.warning("feature_store_write_failed", error=str(e)[:200], source=source, season=season)
        return {"written": 0}
    return {"written": written}


def get_snapshots(
    db: Session,
    *,
    season: int | None = None,
    week: int | None = None,
    game_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    q = db.query(FeatureSnapshot)
    if season is not None:
        q = q.filter(FeatureSnapshot.season == season)
    if week is not None:
        q = q.filter(FeatureSnapshot.week == week)
    if game_id:
        q = q.filter(FeatureSnapshot.game_id == game_id)
    if entity_id:
        q = q.filter(FeatureSnapshot.entity_id == entity_id)
    rows = q.order_by(FeatureSnapshot.captured_at.desc()).limit(limit).all()
    return [
        {
            "season": r.season,
            "week": r.week,
            "game_id": r.game_id,
            "entity_id": r.entity_id,
            "feature_set_version": r.feature_set_version,
            "model_version": r.model_version,
            "source": r.source,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            "snapshot": r.snapshot,
        }
        for r in rows
    ]


def _safe_float(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None or pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
