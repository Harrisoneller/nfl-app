from __future__ import annotations

from starlette.requests import Request

from app.services import endpoint_slo_service


def test_quantile_works_for_even_samples():
    vals = [10.0, 20.0, 30.0, 40.0]
    assert endpoint_slo_service._quantile(vals, 0.50) == 25.0  # noqa: SLF001
    assert endpoint_slo_service._quantile(vals, 0.95) == 38.5  # noqa: SLF001


def test_snapshot_returns_tracked_route_metrics():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/h2h/PHI/SF",
        "headers": [],
        "query_string": b"",
        "route": type("Route", (), {"path": "/h2h/{team_a}/{team_b}"})(),
    }
    request = Request(scope)
    endpoint_slo_service.track_request(
        request,
        duration_ms=42.0,
        status_code=200,
        cache_status="hit",
    )
    rows = endpoint_slo_service.current_snapshot(window_seconds=3600)["rows"]
    assert any(r["endpoint"] == "/h2h/{team_a}/{team_b}" for r in rows)
