"""Reddit 403/429 opens one process-local cooldown. Other errors still surface."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.news import reddit as reddit_mod
from app.models.news import NewsItem
from app.services import news_service


class _Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {"data": {"children": []}}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://www.reddit.com/r/nfl/hot.json")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("status", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload
        self.calls = 0

    async def get(self, url: str) -> _Response:
        self.calls += 1
        return _Response(self.status, self.payload)

    async def aclose(self) -> None:
        return None


class _Script:
    def __init__(self, items: list[dict], *, source: str) -> None:
        self.source = source
        self.items = items
        self.calls = 0

    async def fetch(self) -> list[dict]:
        self.calls += 1
        if self.items and self.items[0].get("raise"):
            raise RuntimeError(self.items[0]["raise"])
        rows = []
        for item in self.items:
            row = dict(item)
            # NFL refresh pulls two RSS feeds. Distinct ids so both inserts land.
            if self.source == "rss":
                row["id"] = f"{item['id']}-{self.calls}"
            rows.append(row)
        return rows

    async def aclose(self) -> None:
        return None


def _item() -> dict:
    return {
        "id": "rss1",
        "source": "rss",
        "source_label": "ESPN",
        "title": "Eagles host the Cowboys",
        "summary": "Sunday",
        "link": "https://example.test/rss1",
        "author": "desk",
        "image_url": "",
        "published_at": datetime(2026, 11, 8, 18, 0, tzinfo=UTC),
        "team_tags": "",
    }


@pytest.fixture(autouse=True)
def _clear_cooldown():
    reddit_mod._blocked_until = 0.0
    yield
    reddit_mod._blocked_until = 0.0


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": 0.0}
    monkeypatch.setattr(reddit_mod, "_now", lambda: now["t"])
    return now


@pytest.fixture()
def warnings(monkeypatch):
    seen: list[str] = []
    real = reddit_mod.log.warning

    def capture(event, *args, **kwargs):
        seen.append(str(event))
        return real(event, *args, **kwargs)

    monkeypatch.setattr(reddit_mod.log, "warning", capture)
    return seen


@pytest.fixture()
async def adapter():
    item = reddit_mod.RedditAdapter()
    original = item.client
    try:
        yield item
    finally:
        await original.aclose()


def _use(adapter, client: _Client) -> _Client:
    adapter.client = client
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_block_status_skips_calls_and_warns_once(adapter, clock, warnings, status):
    client = _use(adapter, _Client(status=status))
    assert await adapter.fetch() == []
    assert await adapter.fetch() == []
    assert await adapter.fetch() == []
    assert client.calls == 1
    assert warnings == ["reddit_cooldown"]
    assert reddit_mod.reddit_cooldown_active() is True


@pytest.mark.asyncio
async def test_retry_after_ttl_calls_reddit_again(adapter, clock, warnings):
    client = _use(adapter, _Client(status=429))
    assert await adapter.fetch() == []
    clock["t"] = reddit_mod.REDDIT_BLOCK_TTL_SECONDS - 1
    assert await adapter.fetch() == []
    assert client.calls == 1
    assert warnings == ["reddit_cooldown"]

    clock["t"] = reddit_mod.REDDIT_BLOCK_TTL_SECONDS
    client.status = 200
    client.payload = {
        "data": {
            "children": [{
                "data": {
                    "title": "Kickoff thread",
                    "permalink": "/r/nfl/comments/abc/kickoff/",
                    "author": "mod",
                    "created_utc": 1_700_000_000,
                },
            }],
        },
    }
    items = await adapter.fetch()
    assert client.calls == 2
    assert warnings == ["reddit_cooldown"]
    assert len(items) == 1
    assert items[0]["source"] == "reddit"
    assert items[0]["title"] == "Kickoff thread"
    assert items[0]["source_label"] == "r/nfl"


@pytest.mark.asyncio
async def test_unrelated_http_error_is_not_cooled_down(adapter, clock, warnings):
    client = _use(adapter, _Client(status=500))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.fetch()
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.fetch()
    assert client.calls == 2
    assert warnings == []
    assert reddit_mod.reddit_cooldown_active() is False


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    NewsItem.__table__.create(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_cooldown_does_not_suppress_other_sources(db, monkeypatch, adapter, clock, warnings):
    client = _use(adapter, _Client(status=403))
    rss = _Script([_item()], source="rss")
    twitter = _Script([{"raise": "twitter down"}], source="twitter")
    service_warnings: list[str] = []
    real = news_service.log.warning

    def capture(event, *args, **kwargs):
        service_warnings.append(str(event))
        return real(event, *args, **kwargs)

    monkeypatch.setattr(news_service.log, "warning", capture)
    monkeypatch.setattr(news_service, "RedditAdapter", lambda *a, **k: adapter)
    monkeypatch.setattr(news_service, "RSSAdapter", lambda *a, **k: rss)
    monkeypatch.setattr(news_service, "TwitterAdapter", lambda *a, **k: twitter)

    # Two RSS feeds per refresh; Reddit is cooled down after the first 403.
    assert await news_service.refresh_news(db) == 2
    assert await news_service.refresh_news(db) == 2
    assert client.calls == 1
    assert rss.calls == 4
    assert twitter.calls == 2
    assert warnings == ["reddit_cooldown"]
    assert service_warnings == ["news_adapter_failed", "news_adapter_failed"]
    assert db.get(NewsItem, "rss1-1") is not None
    assert db.get(NewsItem, "rss1-4") is not None
