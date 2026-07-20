"""Model-input levers — team scoring adjustments, tilts, player multipliers."""
from __future__ import annotations

import pytest

from app.services import model_inputs_service as mi
from app.services import overrides_service


class TestAdjustedTeamAggregates:
    def _aggs(self) -> dict:
        return {
            "KC": {
                "points_per_game": 26.0,
                "off_plays_per_game": 64.0,
                "off_yards_per_play": 5.8,
                "pass_rate_neutral": 0.58,
            },
            "BUF": {"points_per_game": 25.0, "off_plays_per_game": 63.0},
        }

    def test_no_overrides_returns_input(self, monkeypatch):
        monkeypatch.setattr(overrides_service, "team_input_overrides", lambda db, s: {})
        aggs = self._aggs()
        assert mi.adjusted_team_aggregates(None, 2026, aggs) is aggs

    def test_pace_override_scales_ppg(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"pace": 70.4}},  # 64 → 70.4 = +10%
        )
        out = mi.adjusted_team_aggregates(None, 2026, self._aggs())
        assert out["KC"]["points_per_game"] == pytest.approx(26.0 * 1.10, rel=1e-3)
        # Untouched team shares the original dict.
        assert out["BUF"]["points_per_game"] == 25.0
        assert "_input_adjustment" in out["KC"]

    def test_ypp_damped_elasticity(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"yards_per_play": 6.38}},  # +10%
        )
        out = mi.adjusted_team_aggregates(None, 2026, self._aggs())
        assert out["KC"]["points_per_game"] == pytest.approx(26.0 * 1.10 ** 0.9, rel=1e-3)

    def test_direct_ppg_supersedes_multipliers(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"points_per_game": 30.0, "pace": 70.0}},
        )
        out = mi.adjusted_team_aggregates(None, 2026, self._aggs())
        assert out["KC"]["points_per_game"] == 30.0

    def test_ratio_clamped(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"pace": 200.0}},  # absurd → clamp at 1.25
        )
        out = mi.adjusted_team_aggregates(None, 2026, self._aggs())
        assert out["KC"]["points_per_game"] == pytest.approx(26.0 * 1.25, rel=1e-3)


class TestPassTilts:
    def test_pass_heavier_tilts_both_families(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"pass_rate": 0.64}},
        )
        aggs = {"KC": {"pass_rate_neutral": 0.58}}
        tilts = mi.team_pass_tilts(None, 2026, aggs)
        assert tilts["KC"]["pass"] == pytest.approx(0.64 / 0.58, rel=1e-3)
        assert tilts["KC"]["rush"] == pytest.approx(0.36 / 0.42, rel=1e-3)
        assert tilts["KC"]["pass"] > 1.0 > tilts["KC"]["rush"]

    def test_no_pass_rate_override_no_tilt(self, monkeypatch):
        monkeypatch.setattr(
            overrides_service, "team_input_overrides",
            lambda db, s: {"KC": {"pace": 66.0}},
        )
        assert mi.team_pass_tilts(None, 2026, {"KC": {}}) == {}


class TestPlayerStatMultipliers:
    def test_target_share_scales_receiving_family(self):
        mults = mi.player_stat_multipliers(
            {"target_share": 0.26}, {"target_share": 0.20}, None,
        )
        for s in ("targets", "receptions", "receiving_yards", "receiving_tds"):
            assert mults[s] == pytest.approx(1.30, rel=1e-3)
        assert "carries" not in mults

    def test_efficiency_half_elasticity_on_tds(self):
        mults = mi.player_stat_multipliers(
            {"yards_per_target": 9.9}, {"yards_per_target": 9.0}, None,
        )
        assert mults["receiving_yards"] == pytest.approx(1.10, rel=1e-3)
        assert mults["receiving_tds"] == pytest.approx(1.10 ** 0.5, rel=1e-3)
        assert "targets" not in mults  # efficiency ≠ volume

    def test_no_baseline_is_noop(self):
        assert mi.player_stat_multipliers({"target_share": 0.30}, {}, None) == {}
        assert mi.player_stat_multipliers(
            {"target_share": 0.30}, {"target_share": None}, None,
        ) == {}

    def test_snap_rate_scales_everything(self):
        mults = mi.player_stat_multipliers(
            {"snap_rate": 0.90}, {"snap_rate": 0.60}, None,
        )
        for s in ("targets", "carries", "attempts", "receiving_yards", "rushing_tds"):
            assert mults[s] == pytest.approx(1.50, rel=1e-3)  # clamp cap

    def test_tilt_composes_with_levers(self):
        mults = mi.player_stat_multipliers(
            {"rush_share": 0.55}, {"rush_share": 0.50},
            {"pass": 1.10, "rush": 0.90},
        )
        # Rush family: share ratio 1.1 × rush tilt 0.9.
        assert mults["carries"] == pytest.approx(1.10 * 0.90, rel=1e-3)
        # Receiving family: tilt only.
        assert mults["targets"] == pytest.approx(1.10, rel=1e-3)


class TestUpsertValidation:
    def test_team_field_whitelist(self):
        with pytest.raises(ValueError, match="team field"):
            overrides_service.upsert_override(
                None, entity_type="team", entity_id="KC",
                field="mojo", value=1.0, season=2026,
            )

    def test_team_week_scope_rejected(self):
        with pytest.raises(ValueError, match="season-scoped"):
            overrides_service.upsert_override(
                None, entity_type="team", entity_id="KC",
                field="pace", value=64.0, season=2026, week=3,
            )

    def test_player_input_week_scope_rejected(self):
        with pytest.raises(ValueError, match="season-scoped input lever"):
            overrides_service.upsert_override(
                None, entity_type="player", entity_id="p1",
                field="target_share", value=0.25, season=2026, week=3,
            )

    def test_share_range_enforced(self):
        with pytest.raises(ValueError, match="share"):
            overrides_service.upsert_override(
                None, entity_type="team", entity_id="KC",
                field="pass_rate", value=58.0, season=2026,  # meant 0.58
            )
