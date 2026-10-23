"""Anonymous widget share read.

GET /widgets/{id}/public serves the server-rendered /widget/[id] page, which
has no bearer token. Listing, saving, inline render, and delete stay
authenticated. The payload is a whitelist: widget definition plus rendered
data, never the owner id, email, chat, or private notes.

Widget primary keys are uuid4, so the id is an unguessable capability.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

EMAIL = "owner-secret@example.com"
CHAT = "secret-transcript-do-not-leak"
NOTES = "private-note-do-not-leak"

_SPEC = {
    "title": "Live scoreboard",
    "kind": "scoreboard",
    "data_source": {
        "service": "scores",
        "method": "current",
        "params": {"limit": 4},
    },
    "display": {"limit": 4},
    "description": "Today's games",
}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.user import User
    from app.models.widget import Widget
    from app.rate_limits import limiter

    Widget.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)
    Widget.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app

    limiter.enabled = False
    try:
        with TestClient(create_app()) as client:
            reg = client.post(
                "/auth/register",
                json={
                    "email": "owner@example.com",
                    "password": "securepass1",
                    "display_name": "Owner",
                },
            )
            assert reg.status_code == 200, reg.text
            headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
            me = client.get("/auth/me", headers=headers)
            assert me.status_code == 200, me.text
            owner_id = me.json()["id"]

            created = client.post(
                "/widgets",
                headers=headers,
                json={"spec": _SPEC, "pinned": True},
            )
            assert created.status_code == 200, created.text
            widget_id = created.json()["id"]
            _plant_private_fields(widget_id, owner_id)

            yield {
                "client": client,
                "headers": headers,
                "widget_id": widget_id,
                "owner_id": owner_id,
            }
    finally:
        limiter.enabled = True
        Widget.__table__.drop(bind=engine, checkfirst=True)
        User.__table__.drop(bind=engine, checkfirst=True)
        get_settings.cache_clear()


def _plant_private_fields(widget_id: str, owner_id: str) -> None:
    """Stuff owner and chat fields into the stored JSONB spec.

    The create API validates a WidgetSpec and drops these keys. A direct
    write is what a leaky column or a future writer would persist.
    """
    from app.db import SessionLocal
    from app.models.widget import Widget

    db = SessionLocal()
    try:
        widget = db.query(Widget).filter(Widget.id == uuid.UUID(widget_id)).one()
        spec = dict(widget.spec)
        source = dict(spec["data_source"])
        params = dict(source.get("params") or {})
        params["email"] = EMAIL
        params["user_id"] = owner_id
        source["params"] = params
        spec["data_source"] = source
        spec["email"] = EMAIL
        spec["user_id"] = owner_id
        spec["chat"] = CHAT
        spec["chat_content"] = CHAT
        spec["private_notes"] = NOTES
        spec["notes"] = NOTES
        widget.spec = spec
        db.commit()
    finally:
        db.close()


def _keys(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key).lower())
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


def test_widget_ids_are_uuid4_not_sequential(api):
    """Share links are safe only while ids stay unguessable."""
    from app.models.widget import Widget

    column = Widget.__table__.c.id
    assert isinstance(column.type, PG_UUID)
    assert not isinstance(column.type, Integer)
    # Identity (`is uuid.uuid4`) is unreliable if the uuid module was loaded
    # twice. The name plus a real inserted id's version is the contract.
    assert column.default.arg.__module__ == "uuid"
    assert column.default.arg.__name__ == "uuid4"

    parsed = uuid.UUID(api["widget_id"])
    assert parsed.version == 4
    assert str(parsed) != api["owner_id"]


def test_anonymous_public_fetch_hides_owner_fields(api, monkeypatch):
    client: TestClient = api["client"]
    widget_id = api["widget_id"]
    owner_id = api["owner_id"]

    async def fake_render(db, spec):
        # The public route must render the whitelisted spec, not the raw row.
        blob = str(spec)
        assert EMAIL not in blob
        assert owner_id not in blob
        assert CHAT not in blob
        assert NOTES not in blob
        return {
            "games": [{"id": "401772810", "status": "scheduled", "home_team_id": "KC"}],
            "user_id": owner_id,
            "email": EMAIL,
            "chat": CHAT,
            "chat_content": CHAT,
            "private_notes": NOTES,
            "notes": NOTES,
            "wrapper": {"email": EMAIL, "keep": "visible"},
        }

    monkeypatch.setattr("app.services.widget_service.render", fake_render)

    anon = client.get(f"/widgets/{widget_id}/public")
    assert anon.status_code == 200, anon.text
    body = anon.json()

    assert set(body.keys()) == {"widget", "data"}
    widget = body["widget"]
    assert set(widget.keys()) == {"id", "title", "kind", "spec"}
    assert widget["id"] == widget_id
    assert widget["title"] == "Live scoreboard"
    assert widget["kind"] == "scoreboard"
    assert widget["spec"]["description"] == "Today's games"
    assert widget["spec"]["data_source"]["service"] == "scores"
    assert widget["spec"]["data_source"]["method"] == "current"
    assert widget["spec"]["data_source"]["params"] == {"limit": 4}
    assert body["data"]["games"][0]["id"] == "401772810"
    assert body["data"]["wrapper"]["keep"] == "visible"

    forbidden = {
        "user_id",
        "userid",
        "owner_id",
        "email",
        "owner_email",
        "chat",
        "chats",
        "chat_content",
        "chat_message",
        "chat_messages",
        "transcript",
        "private_notes",
        "private_note",
        "notes",
        "note",
        "hashed_password",
        "password",
        "password_hash",
    }
    assert not (forbidden & _keys(body))
    text = anon.text
    assert EMAIL not in text
    assert CHAT not in text
    assert NOTES not in text
    assert owner_id not in text

    # A bad or missing token must not turn the share URL into a 401.
    with_junk = client.get(
        f"/widgets/{widget_id}/public",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert with_junk.status_code == 200

    missing = client.get(f"/widgets/{uuid.uuid4()}/public")
    assert missing.status_code == 404


def test_listing_saving_rendering_and_deleting_require_auth(api):
    client: TestClient = api["client"]
    widget_id = api["widget_id"]
    headers = api["headers"]

    listing = client.get("/widgets")
    assert listing.status_code == 401
    assert listing.json()["detail"] == "Missing bearer token"

    saving = client.post("/widgets", json={"spec": _SPEC, "pinned": False})
    assert saving.status_code == 401

    inline = client.post("/widgets/render", json=_SPEC)
    assert inline.status_code == 401

    by_id = client.get(f"/widgets/{widget_id}/render")
    assert by_id.status_code == 401

    deleting = client.delete(f"/widgets/{widget_id}")
    assert deleting.status_code == 401

    owned = client.get("/widgets", headers=headers)
    assert owned.status_code == 200
    ids = [row["id"] for row in owned.json()]
    assert widget_id in ids
    # The authenticated list is the owner's own row, but the response model
    # still has no owner column.
    assert "user_id" not in owned.json()[0]
    assert "email" not in owned.json()[0]
