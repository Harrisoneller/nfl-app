"""News aggregation + team/player-specific queries.

Ingestion tags every item with the team_ids whose aliases appear in the
title+summary, so per-team filtering becomes a cheap LIKE on team_tags.
Player-specific feeds are a substring search on title+summary.

Writes are ``INSERT ... ON CONFLICT (id) DO UPDATE``. Two refreshes can see
the same headline before either commits (startup used to run a boot job and
the interval job together). A check-then-insert then raises UniqueViolation
on ``news_items_pkey`` and leaves the session pending rollback.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..adapters.data.sleeper import SleeperAdapter
from ..adapters.news.reddit import RedditAdapter, subreddit_for_team
from ..adapters.news.rss import RSSAdapter
from ..adapters.news.twitter import TwitterAdapter
from ..cache import cache
from ..config import get_settings
from ..logging_config import get_logger
from ..models.news import NewsItem
from ..utils.team_aliases import tags_for_text

log = get_logger(__name__)

TEAM_SUBREDDIT_CACHE_TTL = 60 * 5  # 5 min


def list_news(
    db: Session,
    *,
    limit: int = 30,
    source: str | None = None,
    team_id: str | None = None,
) -> list[NewsItem]:
    q = db.query(NewsItem)
    if source:
        q = q.filter(NewsItem.source == source)
    if team_id:
        # team_tags is comma-separated; check for boundary-safe match.
        tid = team_id.upper()
        q = q.filter(
            or_(
                NewsItem.team_tags == tid,
                NewsItem.team_tags.like(f"{tid},%"),
                NewsItem.team_tags.like(f"%,{tid}"),
                NewsItem.team_tags.like(f"%,{tid},%"),
            )
        )
    return q.order_by(NewsItem.published_at.desc().nullslast()).limit(limit).all()


def search_news_by_text(db: Session, text: str, limit: int = 25) -> list[NewsItem]:
    """Substring match on title or summary — used for player feeds."""
    if not text or len(text) < 3:
        return []
    like = f"%{text}%"
    return (
        db.query(NewsItem)
        .filter(or_(NewsItem.title.ilike(like), NewsItem.summary.ilike(like)))
        .order_by(NewsItem.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )


async def fetch_team_reddit(team_id: str, limit: int = 20) -> list[dict]:
    """Lazy team-subreddit fetcher with short TTL caching."""
    sub = subreddit_for_team(team_id)
    if not sub:
        return []
    key = f"team_subreddit:{sub}"
    if (v := cache.get(key)) is not None:
        return v
    adapter = RedditAdapter(subreddit=sub, limit=limit)
    try:
        items = await adapter.fetch()
    finally:
        await adapter.aclose()
    cache.set(key, items, TEAM_SUBREDDIT_CACHE_TTL)
    return items


async def list_fantasy_news(db: Session, limit: int = 30) -> list[NewsItem]:
    """News from fantasy-tagged sources only."""
    return (
        db.query(NewsItem)
        .filter(NewsItem.source.in_(["fantasy_rss", "fantasy_reddit"]))
        .order_by(NewsItem.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )


async def fetch_sleeper_trending(kind: str = "add", limit: int = 20) -> list[dict]:
    """Trending players (Sleeper) — fantasy adds/drops over last 24h."""
    key = f"sleeper_trending:{kind}:{limit}"
    if (v := cache.get(key)) is not None:
        return v
    adapter = SleeperAdapter()
    try:
        rows = await adapter.fetch_trending(kind=kind, lookback_hours=24, limit=limit)
    except Exception as e:  # noqa: BLE001
        log.warning("sleeper_trending_failed", error=str(e))
        rows = []
    finally:
        await adapter.aclose()
    cache.set(key, rows, 60 * 5)  # 5 min
    return rows


_NEWS_UPDATE_FIELDS = (
    "source",
    "source_label",
    "title",
    "summary",
    "link",
    "author",
    "image_url",
    "published_at",
    "team_tags",
)


def _news_values(item: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": str(item["id"]),
        "source": item.get("source") or "",
        "source_label": item.get("source_label") or "",
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
        "link": item.get("link") or "",
        "author": item.get("author") or "",
        "image_url": item.get("image_url") or "",
        "published_at": item.get("published_at"),
        "team_tags": item.get("team_tags") or "",
        "created_at": now,
        "updated_at": now,
    }


def build_news_item_upsert(item: dict[str, Any], *, dialect: str):
    """Insert a news row, or refresh its text when ``id`` already exists.

    ``created_at`` stays on the original row. SQLite tests and Postgres both
    implement ``ON CONFLICT (id)``.
    """
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    values = _news_values(item)
    stmt = insert(NewsItem).values(**values)
    excluded = stmt.excluded
    set_ = {field: getattr(excluded, field) for field in _NEWS_UPDATE_FIELDS}
    set_["updated_at"] = excluded.updated_at
    return stmt.on_conflict_do_update(index_elements=["id"], set_=set_)


def upsert_news_items(db: Session, items: list[dict[str, Any]]) -> int:
    """Insert or update each item. The same id twice does not add a second row."""
    dialect = db.get_bind().dialect.name
    written = 0
    for item in items:
        if not item.get("id"):
            continue
        db.execute(build_news_item_upsert(item, dialect=dialect))
        written += 1
    return written


async def _aclose(adapter: object) -> None:
    close = getattr(adapter, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001
        pass


async def refresh_news(db: Session) -> int:
    """Pull from all adapters, tag with team_ids, upsert.

    A write or commit failure rolls the transaction back before it propagates,
    so the caller can keep using ``db``. Adapter fetch failures are skipped.
    """
    s = get_settings()
    adapters = [
        RSSAdapter(),
        RedditAdapter(subreddit="nfl"),
        TwitterAdapter(),
        RSSAdapter(feeds=s.fantasy_rss_feed_list, source_name="fantasy_rss"),
        RedditAdapter(subreddit="fantasyfootball"),
    ]
    # Tag the fantasy-reddit feed as fantasy_reddit (source override)
    adapters[4].source = "fantasy_reddit"
    total = 0
    try:
        for adapter in adapters:
            fetched = False
            try:
                try:
                    items = await adapter.fetch()
                    fetched = True
                except Exception as e:  # noqa: BLE001
                    log.warning("news_adapter_failed", source=adapter.source, error=str(e))
                    continue
                for item in items:
                    text_for_tags = f"{item.get('title','')} {item.get('summary','')}"
                    item["team_tags"] = ",".join(tags_for_text(text_for_tags))
                total += upsert_news_items(db, items)
            finally:
                if fetched:
                    await _aclose(adapter)
        db.commit()
    except Exception:
        db.rollback()
        raise
    log.info("news_refreshed", items=total)
    return total
