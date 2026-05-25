"""Auth register/login/me flow (multi-user mode)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.user import User

    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client

    User.__table__.drop(bind=engine, checkfirst=True)
    get_settings.cache_clear()


def test_register_login_and_me(auth_client: TestClient):
    email = "fan@example.com"
    password = "securepass1"

    reg = auth_client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "Fan"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    assert token

    dup = auth_client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert dup.status_code == 409

    bad = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "wrongpass"},
    )
    assert bad.status_code == 401

    login = auth_client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["display_name"] == "Fan"

    patch = auth_client.patch(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "Super Fan"},
    )
    assert patch.status_code == 200
    assert patch.json()["display_name"] == "Super Fan"


def test_register_password_too_short(auth_client: TestClient):
    r = auth_client.post(
        "/auth/register",
        json={"email": "short@example.com", "password": "abc"},
    )
    assert r.status_code == 422
