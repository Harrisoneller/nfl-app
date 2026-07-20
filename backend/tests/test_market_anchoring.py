"""Price-aware prop anchoring — engine math + anchor-target selection."""
from __future__ import annotations

import math

import pytest

from app.services import player_projection_engine as engine


class TestMarketImpliedMean:
    def test_even_price_returns_line(self):
        assert engine.market_implied_mean(250.5, 0.50, 60.0) == pytest.approx(250.5)

    def test_juiced_over_shifts_mean_up(self):
        m = engine.market_implied_mean(250.5, 0.58, 60.0)
        assert m > 250.5
        # Φ⁻¹(0.58) ≈ 0.2019 → shift ≈ 12.1 yds on a 60-yd sd.
        assert m == pytest.approx(250.5 + 60.0 * 0.2019, abs=0.5)

    def test_missing_or_extreme_price_falls_back_to_line(self):
        assert engine.market_implied_mean(250.5, None, 60.0) == 250.5
        assert engine.market_implied_mean(250.5, 0.99, 60.0) == 250.5

    def test_shift_capped(self):
        up = engine.market_implied_mean(100.0, 0.92, 50.0)
        assert up - 100.0 <= 0.8 * 50.0 + 1e-9


class TestPoissonRateInversion:
    def test_closed_form_anytime_line(self):
        # P(X ≥ 1) = 0.55 → λ = −ln(0.45)
        lam = engine.poisson_rate_from_over_prob(0.5, 0.55)
        assert lam == pytest.approx(-math.log(0.45), abs=1e-9)

    @pytest.mark.parametrize("line,lam_true", [(0.5, 0.4), (1.5, 1.2), (2.5, 2.1)])
    def test_roundtrip_via_bisection(self, line: float, lam_true: float):
        # Forward: P(X > line) under Poisson(λ_true), then invert.
        k = int(line)
        term, cdf = math.exp(-lam_true), math.exp(-lam_true)
        for i in range(1, k + 1):
            term *= lam_true / i
            cdf += term
        p_over = 1.0 - cdf
        lam = engine.poisson_rate_from_over_prob(line, p_over)
        assert lam == pytest.approx(lam_true, abs=1e-4)

    def test_garbage_prices_rejected(self):
        assert engine.poisson_rate_from_over_prob(0.5, 0.995) is None
        assert engine.poisson_rate_from_over_prob(0.5, 0.01) is None
        assert engine.poisson_rate_from_over_prob(-1.0, 0.5) is None


class TestAnchorTarget:
    def test_scoring_requires_price(self):
        from app.services.player_predictions_service import _anchor_target

        # No price on a TD threshold → unanchorable (old behavior preserved).
        assert _anchor_target("passing_tds", {"line": 1.5, "over_prob": None}, 0.9) is None
        # With a price → Poisson-inverted rate, scoring cap.
        target = _anchor_target("passing_tds", {"line": 1.5, "over_prob": 0.45}, 0.9)
        assert target is not None
        lam, cap = target
        assert 0.5 < lam < 3.0
        assert cap == pytest.approx(0.30)

    def test_yardage_uses_price_aware_mean(self):
        from app.services.player_predictions_service import _anchor_target

        target = _anchor_target(
            "passing_yards", {"line": 250.5, "over_prob": 0.58}, 60.0,
        )
        assert target is not None
        mean, cap = target
        assert mean > 250.5
        assert cap == pytest.approx(0.40)
