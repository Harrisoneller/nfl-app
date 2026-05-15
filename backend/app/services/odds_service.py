"""Odds ingestion + queries."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..adapters.data.odds_api import TheOddsApiAdapter
from ..logging_config import get_logger
from ..models.odds import OddsLine

log = get_logger(__name__)


def list_odds(
    db: Session, *, market: str | None = None, limit: int = 100,
) -> list[OddsLine]:
    q = db.query(OddsLine)
    if market:
        q = q.filter(OddsLine.market == market)
    return q.order_by(OddsLine.commence_time.asc().nullslast()).limit(limit).all()


def get_event_odds(db: Session, event_id: str) -> list[OddsLine]:
    return db.query(OddsLine).filter(OddsLine.event_id == event_id).all()


async def refresh_odds(db: Session) -> int:
    adapter = TheOddsApiAdapter()
    try:
        events = await adapter.fetch_game_odds()
    except Exception as e:  # noqa: BLE001
        log.warning("odds_fetch_failed", error=str(e))
        events = []
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
    log.info("odds_refreshed", lines=n)
    return n


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
