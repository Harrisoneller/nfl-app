"""Persist outcomes of scheduled ingest / derive jobs."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.data_sync_run import DataSyncRun

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def begin(db: Session, domain: str, *, season: int | None = None) -> DataSyncRun:
    run = DataSyncRun(
        domain=domain,
        season=season,
        status="running",
        started_at=_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish(
    db: Session,
    run: DataSyncRun,
    *,
    status: str,
    rows_affected: int | None = None,
    message: str | None = None,
) -> None:
    run.status = status
    run.finished_at = _now()
    run.rows_affected = rows_affected
    run.message = (message or "")[:4000] or None
    db.commit()


def _rows_from_result(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        for key in ("lines", "lines_in_db", "games_upserted", "count", "rows", "pairs", "deleted"):
            if key in result and isinstance(result[key], int):
                return result[key]
        if "added" in result and isinstance(result["added"], int):
            return result["added"]
    return None


async def run_job(
    domain: str,
    job: Callable[[Session], Awaitable[Any]],
    *,
    season: int | None = None,
) -> Any:
    """Open a session, log start/finish, invoke `job(db)`."""
    from ..db import SessionLocal

    db = SessionLocal()
    run = begin(db, domain, season=season)
    try:
        result = await job(db)
        finish(
            db,
            run,
            status="ok",
            rows_affected=_rows_from_result(result),
            message=_status_message(result),
        )
        return result
    except Exception as e:  # noqa: BLE001
        finish(db, run, status="error", message=str(e)[:4000])
        log.warning("sync_job_failed", domain=domain, error=str(e)[:200])
        raise
    finally:
        db.close()


def _status_message(result: Any) -> str | None:
    if isinstance(result, dict) and result.get("message"):
        msg = str(result["message"])
        status = result.get("status")
        if status:
            return f"{status}: {msg}" if status not in msg else msg
        return msg
    return None


def last_runs(db: Session, *, limit_per_domain: int = 1) -> list[dict[str, Any]]:
    """Most recent run per domain (for admin / readiness)."""
    domains = list(
        db.scalars(
            select(DataSyncRun.domain).distinct().order_by(DataSyncRun.domain)
        ).all()
    )
    out: list[dict[str, Any]] = []
    for domain in domains:
        rows = (
            db.execute(
                select(DataSyncRun)
                .where(DataSyncRun.domain == domain)
                .order_by(desc(DataSyncRun.started_at))
                .limit(limit_per_domain)
            )
            .scalars()
            .all()
        )
        for r in rows:
            out.append(_serialize(r))
    out.sort(key=lambda x: x["started_at"] or "", reverse=True)
    return out


def _serialize(r: DataSyncRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "domain": r.domain,
        "season": r.season,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "rows_affected": r.rows_affected,
        "message": r.message,
    }
