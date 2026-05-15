"""Smoke tests for the health endpoints — proves the app boots."""


def test_live_ok(client):
    r = client.get("/live")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "llm_provider" in body


def test_request_id_header(client):
    r = client.get("/live")
    assert "x-request-id" in {k.lower() for k in r.headers}
