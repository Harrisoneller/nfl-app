"""Fantasy market layer — ADP weight decay, rank blending, row enrichment."""
from __future__ import annotations

import pytest

from app.services import fantasy_market_service as fm


class TestAdpWeight:
    def test_preseason_heavy_market(self):
        assert fm.adp_weight(0) == pytest.approx(0.55)

    def test_decays_with_weeks_to_floor(self):
        assert fm.adp_weight(4) == pytest.approx(0.55 - 4 * 0.045)
        assert fm.adp_weight(40) == pytest.approx(0.15)  # floor
        # Strictly non-increasing.
        ws = [fm.adp_weight(w) for w in range(0, 18)]
        assert all(a >= b for a, b in zip(ws, ws[1:]))


class TestConsensusRank:
    def test_no_adp_is_pure_model(self):
        assert fm.consensus_rank_score(7, None, 0) == 7.0

    def test_preseason_blend_sits_between_ranks(self):
        s = fm.consensus_rank_score(20, 5, 0)
        assert 5 < s < 20
        # Market rank is 5 and carries 55% preseason → closer to 5 than to 20.
        assert s < 12.5

    def test_late_season_leans_model(self):
        early = fm.consensus_rank_score(20, 5, 0)
        late = fm.consensus_rank_score(20, 5, 12)
        assert late > early  # market pull weakens


class TestAttachMarketContext:
    def test_rows_enriched_and_value_computed(self):
        rows = [
            {"rank": 1, "name": "Justin Jefferson"},
            {"rank": 2, "name": "Unknown Guy"},
        ]
        adp_map = {
            "justin jefferson": {
                "adp": 4.2, "adp_overall_rank": 4, "adp_pos_rank": 2,
                "position": "WR", "team": "MIN", "stdev": 1.1,
                "times_drafted": 200, "trending_adds": 1500,
            },
        }
        fm.attach_market_context(rows, adp_map, weeks_played=0)
        jj = rows[0]
        assert jj["market"]["adp"] == 4.2
        # Model rank 1 vs market rank 4 → market drafts him later → value +3.
        assert jj["market"]["value_vs_adp"] == 3
        # 0.45·1 + 0.55·4 = 2.65, rounded to one decimal by the service.
        assert jj["consensus_rank_score"] == pytest.approx(2.65, abs=0.06)
        # Unmatched player: nulls, model-rank passthrough.
        other = rows[1]
        assert other["market"]["adp"] is None
        assert other["consensus_rank_score"] == 2.0

    def test_name_normalization_matches_suffixes(self):
        rows = [{"rank": 1, "name": "Kenneth Walker III"}]
        adp_map = {"kenneth walker": {"adp": 20.0, "adp_overall_rank": 18,
                                      "adp_pos_rank": 9, "position": "RB",
                                      "team": "SEA", "stdev": 3.0,
                                      "times_drafted": 90, "trending_adds": None}}
        fm.attach_market_context(rows, adp_map, weeks_played=0)
        assert rows[0]["market"]["adp"] == 20.0
