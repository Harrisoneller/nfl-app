from __future__ import annotations

from app.services import experiment_service


def test_assignment_is_stable_for_same_session():
    a = experiment_service.assign_variant("insight_card_order_v1", "sess_123")
    b = experiment_service.assign_variant("insight_card_order_v1", "sess_123")
    assert a["variant"] == b["variant"]


def test_assignment_falls_back_for_unknown_experiment():
    out = experiment_service.assign_variant("unknown_exp", "sess_123")
    assert out["variant"] == "control"
    assert out["enabled"] is False
