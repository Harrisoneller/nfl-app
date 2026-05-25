"""Lightweight experiment assignment + event aggregation."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models.experiment_event import ExperimentEvent

EXPERIMENTS: dict[str, dict[str, Any]] = {
    "insight_card_order_v1": {
        "variants": [
            {"key": "control", "weight": 50},
            {"key": "confidence_first", "weight": 50},
        ],
        "enabled": True,
    }
}


def assign_variant(experiment_key: str, session_id: str) -> dict[str, Any]:
    config = EXPERIMENTS.get(experiment_key)
    if not config or not config.get("enabled"):
        return {"experiment_key": experiment_key, "variant": "control", "enabled": False}
    bucket = _bucket(session_id=session_id, experiment_key=experiment_key)
    cumulative = 0
    for variant in config["variants"]:
        cumulative += int(variant["weight"])
        if bucket < cumulative:
            return {
                "experiment_key": experiment_key,
                "variant": variant["key"],
                "enabled": True,
                "bucket": bucket,
            }
    fallback = config["variants"][-1]["key"]
    return {"experiment_key": experiment_key, "variant": fallback, "enabled": True, "bucket": bucket}


def record_events(db: Session, events: list[dict[str, Any]]) -> dict[str, Any]:
    inserted = 0
    for e in events:
        exp_key = str(e.get("experiment_key") or "").strip()
        variant = str(e.get("variant") or "").strip()
        event_type = str(e.get("event_type") or "").strip()
        session_id = str(e.get("session_id") or "").strip()
        if not exp_key or not variant or not event_type or not session_id:
            continue
        db.add(
            ExperimentEvent(
                experiment_key=exp_key,
                variant=variant,
                event_type=event_type,
                session_id=session_id,
                page=str(e.get("page") or "unknown"),
                card_key=(str(e["card_key"]) if e.get("card_key") else None),
                payload=(e.get("payload") if isinstance(e.get("payload"), dict) else {}),
            )
        )
        inserted += 1
    db.commit()
    return {"inserted": inserted}


def report(
    db: Session,
    *,
    experiment_key: str,
    days: int = 7,
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=max(1, days))
    stmt = select(ExperimentEvent).where(
        and_(
            ExperimentEvent.experiment_key == experiment_key,
            ExperimentEvent.created_at >= since,
        )
    )
    rows = db.execute(stmt).scalars().all()
    by_variant: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_variant[r.variant][r.event_type] += 1

    variants: list[dict[str, Any]] = []
    for variant, counts in sorted(by_variant.items()):
        impressions = counts.get("impression", 0)
        clicks = counts.get("click", 0)
        returns = counts.get("return", 0)
        variants.append(
            {
                "variant": variant,
                "events": dict(counts),
                "ctr": round(clicks / impressions, 4) if impressions else 0.0,
                "return_rate": round(returns / impressions, 4) if impressions else 0.0,
            }
        )
    return {
        "experiment_key": experiment_key,
        "days": days,
        "since": since.isoformat(),
        "total_events": len(rows),
        "variants": variants,
    }


def _bucket(*, session_id: str, experiment_key: str) -> int:
    digest = hashlib.sha256(f"{experiment_key}:{session_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100
