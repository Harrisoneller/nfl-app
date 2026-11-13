"""News refresh writes are idempotent, and startup schedules that refresh once.

Production (Nov 13) ran the boot date-trigger and the 5-minute interval at the
same moment. One commit won. The other raised UniqueViolation on
``news_items_pkey`` and then PendingRollbackError, because the failed
transaction was still open when the job tried to record its error.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError, PendingRollbackError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.jobs import scheduler as scheduler_mod
from app.models.data_sync_run import DataSyncRun
from app.models.news import NewsItem
from app.services import news_service, sync_run_service
from app.services.news_service import build_news_item_upsert, upsert_news_items


def _row(**overrides) -> dict:
    now = datetime(2026, 11, 13, 16, 13, tzinfo=timezone.utc)
    base = {
        "id": "headline-1",
        "source": "rss",
        "source_label": "ESPN",
        "title": "Eagles host the Cowboys",
        "summary": "Sunday night",
        "link": "https://example.test/headline-1",
        "author": "desk",
        "image_url": "",
        "published_at": now,
        "team_tags": "",
    }
    base.update(overrides)
    return base


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _memory_engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    NewsItem.__table__.create(bind=engine)
    return engine


def _session(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()


@pytest.fixture()
def db():
    engine = _memory_engine()
    session = _session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_postgres_upsert_updates_on_primary_key():
    stmt = build_news_item_upsert(_row(), dialect="postgresql")
    compact = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())
    insert_sql, update_sql = compact.split(" DO UPDATE SET ", 1)
    assert "ON CONFLICT (id)" in insert_sql
    assert "title" in update_sql
    assert "team_tags" in update_sql
    assert "updated_at" in update_sql
    assert "created_at" not in update_sql


def test_duplicate_ids_update_one_row_and_keep_created_at(db):
    original = _row()
    assert upsert_news_items(db, [original, dict(original)]) == 2
    db.commit()
    db.expire_all()
    stored = db.get(NewsItem, "headline-1")
    assert stored is not None
    created = _utc(stored.created_at)
    assert db.query(NewsItem).count() == 1

    updated = _row(title="Eagles-Cowboys kickoff moved", summary="New time")
    assert upsert_news_items(db, [updated]) == 1
    db.commit()
    db.expire_all()
    stored = db.get(NewsItem, "headline-1")
    assert stored is not None
    assert stored.title == "Eagles-Cowboys kickoff moved"
    assert stored.summary == "New time"
    assert _utc(stored.created_at) == created
    assert db.query(NewsItem).count() == 1


def test_concurrent_inserts_of_the_same_id_do_not_raise_or_duplicate(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'news.db'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=30000")
    NewsItem.__table__.create(bind=engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(title: str) -> None:
        session = Session()
        try:
            barrier.wait(timeout=10)
            upsert_news_items(session, [_row(title=title)])
            session.commit()
        except BaseException as exc:  # noqa: BLE001 — surface a thread failure
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [
        threading.Thread(target=run, args=("first writer",)),
        threading.Thread(target=run, args=("second writer",)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        check = Session()
        try:
            rows = check.query(NewsItem).all()
            assert len(rows) == 1
            assert rows[0].id == "headline-1"
            assert rows[0].title in {"first writer", "second writer"}
        finally:
            check.close()
    finally:
        engine.dispose()


class _Feed:
    source = "rss"

    def __init__(self, items_fn):
        self._items_fn = items_fn
        self.source = "rss"

    def __call__(self, *args, **kwargs):
        feed = _Feed(self._items_fn)
        feed.source = kwargs.get("source_name", "rss")
        return feed

    async def fetch(self):
        return self._items_fn()

    async def aclose(self):
        return None


def _patch_adapters(monkeypatch, items_fn) -> None:
    factory = _Feed(items_fn)
    monkeypatch.setattr(news_service, "RSSAdapter", factory)
    monkeypatch.setattr(news_service, "RedditAdapter", factory)
    monkeypatch.setattr(news_service, "TwitterAdapter", factory)


@pytest.mark.asyncio
async def test_refresh_duplicate_ids_do_not_duplicate_rows(db, monkeypatch):
    state = {"title": "Eagles host the Cowboys"}

    def items():
        row = _row(id="dup", title=state["title"])
        return [row, dict(row)]

    _patch_adapters(monkeypatch, items)
    assert await news_service.refresh_news(db) == 10
    db.expire_all()
    stored = db.query(NewsItem).one()
    assert stored.id == "dup"
    assert "PHI" in stored.team_tags
    assert "DAL" in stored.team_tags

    state["title"] = "Cowboys at Eagles, updated"
    assert await news_service.refresh_news(db) == 10
    db.expire_all()
    stored = db.query(NewsItem).one()
    assert stored.title == "Cowboys at Eagles, updated"


@pytest.mark.asyncio
async def test_failed_refresh_rolls_back_and_leaves_the_session_usable(db, monkeypatch):
    seed = NewsItem(
        id="seed",
        source="rss",
        source_label="ESPN",
        title="Already stored",
        summary="",
        link="https://example.test/seed",
        author="",
        image_url="",
        team_tags="",
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    db.add(seed)
    db.commit()

    real = news_service.upsert_news_items

    def explode(session, items):
        real(session, items)
        session.execute(text("SELECT * FROM no_such_news_table"))

    monkeypatch.setattr(news_service, "upsert_news_items", explode)
    _patch_adapters(monkeypatch, lambda: [_row(id="incoming")])

    with pytest.raises(OperationalError):
        await news_service.refresh_news(db)

    assert db.execute(text("SELECT 1")).scalar_one() == 1
    assert db.get(NewsItem, "seed") is not None
    assert db.get(NewsItem, "incoming") is None
    db.add(NewsItem(
        id="after",
        source="rss",
        source_label="ESPN",
        title="Session still accepts writes",
        summary="",
        link="https://example.test/after",
        author="",
        image_url="",
        team_tags="",
        created_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
    ))
    db.commit()
    assert db.get(NewsItem, "after") is not None


@pytest.mark.asyncio
async def test_failed_news_job_records_error_without_pending_rollback(monkeypatch):
    """The startup failure was UniqueViolation, then PendingRollbackError."""
    engine = _memory_engine()
    DataSyncRun.__table__.create(bind=engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    monkeypatch.setattr("app.db.SessionLocal", Session)

    real = news_service.upsert_news_items

    def explode(session, items):
        real(session, items)
        session.execute(text("SELECT * FROM no_such_news_table"))

    monkeypatch.setattr(news_service, "upsert_news_items", explode)
    _patch_adapters(monkeypatch, lambda: [_row(id="incoming")])

    async def job(session):
        return await news_service.refresh_news(session)

    try:
        with pytest.raises(OperationalError) as raised:
            await sync_run_service.run_job("news", job)
        assert not isinstance(raised.value, PendingRollbackError)

        check = Session()
        try:
            run = check.query(DataSyncRun).one()
            assert run.domain == "news"
            assert run.status == "error"
            assert "no_such_news_table" in (run.message or "")
            assert check.query(NewsItem).count() == 0
            assert check.execute(text("SELECT 1")).scalar_one() == 1
        finally:
            check.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_run_job_rolls_back_a_poisoned_session_before_recording_the_error(monkeypatch):
    """A job that does not roll back itself must not raise PendingRollbackError."""
    engine = _memory_engine()
    DataSyncRun.__table__.create(bind=engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    monkeypatch.setattr("app.db.SessionLocal", Session)

    async def poison(session):
        session.execute(text("SELECT * FROM no_such_news_table"))

    try:
        with pytest.raises(OperationalError) as raised:
            await sync_run_service.run_job("news", poison)
        assert not isinstance(raised.value, PendingRollbackError)
        check = Session()
        try:
            run = check.query(DataSyncRun).one()
            assert run.status == "error"
            assert "no_such_news_table" in (run.message or "")
        finally:
            check.close()
    finally:
        engine.dispose()


@pytest.mark.parametrize("boot", ["none", "minimal", "full"])
@pytest.mark.parametrize("live", [False, True])
def test_startup_registers_one_non_overlapping_news_refresh(monkeypatch, boot, live):
    settings = get_settings()
    monkeypatch.setattr(settings, "boot_warmup_level", boot)
    monkeypatch.setattr(scheduler_mod, "is_nfl_live_period", lambda: live)
    monkeypatch.setattr(scheduler_mod.AsyncIOScheduler, "start", lambda self, *a, **k: None)
    scheduler_mod._scheduler = None
    try:
        scheduler_mod.start_scheduler()
        sched = scheduler_mod._scheduler
        assert sched is not None
        news_jobs = [job for job in sched.get_jobs() if job.func is scheduler_mod._job_refresh_news]
        assert [job.id for job in news_jobs] == ["news"]
        assert sched.get_job("boot_news") is None
        job = news_jobs[0]
        assert isinstance(job.trigger, IntervalTrigger)
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.next_run_time is not None
        delay = (job.next_run_time - datetime.now(timezone.utc)).total_seconds()
        assert 0 <= delay <= 30
    finally:
        scheduler_mod._scheduler = None
