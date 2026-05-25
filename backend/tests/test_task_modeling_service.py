from app.services import task_modeling_service


def test_exponential_recency_weight_decay():
    newest = task_modeling_service.exponential_recency_weight(
        current_index=100,
        sample_index=100,
        half_life=6.0,
    )
    older = task_modeling_service.exponential_recency_weight(
        current_index=100,
        sample_index=88,
        half_life=6.0,
    )
    assert newest > older
    assert round(newest, 3) == 1.0


def test_market_and_residual_combination_with_fallback():
    with_market = task_modeling_service.combine_market_and_residual(
        baseline_value=-2.5,
        residual_value=1.2,
        fallback_baseline=0.0,
    )
    without_market = task_modeling_service.combine_market_and_residual(
        baseline_value=None,
        residual_value=1.2,
        fallback_baseline=0.0,
    )
    assert round(with_market, 3) == -1.3
    assert round(without_market, 3) == 1.2


def test_platt_calibration_training_and_application():
    raw_probs = [0.2, 0.3, 0.4, 0.6, 0.75, 0.8] * 8
    outcomes = [0, 0, 0, 1, 1, 1] * 8
    artifact = task_modeling_service.fit_platt_calibration(
        raw_probs=raw_probs,
        outcomes=outcomes,
    )
    calibrated = [task_modeling_service.apply_platt_calibration(p, artifact) for p in raw_probs]
    assert artifact["method"] in {"platt", "identity"}
    assert all(0.0 < p < 1.0 for p in calibrated)
