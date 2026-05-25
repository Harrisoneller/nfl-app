"""Data freshness contracts + SLA status for core modules."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.data_sync_run import DataSyncRun

log = get_logger(__name__)

# Module freshness contracts (seconds). Keep additive and easy to tune.
FRESHNESS_CONTRACTS = {
    "odds": {"domains": ["odds"], "sla_seconds": 60 * 60 * 8},
    "predictions": {"domains": ["derive_pipeline", "predictions_warmup"], "sla_seconds": 60 * 60 * 24},
    "news": {"domains": ["news"], "sla_seconds": 60 * 60},
    "scores": {"domains": ["scores"], "sla_seconds": 60 * 5},
    "players": {"domains": ["players"], "sla_seconds": 60 * 60 * 24 * 3},
}


def freshness_status(age_seconds: int | None, sla_seconds: int) -> str:
    if age_seconds is None:
        return "stale"
    if age_seconds <= sla_seconds:
        return "ok"
    if age_seconds <= int(sla_seconds * 1.5):
        return "warn"
    return "stale"


def freshness_snapshot(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    modules: list[dict[str, Any]] = []

    for module, contract in FRESHNESS_CONTRACTS.items():
        domains = contract["domains"]
        sla_seconds = int(contract["sla_seconds"])
        latest = (
            db.query(func.max(DataSyncRun.finished_at))
            .filter(DataSyncRun.domain.in_(domains), DataSyncRun.status == "ok")
            .scalar()
        )
        if latest is not None and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_seconds = int((now - latest).total_seconds()) if latest else None
        status = freshness_status(age_seconds, sla_seconds)
        item = {
            "module": module,
            "domains": domains,
            "last_updated_at": latest.isoformat() if latest else None,
            "age_seconds": age_seconds,
            "sla_seconds": sla_seconds,
            "status": status,
        }
        modules.append(item)
        if status == "stale":
            log.warning(
                "freshness_sla_breached",
                module=module,
                domains=domains,
                age_seconds=age_seconds,
                sla_seconds=sla_seconds,
            )

    return {
        "generated_at": now.isoformat(),
        "modules": modules,
    }
