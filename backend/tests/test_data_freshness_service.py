from app.services import data_freshness_service


def test_freshness_status_thresholds():
    assert data_freshness_service.freshness_status(10, 30) == "ok"
    assert data_freshness_service.freshness_status(40, 30) == "warn"
    assert data_freshness_service.freshness_status(100, 30) == "stale"
    assert data_freshness_service.freshness_status(None, 30) == "stale"
