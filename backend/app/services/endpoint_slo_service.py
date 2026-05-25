"""In-memory rolling endpoint SLO stats + optional DB snapshots."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from ..logging_config import get_logger
from ..models.endpoint_slo_snapshot import EndpointSloSnapshot

log = get_logger(__name__)

WINDOW_SECONDS = 60 * 30
TRACKED_ENDPOINTS = {
    "/h2h/{team_a}/{team_b}",
    "/predictions/games",
    "/odds",
    "/odds/event/{event_id}",
    "/teams",
    "/teams/{team_id}/profile",
    "/players",
    "/players/{player_id}",
    "/stats/compare/teams",
    "/stats/compare/players",
}


@dataclass(slots=True)
class _MetricPoint:
    ts: float
    duration_ms: float
    cache_status: str


_samples: dict[tuple[str, str, str], deque[_MetricPoint]] = defaultdict(deque)
_lock = threading.Lock()


def track_request(
    request: Request,
    *,
    duration_ms: float,
    status_code: int,
    cache_status: str | None = None,
) -> None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or request.url.path
    if route_path not in TRACKED_ENDPOINTS:
        return
    method = request.method.upper()
    status_bucket = _status_bucket(status_code)
    point = _MetricPoint(
        ts=time.time(),
        duration_ms=float(duration_ms),
        cache_status=(cache_status or "unknown"),
    )
    key = (route_path, method, status_bucket)
    with _lock:
        q = _samples[key]
        q.append(point)
        _prune(q, max_age_seconds=WINDOW_SECONDS)


def current_snapshot(window_seconds: int = 60 * 15) -> dict[str, Any]:
    cutoff = time.time() - window_seconds
    generated = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    with _lock:
        for (endpoint_key, method, status_bucket), points in _samples.items():
            subset = [p for p in points if p.ts >= cutoff]
            if not subset:
                continue
            durations = sorted(p.duration_ms for p in subset)
            cache_hits = sum(1 for p in subset if p.cache_status in {"hit", "stale"})
            rows.append(
                {
                    "endpoint": endpoint_key,
                    "method": method,
                    "status_bucket": status_bucket,
                    "sample_size": len(subset),
                    "p50_ms": _quantile(durations, 0.50),
                    "p95_ms": _quantile(durations, 0.95),
                    "p99_ms": _quantile(durations, 0.99),
                    "cache_hit_rate": round(cache_hits / len(subset), 4),
                }
            )
    rows.sort(key=lambda r: (r["endpoint"], r["method"], r["status_bucket"]))
    return {"generated_at": generated, "window_seconds": window_seconds, "rows": rows}


def flush_snapshot(db: Session, *, window_seconds: int = 60 * 15) -> dict[str, Any]:
    snap = current_snapshot(window_seconds=window_seconds)
    now = datetime.now(UTC)
    start = now - timedelta(seconds=window_seconds)
    written = 0
    for row in snap["rows"]:
        db.add(
            EndpointSloSnapshot(
                endpoint_key=row["endpoint"],
                method=row["method"],
                status_bucket=row["status_bucket"],
                sample_size=row["sample_size"],
                p50_ms=row["p50_ms"],
                p95_ms=row["p95_ms"],
                p99_ms=row["p99_ms"],
                cache_hit_rate=row["cache_hit_rate"],
                window_started_at=start,
                window_ended_at=now,
            )
        )
        written += 1
    db.commit()
    log.info("endpoint_slo_snapshot_flushed", rows=written, window_seconds=window_seconds)
    return {"status": "ok", "rows": written, "window_seconds": window_seconds}


def recent_history(db: Session, *, endpoint: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(EndpointSloSnapshot).order_by(desc(EndpointSloSnapshot.id)).limit(limit)
    if endpoint:
        stmt = stmt.where(EndpointSloSnapshot.endpoint_key == endpoint)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "endpoint": r.endpoint_key,
            "method": r.method,
            "status_bucket": r.status_bucket,
            "sample_size": r.sample_size,
            "p50_ms": r.p50_ms,
            "p95_ms": r.p95_ms,
            "p99_ms": r.p99_ms,
            "cache_hit_rate": r.cache_hit_rate,
            "window_started_at": r.window_started_at.isoformat(),
            "window_ended_at": r.window_ended_at.isoformat(),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _status_bucket(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


def _prune(points: deque[_MetricPoint], *, max_age_seconds: int) -> None:
    cutoff = time.time() - max_age_seconds
    while points and points[0].ts < cutoff:
        points.popleft()


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    idx = (len(values) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(values) - 1)
    weight = idx - lo
    out = values[lo] * (1 - weight) + values[hi] * weight
    return round(out, 2)
