"""Sign of the spread residual, and the walk-forward scorer."""
import numpy as np
import pandas as pd

from app.services import ml_predictions_service
from app.services.spread_residual_refit import (
    ats_hit,
    baseline_margin,
    bootstrap_mean_ci,
    fit_spread_residual,
    score_frame,
)


def _games(season: int, n: int, line: float) -> pd.DataFrame:
    rows = []
    for i in range(n):
        margin = line + 6.0
        row = {name: 0.0 for name in ml_predictions_service._FEATURES}
        row.update({
            "season": season,
            "week_number": 1 + (i % 17),
            "game_id": f"{season}-{i}",
            "game_type": "REG",
            "home_margin": float(margin),
            "market_spread_home": line,
            "short_week_home": 0,
            "short_week_away": 0,
            "weather_is_bad": 0,
            "qb_change_proxy_home": 0.0,
            "qb_change_proxy_away": 0.0,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def test_corrected_baseline_is_the_home_margin():
    line = pd.Series([4.0, -3.0])
    assert list(baseline_margin(line, negate=False)) == [4.0, -3.0]
    assert list(baseline_margin(line, negate=True)) == [-4.0, 3.0]


def test_refit_beats_the_negated_baseline_when_features_are_flat():
    train = pd.concat([_games(2018, 80, 4.0), _games(2019, 80, -3.0)], ignore_index=True)
    test = _games(2020, 40, 4.0)
    live = fit_spread_residual(train, test, negate_baseline=True)
    refit = fit_spread_residual(train, test, negate_baseline=False)
    live_score = score_frame(test, live)
    refit_score = score_frame(test, refit)
    assert refit_score["mae"].mean() < live_score["mae"].mean()
    assert refit_score["crps"].mean() < live_score["crps"].mean()
    # Close is +4 and the actual margin is 5 or 3, so home covers every time.
    # The corrected model, predicting near +4, takes home. The negated model
    # predicts near -4 and takes away.
    assert np.nanmean(refit_score["ats"]) > np.nanmean(live_score["ats"])


def test_ats_push_and_pickem_are_not_graded():
    assert np.isnan(ats_hit(4.0, 4.0, 10.0))
    assert np.isnan(ats_hit(7.0, 3.0, 3.0))
    assert ats_hit(7.0, 3.0, 10.0) == 1.0
    assert ats_hit(1.0, 4.0, -1.0) == 1.0


def test_bootstrap_interval_contains_the_mean():
    values = np.array([1.0, 2.0, 3.0, 4.0])
    ci = bootstrap_mean_ci(values, n_boot=500, seed=1)
    assert ci["lo"] <= ci["estimate"] <= ci["hi"]
    assert ci["n"] == 4
