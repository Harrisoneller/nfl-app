from app.services import model_lifecycle_service


def test_evaluate_gates_approves_good_challenger():
    challenger = {
        "n_games": 300,
        "brier_score": 0.21,
        "log_loss": 0.66,
        "classifier_accuracy_pct": 54.0,
        "ats_correct_pct": 51.0,
    }
    champion = {
        "brier_score": 0.215,
        "classifier_accuracy_pct": 53.8,
    }
    result = model_lifecycle_service.evaluate_gates(challenger, champion)
    assert result["approved"] is True


def test_auto_promote_is_off_unless_enabled(monkeypatch):
    assert model_lifecycle_service.AUTO_PROMOTE_ENABLED is False
    assert model_lifecycle_service.should_auto_promote(True) is False
    assert model_lifecycle_service.should_auto_promote(False) is False

    monkeypatch.setattr(model_lifecycle_service, "AUTO_PROMOTE_ENABLED", True)
    assert model_lifecycle_service.should_auto_promote(True) is True
    assert model_lifecycle_service.should_auto_promote(False) is False


def test_evaluate_gates_rejects_poor_challenger():
    challenger = {
        "n_games": 50,
        "brier_score": 0.28,
        "log_loss": 0.73,
        "classifier_accuracy_pct": 49.0,
        "ats_correct_pct": 46.0,
    }
    result = model_lifecycle_service.evaluate_gates(challenger, None)
    assert result["approved"] is False
