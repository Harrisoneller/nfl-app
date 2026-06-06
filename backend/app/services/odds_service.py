"""Odds ingestion + queries.

Budget model: The Odds API free tier is 500 credits/month and each pull costs
(markets × regions) credits. To stay sustainable we treat the API as a *batch*
source — only the scheduled job pulls (twice daily, see jobs/scheduler.py), and
every user-facing read serves the persisted `odds_lines` snapshot. `refresh_odds`
is additionally guarded so it never over-pulls:

  1. **Min-interval guard** — skip if we pulled more recently than
     `settings.odds_min_refresh_hours`. The last-pull time is persisted in the
     model_artifacts table (via artifact_cache) so the guard survives process
     restarts/redeploys, not just the in-memory cache.
  2. **Offseason guard** — skip if no game kicks off within
     `settings.odds_lookahead_days`. In the deep offseason the API returns empty
     event lists but still bills a credit, so we don't bother calling it until
     lines are actually meaningful.

Pass `force=True` (the manual /admin/refresh/odds endpoint and warmup script do)
to bypass both guards and pull immediately.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..adapters.data.odds_api import OddsFetchStatus, TheOddsApiAdapter
from ..config import get_settings
from ..logging_config import get_logger
from ..models.game import Game
from ..models.odds import OddsLine
from . import artifact_cache

log = get_logger(__name__)

# model_artifacts slot used to persist the last-pull timestamp across restarts.
_META_KIND = "odds_meta"
_META_KEY = "last_refresh"


class OddsRefreshResult(TypedDict):
    lines: int
    configured: bool
    upstream_events: int
    status: str  # OddsFetchStatus | "skipped_fresh" | "skipped_offseason"
    message: str | None
    lines_in_db: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_odds(
    db: Session, *, market: str | None = None, limit: int = 100,
) -> list[OddsLine]:
    q = db.query(OddsLine)
    if market:
        q = q.filter(OddsLine.market == market)
    return q.order_by(OddsLine.commence_time.asc().nullslast()).limit(limit).all()


def get_event_odds(db: Session, event_id: str) -> list[OddsLine]:
    return db.query(OddsLine).filter(OddsLine.event_id == event_id).all()


def odds_status(db: Session) -> dict[str, Any]:
    """Lightweight health for the odds page / admin tooling.

    Surfaces *both* "when were lines last written" (max(created_at)) and
    "when did the cron last attempt a pull, with what outcome" (artifact_cache
    `odds_meta:last_refresh`). These can drift apart: in the offseason the cron
    fires every 12h but skips, so `last_attempt` advances while `last_updated`
    stays pinned to the most recent real write. The UI uses `last_attempt.status`
    to explain *why* lines look stale instead of misleadingly claiming a
    twice-daily refresh that's actually being skipped by design.
    """
    settings = get_settings()
    configured = bool(settings.odds_api_key.strip())
    lines_in_db = db.query(OddsLine).count()
    # All rows are rewritten together on each successful pull, so the newest
    # created_at is "when the lines on screen were fetched".
    last_updated = db.query(func.max(OddsLine.created_at)).scalar()

    # Last cron attempt (any outcome — ok / skipped_fresh / skipped_offseason / error).
    last_attempt: dict[str, Any] | None = None
    rec = artifact_cache.get(db, _META_KIND, _META_KEY)
    if rec:
        last_attempt = {
            "at": rec.get("at"),
            "status": rec.get("status"),
            "lines_in_db": rec.get("lines_in_db"),
        }

    return {
        "configured": configured,
        "lines_in_db": lines_in_db,
        "ready": lines_in_db > 0,
        "last_updated": last_updated.isoformat() if last_updated else None,
        "last_attempt": last_attempt,
        "next_refresh_at": _next_cron_fire(settings.odds_refresh_hours_utc),
        "refresh_hours_utc": settings.odds_refresh_hours_utc,
        "lookahead_days": settings.odds_lookahead_days,
    }


def _next_cron_fire(hours_csv: str) -> str | None:
    """Next UTC datetime the odds cron will fire, given a 'H,H,...' hour list.

    Pure stdlib — no APScheduler introspection needed. Returns ISO-8601 or None
    if the hour list is unparseable.
    """
    hours: list[int] = []
    for part in (hours_csv or "").split(","):
        p = part.strip()
        if p.isdigit():
            h = int(p)
            if 0 <= h <= 23:
                hours.append(h)
    if not hours:
        return None
    hours.sort()
    now = _now()
    # Cron is `minute=0` on each listed hour — find the next one ≥ now.
    for h in hours:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate.isoformat()
    # All hours today have passed; first hour tomorrow.
    tomorrow = (now + timedelta(days=1)).replace(
        hour=hours[0], minute=0, second=0, microsecond=0,
    )
    return tomorrow.isoformat()


def _get_last_refresh(db: Session) -> datetime | None:
    """Last time we actually pulled from the API (persisted, restart-safe)."""
    rec = artifact_cache.get(db, _META_KIND, _META_KEY)
    if not rec:
        return None
    try:
        return datetime.fromisoformat(rec["at"])
    except (KeyError, TypeError, ValueError):
        return None


def _set_last_refresh(db: Session, status: str, lines_in_db: int) -> None:
    artifact_cache.set_(
        db, _META_KIND, _META_KEY,
        {"at": _now().isoformat(), "status": status, "lines_in_db": lines_in_db},
        ttl_seconds=None,  # never expires; we compare ages ourselves
    )


def _has_game_within(db: Session, days: int) -> bool:
    """True if any game kicks off between ~now and now+`days` (offseason guard)."""
    now = _now()
    try:
        row = (
            db.query(Game.id)
            .filter(Game.start_time.isnot(None))
            .filter(Game.start_time >= now - timedelta(days=1))
            .filter(Game.start_time <= now + timedelta(days=days))
            .first()
        )
    except Exception as e:  # noqa: BLE001 — never let the guard take down a refresh
        log.debug("odds_offseason_guard_failed", error=str(e)[:120])
        return True  # fail open: if we can't tell, allow the pull
    return row is not None


async def refresh_odds(db: Session, *, force: bool = False) -> OddsRefreshResult:
    settings = get_settings()
    configured = bool(settings.odds_api_key.strip())
    lines_before = db.query(OddsLine).count()

    def _skip(status: str, message: str) -> OddsRefreshResult:
        log.info("odds_refresh_skipped", status=status, message=message)
        return {
            "lines": 0,
            "configured": configured,
            "upstream_events": 0,
            "status": status,
            "message": message,
            "lines_in_db": lines_before,
        }

    # --- Budget guards (bypassed when force=True) ---------------------------
    if not force:
        last = _get_last_refresh(db)
        if last is not None:
            age_h = (_now() - last).total_seconds() / 3600.0
            if age_h < settings.odds_min_refresh_hours:
                return _skip(
                    "skipped_fresh",
                    f"Pulled {age_h:.1f}h ago (< {settings.odds_min_refresh_hours}h floor)",
                )
        if not _has_game_within(db, settings.odds_lookahead_days):
            return _skip(
                "skipped_offseason",
                f"No game kicks off within {settings.odds_lookahead_days} days",
            )

    adapter = TheOddsApiAdapter()
    status: OddsFetchStatus = "error"
    message: str | None = None
    events: list[dict[str, Any]] = []
    try:
        result = await adapter.fetch_game_odds_result()
        status = result.status
        message = result.message
        events = result.events
    except Exception as e:  # noqa: BLE001
        log.warning("odds_fetch_failed", error=str(e))
        status = "error"
        message = str(e)
    finally:
        await adapter.aclose()

    # Replace all current odds (small dataset, simpler than diffing)
    if events:
        db.query(OddsLine).delete()

    n = 0
    for ev in events:
        commence = _parse_iso(ev.get("commence_time"))
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                for outcome in mk.get("outcomes", []):
                    db.add(OddsLine(
                        market=mk.get("key", ""),
                        event_id=str(ev.get("id")),
                        home_team=ev.get("home_team"),
                        away_team=ev.get("away_team"),
                        commence_time=commence,
                        bookmaker=bm.get("title", ""),
                        label=outcome.get("name", ""),
                        price=outcome.get("price"),
                        point=outcome.get("point"),
                        raw=outcome,
                    ))
                    n += 1
    db.commit()
    lines_in_db = db.query(OddsLine).count()

    # Sparky: append the same events to the append-only odds_snapshots history so
    # line-movement signals have data to work with. Reuses the events we already
    # fetched (zero extra Odds API spend) and never blocks the odds pull on error.
    if events:
        try:
            from . import sparky_service  # lazy import avoids a module-load cycle

            sparky_service.capture_snapshot_from_events(db, events)
        except Exception as e:  # noqa: BLE001
            log.warning("sparky_snapshot_capture_failed", error=str(e)[:160])

    # Record the pull time (any outcome) so the min-interval guard holds across
    # restarts and we don't re-hit a rate-limited/erroring API every cron tick.
    _set_last_refresh(db, status, lines_in_db)
    log.info(
        "odds_refreshed",
        lines=n,
        status=status,
        upstream_events=len(events),
        lines_in_db=lines_in_db,
    )
    return {
        "lines": n,
        "configured": configured,
        "upstream_events": len(events),
        "status": status,
        "message": message,
        "lines_in_db": lines_in_db,
    }


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
