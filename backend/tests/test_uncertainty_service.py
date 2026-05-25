from app.services import uncertainty_service


def test_calibration_score_from_ece():
    assert uncertainty_service.calibration_score_from_ece(None) == 0.5
    assert uncertainty_service.calibration_score_from_ece(0.0) == 1.0
    assert uncertainty_service.calibration_score_from_ece(0.25) == 0.5
    assert uncertainty_service.calibration_score_from_ece(0.8) == 0.0


def test_confidence_tier_prefers_tighter_and_calibrated_predictions():
    high = uncertainty_service.confidence_tier(
        home_win_prob=0.72,
        interval_low=0.65,
        interval_high=0.78,
        calibration_score=0.9,
    )
    low = uncertainty_service.confidence_tier(
        home_win_prob=0.55,
        interval_low=0.35,
        interval_high=0.75,
        calibration_score=0.4,
    )
    assert high == "high"
    assert low == "low"
