"""Alembic environment.

Pulls the database URL from the app's settings so migrations and the app
agree on the target. Imports the models package so autogenerate sees them.
"""
from __future__ import annotations

import uuid
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401  (registers all models with Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata

_sqlite_compat_installed = False


def _install_sqlite_compat() -> None:
    """Render the Postgres revision history onto SQLite.

    The scripts use UUID, JSONB, ``now()``, and ``::jsonb``. Those rewrites
    apply only when the connection dialect is sqlite. Postgres DDL is unchanged.
    """
    global _sqlite_compat_installed
    if _sqlite_compat_installed:
        return
    _sqlite_compat_installed = True

    @compiles(UUID, "sqlite")
    def _compile_uuid_sqlite(type_, compiler, **kw):
        return "CHAR(36)"

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(type_, compiler, **kw):
        return "JSON"

    @event.listens_for(Engine, "connect")
    def _sqlite_functions(dbapi_conn, _connection_record):
        import sqlite3

        if not isinstance(dbapi_conn, sqlite3.Connection):
            return
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))

    @event.listens_for(Engine, "before_cursor_execute", retval=True)
    def _sqlite_sql(conn, _cursor, statement, parameters, _context, _executemany):
        if conn.dialect.name != "sqlite":
            return statement, parameters
        statement = statement.replace("::jsonb", "")
        statement = statement.replace("now()", "CURRENT_TIMESTAMP")
        return statement, parameters


_install_sqlite_compat()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
