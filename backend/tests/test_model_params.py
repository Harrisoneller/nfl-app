"""Global tuning layer: registry resolution, bounds/cross-param validation,
audit trail, presets, overlay, and the require_admin gate on /admin/params.

Follows the ``test_admin_overrides.py`` fixture style.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    # The repo .env pins ADMIN_EMAILS; blank it so the DB is_admin path is
    # what these tests exercise (env vars beat .env in pydantic-settings).
    monkeypatch.setenv("ADMIN_EMAILS", "")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.cache import cache
    from app.db import engine
    from app.models.model_param import AdminAuditLog, ModelParam, ModelParamPreset
    from app.models.user import User

    for t in (ModelParam, AdminAuditLog, ModelParamPreset, User):
        t.__table__.drop(bind=engine, checkfirst=True)
    for t in (User, ModelParam, AdminAuditLog, ModelParamPreset):
        t.__table__.create(bind=engine, checkfirst=True)
    cache.clear()

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    for t in (ModelParam, AdminAuditLog, ModelParamPreset, User):
        t.__table__.drop(bind=engine, checkfirst=True)
    cache.clear()
    get_settings.cache_clear()


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22!", "display_name": "T"},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_admin(email: str) -> None:
    from app.db import SessionLocal, engine
    from app.models.user import User

    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email).one()
        u.is_admin = True
        db.commit()
    # Old pooled SQLite connections can hold a pre-commit read snapshot;
    # recycle them so the app sees the flag immediately.
    engine.dispose()


@pytest.fixture
def admin_headers(client):
    h = _register(client, "boss@example.com")
    _make_admin("boss@example.com")
    return h


# ---- Registry resolution (pure service level) --------------------------------


def test_value_falls_back_to_default(client):
    from app.services import param_registry

    assert param_registry.value("elo.k_factor") == 20.0
    with pytest.raises(KeyError):
        param_registry.value("nope.not_a_param")


def test_set_param_changes_effective_value_and_audits(client, admin_headers):
    from app.services import param_registry

    r = client.put(
        "/admin/params/values/elo.k_factor",
        json={"value": 28.0, "note": "faster reaction mid-season"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_overridden"] is True
    assert param_registry.value("elo.k_factor") == 28.0

    # Audit trail has the change with old + new values.
    a = client.get("/admin/params/audit", headers=admin_headers).json()
    entry = a["entries"][0]
    assert entry["action"] == "param_set"
    assert entry["target_key"] == "elo.k_factor"
    assert entry["old_value"] == 20.0
    assert entry["new_value"] == 28.0
    assert entry["note"] == "faster reaction mid-season"
    assert entry["actor"] == "boss@example.com"


def test_revert_restores_default(client, admin_headers):
    from app.services import param_registry

    client.put("/admin/params/values/dist.margin_sigma",
               json={"value": 14.2}, headers=admin_headers)
    assert param_registry.value("dist.margin_sigma") == 14.2
    r = client.delete("/admin/params/values/dist.margin_sigma", headers=admin_headers)
    assert r.status_code == 200
    assert param_registry.value("dist.margin_sigma") == 13.5


def test_bounds_validation(client, admin_headers):
    r = client.put("/admin/params/values/market.w_cap",
                   json={"value": 3.0}, headers=admin_headers)
    assert r.status_code == 422
    r = client.put("/admin/params/values/not.a.param",
                   json={"value": 1.0}, headers=admin_headers)
    assert r.status_code == 422


def test_cross_param_rule_lo_below_hi(client, admin_headers):
    # defense.clamp_lo must stay below defense.clamp_hi (default 1.25).
    r = client.put("/admin/params/values/defense.clamp_lo",
                   json={"value": 0.9}, headers=admin_headers)
    assert r.status_code == 200
    r = client.put("/admin/params/values/defense.clamp_hi",
                   json={"value": 1.0}, headers=admin_headers)
    assert r.status_code == 200  # 0.9 < 1.0 ok
    r = client.put("/admin/params/values/defense.clamp_hi",
                   json={"value": 0.85}, headers=admin_headers)
    assert r.status_code == 422  # would invert the pair


def test_list_params_shape(client, admin_headers):
    r = client.get("/admin/params", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] >= 70  # expanded registry (weather/injury/priors/…)
    cats = {c["id"]: c for c in body["categories"]}
    assert "elo" in cats and "market_blend" in cats
    assert "weather" in cats and "injury" in cats
    p = next(p for c in body["categories"] for p in c["params"]
             if p["key"] == "market.w_base")
    assert p["default"] == 0.30 and "min" in p and "description" in p
    # New prior/weather/injury keys are present and bounds-validated.
    keys = {p["key"] for c in body["categories"] for p in c["params"]}
    for k in (
        "player.prior_n_scoring", "player.shrink_k_scoring",
        "player.env_clamp_lo", "weather.wind_high_mph",
        "injury.doubtful_mult", "levers.def_ypp_elasticity",
        "levers.availability_clamp_lo",
    ):
        assert k in keys


def test_requires_admin(client):
    h = _register(client, "pleb@example.com")
    assert client.get("/admin/params", headers=h).status_code == 403
    assert client.put("/admin/params/values/elo.k_factor",
                      json={"value": 25}, headers=h).status_code == 403


# ---- Presets -----------------------------------------------------------------


def test_preset_save_apply_revert_cycle(client, admin_headers):
    from app.services import param_registry

    client.put("/admin/params/values/elo.k_factor",
               json={"value": 30.0}, headers=admin_headers)
    client.put("/admin/params/values/market.w_cap",
               json={"value": 0.9}, headers=admin_headers)

    r = client.post("/admin/params/presets",
                    json={"name": "aggressive", "description": "hot takes"},
                    headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["params"] == {"elo.k_factor": 30.0, "market.w_cap": 0.9}

    # Change the world, then apply the preset back.
    client.put("/admin/params/values/elo.k_factor",
               json={"value": 15.0}, headers=admin_headers)
    client.put("/admin/params/values/adp.weight_floor",
               json={"value": 0.25}, headers=admin_headers)

    r = client.post("/admin/params/presets/aggressive/apply",
                    json={}, headers=admin_headers)
    assert r.status_code == 200
    assert param_registry.value("elo.k_factor") == 30.0
    assert param_registry.value("market.w_cap") == 0.9
    # Key not in preset reverted to default.
    assert param_registry.value("adp.weight_floor") == 0.15

    assert client.delete("/admin/params/presets/aggressive",
                         headers=admin_headers).status_code == 200
    assert client.post("/admin/params/presets/aggressive/apply",
                       json={}, headers=admin_headers).status_code == 404


def test_revert_all(client, admin_headers):
    from app.services import param_registry

    client.put("/admin/params/values/elo.k_factor",
               json={"value": 35.0}, headers=admin_headers)
    r = client.post("/admin/params/revert-all", json={"note": "clean slate"},
                    headers=admin_headers)
    assert r.status_code == 200
    assert "elo.k_factor" in r.json()["reverted"]
    assert param_registry.value("elo.k_factor") == 20.0


# ---- Overlay (impact-preview plumbing) ---------------------------------------


def test_overlay_is_context_local_and_never_persists(client):
    from app.services import param_registry

    with param_registry.overlay({"elo.k_factor": 40.0, "junk.key": 9}):
        assert param_registry.value("elo.k_factor") == 40.0
        # Overlay changes the downstream cache version token.
        from app.db import SessionLocal
        with SessionLocal() as db:
            assert param_registry.version(db).startswith("mp-preview-")
    assert param_registry.value("elo.k_factor") == 20.0


def test_wired_services_read_registry(client, admin_headers):
    """A param write actually changes model math, no restart needed."""
    from app.services import elo_service, prediction_dist

    base_spread = elo_service.predicted_spread(1600.0, 1500.0)
    client.put("/admin/params/values/elo.home_field_advantage",
               json={"value": 0.0, "note": "no HFA"}, headers=admin_headers)
    assert abs(elo_service.predicted_spread(1600.0, 1500.0)) < abs(base_spread)

    client.put("/admin/params/values/dist.margin_sigma",
               json={"value": 9.0}, headers=admin_headers)
    # Tighter sigma → same margin implies more confident win prob.
    assert prediction_dist.win_prob(7.0) > 0.72


# ---- Override audit unification ---------------------------------------------


def test_override_writes_land_in_unified_audit(client, admin_headers):
    from app.models.admin_override import AdminOverride
    from app.db import engine

    AdminOverride.__table__.create(bind=engine, checkfirst=True)
    try:
        r = client.post(
            "/admin/overrides",
            json={"entity_type": "game", "entity_id": "2026_01_KC_BUF",
                  "field": "predicted_total", "value": 51.5,
                  "original_value": 48.0, "note": "weather"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        a = client.get("/admin/params/audit?target_type=override",
                       headers=admin_headers).json()
        entry = a["entries"][0]
        assert entry["action"] == "override_set"
        assert entry["target_key"] == "game:2026_01_KC_BUF:predicted_total"
        assert entry["new_value"] == 51.5
    finally:
        AdminOverride.__table__.drop(bind=engine, checkfirst=True)


# ---- Bulk set / snapshot / status -------------------------------------------


def test_bulk_set_atomic_and_audited(client, admin_headers):
    from app.services import param_registry

    # Cross-param failure rolls back — nothing applied.
    r = client.post(
        "/admin/params/bulk",
        json={"changes": {"defense.clamp_lo": 1.2, "defense.clamp_hi": 1.0},
              "note": "bad pair"},
        headers=admin_headers,
    )
    assert r.status_code == 422
    assert param_registry.value("defense.clamp_lo") == 0.80

    r = client.post(
        "/admin/params/bulk",
        json={"changes": {"elo.k_factor": 32.0, "dist.margin_sigma": 14.0},
              "note": "bulk mid-season"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert param_registry.value("elo.k_factor") == 32.0
    assert param_registry.value("dist.margin_sigma") == 14.0


def test_weather_and_injury_params_wire_into_math(client, admin_headers):
    from app.services import player_predictions_service as pps

    # Default outdoor moderate wind applies pass penalty.
    weather = {"available": True, "is_indoor": False, "wind_mph": 18,
               "precipitation_in": 0, "temperature_f": 55}
    base = pps.weather_multiplier(weather, "passing_yards")
    assert base < 1.0

    client.put("/admin/params/values/weather.pass_wind_mod_mult",
               json={"value": 0.70}, headers=admin_headers)
    assert pps.weather_multiplier(weather, "passing_yards") == pytest.approx(0.70)

    assert pps.injury_multiplier("DOUBTFUL") == pytest.approx(0.30)
    client.put("/admin/params/values/injury.doubtful_mult",
               json={"value": 0.10}, headers=admin_headers)
    assert pps.injury_multiplier("DOUBTFUL") == pytest.approx(0.10)
    assert pps.injury_multiplier("OUT") == 0.0  # always zero, not tunable


def test_snapshot_export_import_roundtrip(client, admin_headers):
    from app.models.admin_override import AdminOverride
    from app.db import engine
    from app.services import param_registry

    AdminOverride.__table__.create(bind=engine, checkfirst=True)
    try:
        client.put("/admin/params/values/elo.k_factor",
                   json={"value": 28.0}, headers=admin_headers)
        client.post(
            "/admin/overrides",
            json={"entity_type": "team", "entity_id": "KC", "field": "pace",
                  "value": 68.0, "season": 2026, "original_value": 64.0,
                  "note": "new OC"},
            headers=admin_headers,
        )

        snap = client.get("/admin/params/snapshot", headers=admin_headers).json()
        assert snap["counts"]["params"] >= 1
        assert snap["counts"]["team_input_levers"] >= 1
        assert "elo.k_factor" in snap["params"]

        status = client.get("/admin/params/status", headers=admin_headers).json()
        assert status["counts"]["params"] >= 1
        assert status["registry_total"] >= 70
        assert any(c["params"] for c in status["params_by_category"])

        # Wipe params, re-import merge.
        client.post("/admin/params/revert-all", json={}, headers=admin_headers)
        assert param_registry.value("elo.k_factor") == 20.0

        r = client.post(
            "/admin/params/import",
            json={"snapshot": snap, "note": "restore", "include_overrides": False},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert param_registry.value("elo.k_factor") == 28.0
        assert "elo.k_factor" in r.json()["params_applied"]
    finally:
        AdminOverride.__table__.drop(bind=engine, checkfirst=True)
