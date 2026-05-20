"""L2 persistent cache for expensive model outputs.

Two-layer pattern: in-process TTL cache (`app.cache`) is L1; this is L2
backed by Postgres. Services should call `get_or_compute` to seamlessly
get warm-cache behavior across restarts, deploys, and workers.

Patterns we use:
- **Completed-season immutable data** — written with `valid_until=None`,
  served forever. Examples: a 2023 team profile.
- **Current-season frequently-changing data** — written with a short
  `valid_until` (~24h). Examples: Monte Carlo sims, award leaderboards.
- **Frequently-recomputed but expensive things** — written with a medium
  TTL so a restart still benefits. Examples: backtest results (24h).

Failure modes are absorbed: if the DB is down or the artifact can't be
deserialized, we fall through to recompute. Cache reads should never
take down a request.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..cache import cache as l1_cache
from ..db import SessionLocal
from ..logging_config import get_logger
from ..models.model_artifact import ModelArtifact

log = get_logger(__name__)

T = TypeVar("T")

# Sentinel for "never expires"
NEVER: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# Synchronous primitives
# ============================================================================


def get(db: Session, kind: str, key: str) -> dict | None:
    """Return artifact payload if it exists and isn't expired, else None.

    Any error (missing table, DB unavailable, deserialization issue) returns
    None so the caller falls through to recompute cleanly.
    """
    try:
        row = db.execute(
            select(ModelArtifact).where(
                ModelArtifact.kind == kind, ModelArtifact.key == key,
            )
        ).scalar_one_or_none()
    except Exception as e:  # noqa: BLE001
        log.debug("artifact_get_failed", kind=kind, key=key, error=str(e)[:120])
        return None
    if row is None:
        return None
    if row.valid_until is not None and row.valid_until < _now():
        return None
    return row.payload


def set_(
    db: Session, kind: str, key: str, payload: dict | list,
    ttl_seconds: int | None = None,
) -> None:
    """Upsert an artifact. `ttl_seconds=None` means never expires."""
    valid_until = None if ttl_seconds is None else _now() + timedelta(seconds=ttl_seconds)
    # Ensure JSON-serializable
    try:
        json.dumps(payload, default=str)
    except (TypeError, ValueError) as e:
        log.warning("artifact_payload_not_serializable", kind=kind, key=key, error=str(e))
        return

    try:
        stmt = pg_insert(ModelArtifact).values(
            kind=kind, key=key, payload=payload, valid_until=valid_until,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["kind", "key"],
            set_=dict(payload=stmt.excluded.payload, valid_until=stmt.excluded.valid_until,
                      updated_at=_now()),
        )
        db.execute(stmt)
        db.commit()
    except Exception as e:  # noqa: BLE001
        # Catch missing-table or any other DB error so the caller still gets
        # its computed result. Cache failures should never bubble up.
        db.rollback()
        log.debug("artifact_set_failed", kind=kind, key=key, error=str(e)[:120])


def invalidate(db: Session, kind: str, key: str | None = None) -> int:
    """Delete artifacts. Pass `key=None` to clear an entire kind."""
    q = db.query(ModelArtifact).filter(ModelArtifact.kind == kind)
    if key is not None:
        q = q.filter(ModelArtifact.key == key)
    n = q.delete(synchronize_session=False)
    db.commit()
    return n


# ============================================================================
# Async two-layer pattern
# ============================================================================


# Singleflight: when N concurrent requests for the same (kind, key) arrive
# during a cold-cache window, only the first triggers compute; the rest await
# the same result. Without this, 8 simultaneous loads of the same team page
# would each kick off a 10k-sim Monte Carlo. With it, one runs and seven wait.
_inflight: dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


async def get_or_compute(
    kind: str,
    key: str,
    compute: Callable[[], Awaitable[Any]],
    ttl_seconds: int | None = None,
    l1_ttl_seconds: int = 300,
) -> Any:
    """Read-through cache: L1 in-process → L2 DB → recompute.

    Singleflighted: concurrent identical requests collapse to one compute.
    Fails open: corrupted artifact, DB unreachable, serialization issue
    all fall through to recompute and never cause a 500.
    """
    l1_key = f"artifact:{kind}:{key}"

    # L1 hit
    if (v := l1_cache.get(l1_key)) is not None:
        return v

    # L2 hit
    try:
        db = SessionLocal()
        try:
            cached = get(db, kind, key)
            if cached is not None:
                l1_cache.set(l1_key, cached, l1_ttl_seconds)
                return cached
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        log.warning("artifact_l2_read_failed", kind=kind, key=key, error=str(e)[:200])

    # Miss — but check if another coroutine is already computing this
    inflight_key = f"{kind}:{key}"
    async with _inflight_lock:
        existing = _inflight.get(inflight_key)
        if existing is not None:
            # Another request is computing this. Wait for it.
            log.debug("artifact_singleflight_wait", kind=kind, key=key)
            future = existing
        else:
            future = asyncio.get_event_loop().create_future()
            _inflight[inflight_key] = future

    if existing is not None:
        return await future  # type: ignore[possibly-unbound]

    # We're the chosen one — actually compute
    try:
        result = await compute()
    except Exception as e:
        # Wake any waiters with the same exception so they don't hang forever
        async with _inflight_lock:
            _inflight.pop(inflight_key, None)
        future.set_exception(e)
        raise

    # Write-through both layers (best-effort)
    if result is not None:
        l1_cache.set(l1_key, result, l1_ttl_seconds)
        try:
            db = SessionLocal()
            try:
                set_(db, kind, key, result, ttl_seconds=ttl_seconds)
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001
            log.warning("artifact_l2_write_failed", kind=kind, key=key, error=str(e)[:200])

    # Resolve the future + clear the slot so future requests can compute fresh
    async with _inflight_lock:
        _inflight.pop(inflight_key, None)
    if not future.done():
        future.set_result(result)
    return result


# ============================================================================
# Maintenance + introspection
# ============================================================================


def stats() -> dict[str, Any]:
    """Per-kind row counts + last-write timestamps. For admin dashboards."""
    db = SessionLocal()
    try:
        try:
            rows = db.execute(
                select(
                    ModelArtifact.kind,
                    ModelArtifact.id,
                    ModelArtifact.updated_at,
                    ModelArtifact.valid_until,
                )
            ).all()
        except Exception as e:  # noqa: BLE001
            # Table likely doesn't exist yet — run `alembic upgrade head`.
            return {"by_kind": {}, "error": f"table unavailable: {str(e)[:140]}"}
    finally:
        db.close()

    by_kind: dict[str, dict[str, Any]] = {}
    for kind, _id, updated_at, valid_until in rows:
        d = by_kind.setdefault(kind, {"count": 0, "fresh": 0, "stale": 0, "permanent": 0,
                                       "last_updated": None})
        d["count"] += 1
        if valid_until is None:
            d["permanent"] += 1
        elif valid_until < _now():
            d["stale"] += 1
        else:
            d["fresh"] += 1
        if d["last_updated"] is None or updated_at > d["last_updated"]:
            d["last_updated"] = updated_at
    return {"by_kind": {k: {**v, "last_updated": (v["last_updated"].isoformat() if v["last_updated"] else None)}
                       for k, v in by_kind.items()}}


def vacuum_expired(older_than_days: int = 7) -> int:
    """Delete artifacts that have been expired for `older_than_days`+ days."""
    cutoff = _now() - timedelta(days=older_than_days)
    db = SessionLocal()
    try:
        try:
            n = (
                db.query(ModelArtifact)
                .filter(ModelArtifact.valid_until.isnot(None))
                .filter(ModelArtifact.valid_until < cutoff)
                .delete(synchronize_session=False)
            )
            db.commit()
            return n
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.debug("artifact_vacuum_failed", error=str(e)[:120])
            return 0
    finally:
        db.close()
