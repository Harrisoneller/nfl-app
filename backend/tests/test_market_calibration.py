"""Season-board market calibration — TD shrink, durability, ADP anchoring.

These three mechanisms are the fix for "model ranks a player nowhere near the
market" (the Kyren Williams #3-overall report): scoring rates now regress
harder, durability discounts expected volume, and the board's season fantasy
numbers shrink toward the ADP-implied level.
"""
from __future__ import annotations

import pytest

from app.services import fantasy_market_service as fm
from app.services import player_projection_engine as eng


class TestScoringShrink:
    def _prior_mean(self, stat: str) -> float:
        # Same evidence for both stats: 2 seasons, well above the positional mean.
        seasons = [
            {"mean": 1.0, "sd": 0.5, "games": 17},
            {"mean": 1.0, "sd": 0.5, "games": 16},
        ]
        out = eng.build_prior(stat, seasons, position="RB", age=25, position_mean=0.5)
        assert out is not None
        return out[0]

    def test_scoring_regresses_harder_than_yardage(self):
        td_mean = self._prior_mean("rushing_tds")
        yd_mean = self._prior_mean("rushing_yards")
        vol_mean = self._prior_mean("carries")
        # All shrink toward 0.5; scoring most, volume least.
        assert td_mean < yd_mean < vol_mean < 1.0

    def test_scoring_shrink_magnitude(self):
        # 33 weighted games vs K=6 → posterior ≈ (33·1 + 6·0.5)/39 ≈ 0.923.
        td_mean = self._prior_mean("rushing_tds")
        assert td_mean == pytest.approx(0.923, abs=0.02)


class TestAvailability:
    def test_no_history_returns_positional_norm(self):
        assert eng.availability_rate([], "RB") == pytest.approx(0.87)
        assert eng.availability_rate([None, None, None], "QB") == pytest.approx(0.94)

    def test_iron_man_approaches_one(self):
        a = eng.availability_rate([17.0, 17.0, 17.0], "WR")
        assert 0.93 < a <= 1.0
        # Strictly above the norm — full slates are evidence of durability.
        assert a > eng.availability_rate([], "WR")

    def test_injury_history_discounts(self):
        hurt = eng.availability_rate([9.0, 11.0, 8.0], "RB")
        healthy = eng.availability_rate([17.0, 16.0, 17.0], "RB")
        assert hurt < eng.AVAILABILITY_NORM["RB"] < healthy
        assert hurt >= 0.65  # floor

    def test_recency_weighting(self):
        recent_injury = eng.availability_rate([8.0, 17.0, 17.0], "RB")
        old_injury = eng.availability_rate([17.0, 17.0, 8.0], "RB")
        assert recent_injury < old_injury


def _row(name: str, pos: str, per_game: float, games: int = 17) -> dict:
    def band(pg: float) -> dict:
        mean = pg * games
        return {"mean": mean, "p10": mean * 0.7, "p90": mean * 1.3,
                "per_game": pg}
    return {
        "name": name, "position": pos, "games_remaining": games,
        "fantasy_ppr": band(per_game),
        "fantasy_half_ppr": band(per_game * 0.9),
        "fantasy_standard": band(per_game * 0.8),
    }


class TestAdpAnchor:
    def _board(self) -> list[dict]:
        # Model's RB curve: 20, 18, 16, 14, 12, 10 ppg.
        return [
            _row(f"RB Guy{i}", "RB", pg)
            for i, pg in enumerate([20.0, 18.0, 16.0, 14.0, 12.0, 10.0])
        ]

    def test_overrated_player_pulled_toward_market(self):
        rows = self._board()
        # Model has Guy0 at RB1 (20 ppg); market says he's only RB5.
        adp_map = {
            "rb guy0": {"adp": 40.0, "adp_overall_rank": 40, "adp_pos_rank": 5,
                        "position": "RB", "team": None, "stdev": 3.0,
                        "times_drafted": 100, "trending_adds": None},
        }
        out = fm.apply_adp_anchor(rows, adp_map, weeks_played=0)
        anchored = out[0]["fantasy_ppr"]
        # Market-implied = our RB5 ppg = 12.0; w=0.55 → 0.45·20 + 0.55·12 = 15.6.
        assert anchored["per_game"] == pytest.approx(15.6, abs=0.05)
        assert anchored["adp_anchor"]["market_implied_per_game"] == pytest.approx(12.0)
        # Mean/p10/p90 scaled proportionally.
        assert anchored["mean"] == pytest.approx(15.6 * 17, rel=0.01)
        # Input rows untouched (they belong to a shared cache).
        assert rows[0]["fantasy_ppr"]["per_game"] == 20.0
        assert "adp_anchor" not in rows[0]["fantasy_ppr"]

    def test_market_agreement_barely_moves(self):
        rows = self._board()
        adp_map = {
            "rb guy1": {"adp": 15.0, "adp_overall_rank": 15, "adp_pos_rank": 2,
                        "position": "RB", "team": None, "stdev": 2.0,
                        "times_drafted": 100, "trending_adds": None},
        }
        out = fm.apply_adp_anchor(rows, adp_map, weeks_played=0)
        # Model RB2 = 18 ppg, market says RB2 → implied 18 → unchanged.
        assert out[1]["fantasy_ppr"]["per_game"] == pytest.approx(18.0)

    def test_no_adp_passthrough_and_position_mismatch_skipped(self):
        rows = self._board()
        adp_map = {
            "rb guy2": {"adp": 5.0, "adp_overall_rank": 5, "adp_pos_rank": 1,
                        "position": "WR", "team": None, "stdev": 1.0,
                        "times_drafted": 100, "trending_adds": None},
        }
        out = fm.apply_adp_anchor(rows, adp_map, weeks_played=0)
        assert out[2]["fantasy_ppr"]["per_game"] == 16.0  # WR entry ≠ RB row
        assert out[3] is rows[3]  # untouched rows pass through by identity

    def test_in_season_anchor_weakens(self):
        rows = self._board()
        adp_map = {
            "rb guy0": {"adp": 40.0, "adp_overall_rank": 40, "adp_pos_rank": 5,
                        "position": "RB", "team": None, "stdev": 3.0,
                        "times_drafted": 100, "trending_adds": None},
        }
        pre = fm.apply_adp_anchor(rows, adp_map, weeks_played=0)[0]["fantasy_ppr"]["per_game"]
        late = fm.apply_adp_anchor(rows, adp_map, weeks_played=10)[0]["fantasy_ppr"]["per_game"]
        # Later in the season the model keeps more of its disagreement.
        assert late > pre
