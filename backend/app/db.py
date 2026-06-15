"""SQLAlchemy engine, session, and Base.

Pool tuning matters once we run more than one worker. Defaults here are
sized for a single small Postgres + 2-4 uvicorn workers; tighten on a
constrained host or loosen on a beefier one.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()


def _engine_connect_args() -> dict:
    """Postgres-only server-side statement timeout, web role only.

    On the request-serving `web` role we cap how long any single query may
    run so a pathological query (or a lock wait) can't pin a pooled
    connection indefinitely and starve the pool. The `worker` role is exempt
    because the derive/materialize pipeline legitimately runs long write
    queries that must not be cancelled. sqlite (tests) gets nothing.
    """
    timeout_ms = settings.db_statement_timeout_ms
    if (
        timeout_ms > 0
        and settings.app_role == "web"
        and settings.database_url.startswith("postgresql")
    ):
        # libpq options string; applies to every connection in the pool.
        return {"options": f"-c statement_timeout={int(timeout_ms)}"}
    return {}


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=1800,  # recycle idle connections after 30 min
    connect_args=_engine_connect_args(),
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
