"""Admin gate in single-user mode and in ADMIN_EMAILS mode.

Single-user mode (MULTI_USER_MODE=false, no allowlist) treats every caller
as the seeded system admin. The allowlist, when multi-user mode is on,
decides admin access instead of the DB is_admin flag.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_ROLE", "web")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.user import User

    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app
    from app.rate_limits import limiter

    limiter.enabled = False
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        limiter.enabled = True
        User.__table__.drop(bind=engine, checkfirst=True)
        get_settings.cache_clear()


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22!", "display_name": "T"},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _set_admin(email: str, is_admin: bool) -> None:
    from app.db import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        user.is_admin = is_admin
        db.commit()
    finally:
        db.close()


def test_single_user_mode_anonymous_is_admin(monkeypatch, client):
    monkeypatch.setenv("MULTI_USER_MODE", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    from app.config import get_settings

    get_settings.cache_clear()
    response = client.get("/admin/cost-summary")
    assert response.status_code == 200, response.text
    assert "global" in response.json()


def test_admin_emails_allowlist(monkeypatch, client):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    from app.config import get_settings

    get_settings.cache_clear()

    boss = _register(client, "boss@example.com")
    pleb = _register(client, "pleb@example.com")
    # DB flag must not override the allowlist in either direction.
    _set_admin("boss@example.com", False)
    _set_admin("pleb@example.com", True)

    anon = client.get("/admin/cost-summary")
    assert anon.status_code == 401
    assert anon.json()["detail"] == "Missing bearer token"

    denied = client.get("/admin/cost-summary", headers=pleb)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Admin access required"

    allowed = client.get("/admin/cost-summary", headers=boss)
    assert allowed.status_code == 200, allowed.text

    # A second admin email in the comma-separated list is also accepted.
    monkeypatch.setenv("ADMIN_EMAILS", "other@example.com, boss@example.com")
    get_settings.cache_clear()
    still = client.get("/admin/cost-summary", headers=boss)
    assert still.status_code == 200, still.text


def test_allowlist_with_single_user_mode_rejects_everyone(monkeypatch, client):
    """Tokens are ignored, and system@local is not on the allowlist."""
    monkeypatch.setenv("MULTI_USER_MODE", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    from app.config import get_settings

    get_settings.cache_clear()

    anon = client.get("/admin/cost-summary")
    assert anon.status_code == 403
    assert anon.json()["detail"] == "Admin access required"

    # A bearer token does not help: single-user mode never reads it.
    headers = _register(client, "boss@example.com")
    authed = client.get("/admin/cost-summary", headers=headers)
    assert authed.status_code == 403
    assert authed.json()["detail"] == "Admin access required"
