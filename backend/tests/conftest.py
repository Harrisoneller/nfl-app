"""Test fixtures.

Uses a per-test SQLite in-memory DB so tests don't need a running Postgres.
Production code uses Postgres-specific column types (JSONB, UUID), so we
override the DATABASE_URL before app import and skip the migration test
suite for SQLite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Run from repo root: pytest backend/tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Use a throwaway sqlite DB for unit-style tests.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-key")


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient — boots the app once for the whole session."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
