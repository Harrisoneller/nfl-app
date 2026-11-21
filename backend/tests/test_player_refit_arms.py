"""Banned paths and the week cutoff for the two arms.

Synthetic rows only. The monkeypatches raise if an arm touches a banned call.
Nothing here fits a coefficient or scores a stat.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.adapters.data import odds_api
from app.services import player_opportunity_model as opp
from app.services import player_predictions_service as pps
from app.services import player_projection_engine as engine
from app.services import player_props_service as props
from app.services import team_ratings_v5
from research.player_refit import arms
from research.player_refit.arms import PINNED_V5_REPLAY_SHA256, ProtocolError

ET = ZoneInfo("America/New_York")


def _boom(name: str):
    def _banned(*_args, **_kwargs):
        raise ProtocolError(f"banned call: {name}")

    return _banned


def _ban_refit(monkeypatch) -> None:
    monkeypatch.setattr(opp, "over_prob", _boom("over_prob"))
    monkeypatch.setattr(engine, "projection_over_prob", _boom("projection_over_prob"))
    monkeypatch.setattr(pps, "_project_stat_opportunity", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_anchor_target", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_bulk_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(props, "refresh_player_props", _boom("odds"))
    monkeypatch.setattr(odds_api.TheOddsApiAdapter, "fetch_events", _boom("odds"))
    monkeypatch.setattr(team_ratings_v5, "market_ratings", _boom("live v5"))
    monkeypatch.setattr(team_ratings_v5.WeekModel, "predict", _boom("live v5"))
    monkeypatch.setattr(team_ratings_v5, "build_week_model", _boom("live v5"))


def _ban_incumbent(monkeypatch) -> None:
    monkeypatch.setattr(pps, "_project_stat_opportunity", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_anchor_target", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_bulk_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(props, "refresh_player_props", _boom("odds"))
    monkeypatch.setattr(odds_api.TheOddsApiAdapter, "fetch_events", _boom("odds"))
    monkeypatch.setattr(odds_api.TheOddsApiAdapter, "fetch_event_player_props", _boom("odds"))


class _PinnedReplay:
    """Stand-in for the pinned v5_replay. It has no mkt_d."""

    FILE_SHA256 = PINNED_V5_REPLAY_SHA256
    USES_MKT_D = False

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def team_points(self, rows: list[dict]) -> list[dict]:
        for row in rows:
            leaked = arms.MARKET_INPUTS.intersection(row)
            if leaked:
                raise ProtocolError(f"market input forwarded: {sorted(leaked)}")
        self.seen = rows
        return [{"team_points_no_mkt": 22.0, "opp_points_no_mkt": 20.0} for _ in rows]


def _rows():
    opener = datetime(2024, 9, 5, 20, 20, tzinfo=ET)
    return [
        {
            "season": 2023, "week": 18, "game_type": "REG", "game_id": "prior",
            "kickoff_et": datetime(2024, 1, 7, 13, 0, tzinfo=ET),
            "spread_team": 3.0, "imp_pts": 24.0, "spread_line": -3.0, "mkt_d": 1.0,
        },
        {
            "season": 2024, "week": 1, "game_type": "REG", "game_id": "opener",
            "kickoff_et": opener, "spread": -1.0, "odds": -110,
        },
        {
            "season": 2024, "week": 1, "game_type": "REG", "game_id": "same_instant",
            "kickoff_et": opener,
        },
        {
            "season": 2024, "week": 1, "game_type": "REG", "game_id": "sunday",
            "kickoff_et": opener + timedelta(days=3),
        },
    ]


def test_banned_hooks_raise():
    with pytest.raises(ProtocolError, match="over_prob"):
        _boom("over_prob")()


def test_refit_arm_does_not_call_banned_paths(monkeypatch):
    _ban_refit(monkeypatch)
    replay = _PinnedReplay()
    out = arms.refit_team_points(_rows(), 2024, 1, replay)
    assert out["prop_anchor"] is None
    assert out["n_history"] == 1
    assert replay.seen[0]["game_id"] == "prior"
    assert out["team_points"] == [{"team_points_no_mkt": 22.0, "opp_points_no_mkt": 20.0}]
    with pytest.raises(ProtocolError, match="over_prob"):
        opp.over_prob({}, 0.0)
    with pytest.raises(ProtocolError, match="live v5"):
        team_ratings_v5.market_ratings(None, 2024, 1, [])


def test_refit_rejects_live_v5(monkeypatch):
    _ban_refit(monkeypatch)
    with pytest.raises(ProtocolError, match="live v5"):
        arms.refit_team_points(_rows(), 2024, 1, team_ratings_v5)


def test_fit_window_excludes_the_exact_kickoff():
    opener = datetime(2024, 9, 5, 20, 20, tzinfo=ET)
    kept, cutoff = arms.fit_rows(_rows(), 2024, 1)
    assert cutoff == opener
    assert [row["game_id"] for row in kept] == ["prior"]
    same = datetime(2024, 9, 5, 20, 20, tzinfo=ET)
    assert not (same < cutoff)


def test_incumbent_arm_does_not_call_anchor_or_odds(monkeypatch):
    _ban_incumbent(monkeypatch)
    out = arms.incumbent_arm(_rows(), 2024, 1)
    assert out["prop_anchor"] is None
    assert out["n_history"] == 1
    with pytest.raises(ProtocolError, match="prop-anchor shift"):
        pps._anchor_target("passing_yards", {}, 1.0)
    with pytest.raises(ProtocolError, match="odds"):
        props.refresh_player_props(None)


def test_incumbent_prop_anchor_is_off():
    assert arms.INCUMBENT_PROP_ANCHOR is False
