"""Odds ingestion + queries."""
from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from ..adapters.data.odds_api import OddsFetchStatus, TheOddsApiAdapter
from ..config import get_settings
from ..logging_config import get_logger
from ..models.odds import OddsLine

log = get_logger(__name__)


class OddsRefreshResult(TypedDict):
    lines: int
    configured: bool
    upstream_events: int
    status: OddsFetchStatus
    message: str | None
    lines_in_db: int


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
    """Lightweight health for the odds page / admin tooling."""
    configured = bool(get_settings().odds_api_key.strip())
    lines_in_db = db.query(OddsLine).count()
    return {
        "configured": configured,
        "lines_in_db": lines_in_db,
        "ready": lines_in_db > 0,
    }


async def refresh_odds(db: Session) -> OddsRefreshResult:
    configured = bool(get_settings().odds_api_key.strip())
    lines_before = db.query(OddsLine).count()
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
