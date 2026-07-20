"""Custom fantasy ranking sets: admin gate, CRUD, board replace semantics,
tier validation, draft → publish snapshots, and the public read path.

Follows the ``test_admin_overrides.py`` fixture style — own tables,
multi-user mode so the require_admin gate is actually exercised. The players
table is created with raw DDL because the Player model uses Postgres JSONB,
which SQLite's DDL compiler can't render.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    # The repo-root .env may set ADMIN_EMAILS; a real env var takes priority
    # in pydantic-settings, so blank it to exercise the DB is_admin path.
    monkeypatch.setenv("ADMIN_EMAILS", "")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.model_param import AdminAuditLog
    from app.models.ranking import RankingEntry, RankingSet
    from app.models.user import User

    for t in (RankingEntry, RankingSet, AdminAuditLog, User):
        t.__table__.drop(bind=engine, checkfirst=True)
    for t in (User, AdminAuditLog, RankingSet, RankingEntry):
        t.__table__.create(bind=engine, checkfirst=True)

    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS players"))
        c.execute(text(
            "CREATE TABLE players (id VARCHAR PRIMARY KEY, gsis_id VARCHAR,"
            " espn_id VARCHAR, full_name VARCHAR, position VARCHAR,"
            " team_id VARCHAR, jersey_number INT, age INT, height VARCHAR,"
            " weight INT, college VARCHAR, status VARCHAR, metadata_json TEXT,"
            " created_at DATETIME, updated_at DATETIME)"
        ))
        c.execute(text(
            "INSERT INTO players (id, full_name, position, team_id, metadata_json)"
            " VALUES ('rb1','Bell Cow','RB','KC','{}'),"
            "        ('wr1','Alpha Wideout','WR','CIN','{}'),"
            "        ('qb1','Franchise Guy','QB','BUF','{}')"
        ))

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    for t in (RankingEntry, RankingSet, AdminAuditLog, User):
        t.__table__.drop(bind=engine, checkfirst=True)
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS players"))
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
        u = db.query(User).filter(User.email == email).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


BOARD = [
    {"player_id": "rb1", "tier": 1, "note": "locked in at 1.01"},
    {"player_id": "wr1", "tier": 1},
    {"player_id": "qb1", "tier": 2, "note": "superflex bump"},
]


def test_admin_gate(client):
    user_h = _register(client, "pleb@example.com")
    assert client.get("/admin/rankings", headers=user_h).status_code == 403
    assert client.get("/admin/rankings").status_code == 401
    # Public list is open and empty.
    r = client.get("/fantasy/rankings")
    assert r.status_code == 200
    assert r.json()["sets"] == []


def test_full_lifecycle(client):
    admin_h = _register(client, "boss@example.com")
    _make_admin("boss@example.com")

    # Create.
    r = client.post(
        "/admin/rankings",
        json={"name": "Superflex Big Board", "season": 2026, "format": "superflex"},
        headers=admin_h,
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "draft" and s["version"] == 0
    sid = s["id"]

    # Duplicate name within a season → 422.
    r = client.post(
        "/admin/rankings",
        json={"name": "Superflex Big Board", "season": 2026},
        headers=admin_h,
    )
    assert r.status_code == 422

    # Replace entries: order is the ranking, ranks assigned server-side.
    r = client.put(
        f"/admin/rankings/{sid}/entries", json={"entries": BOARD}, headers=admin_h,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert [e["rank"] for e in d["entries"]] == [1, 2, 3]
    assert d["entries"][0]["name"] == "Bell Cow"  # enriched
    assert d["has_unpublished_changes"] is True

    # Unknown player → 422; duplicate player → 422; decreasing tier → 422.
    for bad in (
        [{"player_id": "nobody"}],
        [{"player_id": "rb1"}, {"player_id": "rb1"}],
        [{"player_id": "rb1", "tier": 2}, {"player_id": "wr1", "tier": 1}],
    ):
        r = client.put(
            f"/admin/rankings/{sid}/entries", json={"entries": bad}, headers=admin_h,
        )
        assert r.status_code == 422, bad

    # Draft is invisible publicly; publishing snapshots it.
    assert client.get(f"/fantasy/rankings/{sid}").status_code == 404
    r = client.post(f"/admin/rankings/{sid}/publish", headers=admin_h)
    assert r.status_code == 200
    pub = r.json()
    assert pub["status"] == "published" and pub["version"] == 1
    assert pub["has_unpublished_changes"] is False

    r = client.get(f"/fantasy/rankings/{sid}")
    assert r.status_code == 200
    board = r.json()
    assert board["count"] == 3
    assert board["players"][0]["player_id"] == "rb1"
    assert board["players"][2]["tier"] == 2
    assert board["players"][0]["note"] == "locked in at 1.01"

    # Draft edits don't leak into the published snapshot.
    reordered = [BOARD[1], BOARD[0], {"player_id": "qb1", "tier": 1}]
    # fix tiers to be valid after reorder (all tier 1)
    reordered = [
        {"player_id": "wr1", "tier": 1},
        {"player_id": "rb1", "tier": 1},
        {"player_id": "qb1", "tier": 1},
    ]
    r = client.put(
        f"/admin/rankings/{sid}/entries", json={"entries": reordered}, headers=admin_h,
    )
    assert r.status_code == 200
    assert r.json()["has_unpublished_changes"] is True
    assert client.get(f"/fantasy/rankings/{sid}").json()["players"][0]["player_id"] == "rb1"

    # Re-publish picks up the reorder and bumps the version.
    client.post(f"/admin/rankings/{sid}/publish", headers=admin_h)
    board = client.get(f"/fantasy/rankings/{sid}").json()
    assert board["version"] == 2
    assert board["players"][0]["player_id"] == "wr1"

    # Public meta list shows it; unpublish pulls it.
    assert len(client.get("/fantasy/rankings?season=2026").json()["sets"]) == 1
    r = client.post(f"/admin/rankings/{sid}/unpublish", headers=admin_h)
    assert r.status_code == 200
    assert client.get("/fantasy/rankings?season=2026").json()["sets"] == []
    assert client.get(f"/fantasy/rankings/{sid}").status_code == 404

    # Delete removes set + entries.
    assert client.delete(f"/admin/rankings/{sid}", headers=admin_h).status_code == 200
    assert client.get(f"/admin/rankings/{sid}", headers=admin_h).status_code == 404


def test_publish_empty_board_rejected(client):
    admin_h = _register(client, "boss2@example.com")
    _make_admin("boss2@example.com")
    sid = client.post(
        "/admin/rankings", json={"name": "Empty", "season": 2026}, headers=admin_h,
    ).json()["id"]
    assert client.post(f"/admin/rankings/{sid}/publish", headers=admin_h).status_code == 422
