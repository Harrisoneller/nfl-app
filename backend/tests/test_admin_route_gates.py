"""Every admin route requires require_admin.

Anonymous callers get 401. A signed-in non-admin gets 403. An admin reaches
the handler. Covers /admin/* (including /admin/cost-summary), /predictions/admin/*,
and /sparky/admin/*.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# Paths that were missing a gate before this change. The sweep below covers
# the whole admin surface; these must stay in that sweep.
_PREVIOUSLY_OPEN = {
    ("GET", "/admin/cost-summary"),
    ("POST", "/predictions/admin/elo/rebuild"),
    ("POST", "/predictions/admin/ml/train"),
    ("GET", "/predictions/admin/lifecycle/latest"),
    ("POST", "/predictions/admin/lifecycle/run"),
}

_SKIP_METHODS = {"HEAD", "OPTIONS"}
_PARAM = re.compile(r"\{[^}]+\}")


def _is_admin_path(path: str) -> bool:
    return "admin" in [part for part in path.split("/") if part]


def _admin_operations(app) -> list[tuple[str, str]]:
    """Public admin operations from the OpenAPI schema.

    This FastAPI version keeps included routers wrapped, so ``app.routes``
    does not list the concrete paths. The schema does.
    """
    ops: list[tuple[str, str]] = []
    for path, methods in app.openapi()["paths"].items():
        if not _is_admin_path(path):
            continue
        for method in methods:
            if method == "parameters":
                continue
            verb = method.upper()
            if verb in _SKIP_METHODS:
                continue
            ops.append((verb, path))
    return ops


def _concrete(path: str) -> str:
    return _PARAM.sub("1", path)


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    # A repo-root .env may set ADMIN_EMAILS. Blank it so the DB is_admin
    # flag is what decides, matching the other admin tests.
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setenv("APP_ROLE", "web")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.user import User

    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app
    from app.rate_limits import limiter

    # The sweep hits every admin route twice. The default 60/minute limit
    # would turn the tail of the sweep into 429s.
    limiter.enabled = False
    try:
        with TestClient(create_app()) as client:
            admin_h = _register(client, "boss@example.com")
            _make_admin("boss@example.com")
            user_h = _register(client, "pleb@example.com")
            yield SimpleNamespace(client=client, admin=admin_h, user=user_h)
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


def _make_admin(email: str) -> None:
    from app.db import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None
        user.is_admin = True
        db.commit()
    finally:
        db.close()


def test_public_health_stays_open(gate):
    client = gate.client
    assert client.get("/live").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_every_admin_route_rejects_anonymous_and_non_admin(gate):
    client: TestClient = gate.client
    ops = _admin_operations(client.app)
    found = set(ops)
    missing = _PREVIOUSLY_OPEN - found
    assert not missing, f"expected admin routes were not mounted: {sorted(missing)}"
    assert len(ops) >= len(_PREVIOUSLY_OPEN)

    failures: list[str] = []
    for method, path in ops:
        url = _concrete(path)
        anon = client.request(method, url)
        if anon.status_code != 401 or anon.json().get("detail") != "Missing bearer token":
            failures.append(
                f"anon {method} {path} -> {anon.status_code} {anon.text[:180]}"
            )
        user = client.request(method, url, headers=gate.user)
        if user.status_code != 403 or user.json().get("detail") != "Admin access required":
            failures.append(
                f"user {method} {path} -> {user.status_code} {user.text[:180]}"
            )

    bad = client.get(
        "/admin/cost-summary",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Invalid token"

    assert not failures, "admin gate mismatches:\n" + "\n".join(failures)


def test_admin_reaches_previously_open_routes(gate, monkeypatch):
    """The gate must let an admin through, not 403 every caller."""
    client: TestClient = gate.client

    async def fake_rebuild(db, seasons):
        return 4

    async def fake_train(db, seasons):
        return {"trained": True, "seasons": seasons}

    async def fake_lifecycle(db, season=None, force=False):
        return {"decision": "hold", "season": season, "force": force}

    monkeypatch.setattr(
        "app.services.elo_service.rebuild_history", fake_rebuild,
    )
    monkeypatch.setattr(
        "app.services.ml_predictions_service.train", fake_train,
    )
    monkeypatch.setattr(
        "app.services.model_lifecycle_service.latest_run",
        lambda db: {"id": "latest"},
    )
    monkeypatch.setattr(
        "app.services.model_lifecycle_service.latest_promoted_run",
        lambda db: None,
    )
    monkeypatch.setattr(
        "app.services.model_lifecycle_service.run_weekly_lifecycle",
        fake_lifecycle,
    )

    cost = client.get("/admin/cost-summary", headers=gate.admin)
    assert cost.status_code == 200, cost.text
    body = cost.json()
    assert "global" in body and "top_users" in body

    rebuild = client.post(
        "/predictions/admin/elo/rebuild?seasons=2024", headers=gate.admin,
    )
    assert rebuild.status_code == 200, rebuild.text
    assert rebuild.json() == {"seasons": [2024], "rows_written": 4}

    train = client.post(
        "/predictions/admin/ml/train?seasons=2024", headers=gate.admin,
    )
    assert train.status_code == 200, train.text
    assert train.json()["trained"] is True
    assert train.json()["seasons"] == [2024]

    latest = client.get("/predictions/admin/lifecycle/latest", headers=gate.admin)
    assert latest.status_code == 200, latest.text
    assert latest.json() == {"latest": {"id": "latest"}, "champion": None}

    run = client.post(
        "/predictions/admin/lifecycle/run?season=2024&force=true",
        headers=gate.admin,
    )
    assert run.status_code == 200, run.text
    assert run.json()["decision"] == "hold"
    assert run.json()["force"] is True

    # Non-admin still cannot trigger the rebuild even with a valid body.
    denied = client.post(
        "/predictions/admin/elo/rebuild?seasons=2024", headers=gate.user,
    )
    assert denied.status_code == 403
