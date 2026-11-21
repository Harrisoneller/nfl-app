"""Banned paths on the real replay and the shipped predict function.

Synthetic rows only. Patches sit on the module that looks the name up.
No real-season fit and no CRPS difference.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services import game_model_v5_service
from app.services import player_opportunity_model as opp
from app.services import player_predictions_service as pps
from app.services import player_projection_engine as engine
from app.services import player_props_service as props
from app.services import team_ratings_v5

FIXTURE = Path(__file__).resolve().parent / "fixtures"
PINNED_SHA = "bee8006b5b3096e7c1bc3ff53afc2cb211b417e2e1b7ca66e47ac442ff9892d3"
if str(FIXTURE) not in sys.path:
    sys.path.insert(0, str(FIXTURE))

from pinned_v5_replay import v5_replay  # noqa: E402


class Banned(RuntimeError):
    """A patched hook fired."""


def _boom(name: str):
    def _banned(*_args, **_kwargs):
        raise Banned(name)

    return _banned


def _weekly():
    rows = []
    for week in range(1, 7):
        for pid, pos, targets in (("WR1", "WR", 9), ("RB1", "RB", 3)):
            rows.append({
                "player_id": pid, "position": pos, "recent_team": "AAA",
                "season": 2024, "week": week, "season_type": "REG",
                "targets": targets, "receptions": targets * 0.6,
                "receiving_yards": targets * 8, "carries": 14 if pos == "RB" else 0,
                "rushing_yards": 60 if pos == "RB" else 0, "attempts": 0,
                "completions": 0, "passing_yards": 0,
            })
    return pd.DataFrame(rows)


def _opp():
    frame = opp.prepare_weekly([_weekly()])
    return {
        "profile": opp.player_profiles(frame, 2024)["WR1"],
        "team_ewm": opp.team_volume_ewm(frame)["AAA"],
        "role_mult": 1.0,
    }


def _env():
    return {"exp_pts_for": 24.0, "exp_pts_against": 21.0}


def _posterior():
    return engine.StatPosterior(mean=70.0, game_sd=25.0, talent_sd=8.0, prior_n=8, obs_n=6)


def _design(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    columns = list(v5_replay.MARGIN_COLUMNS) + list(v5_replay.TOTAL_COLUMNS)
    data = {name: rng.normal(size=n) for name in columns}
    data["margin"] = rng.normal(size=n)
    data["total"] = 44.0 + rng.normal(size=n)
    return pd.DataFrame(data)


def _rating_rows(cutoff: datetime) -> pd.DataFrame:
    before = datetime(2023, 12, 31, 13, 0, tzinfo=cutoff.tzinfo)
    rows = []
    for index in range(4):
        rows.append({
            "game_id": f"g{index}", "season": 2023, "week": 10 + index,
            "posteam": "AAA", "defteam": "BBB", "is_home": 1.0, "pts": 21.0 + index,
            "gw": 60.0, "w_epa": 6.0, "p_w": 35.0, "p_epa": 4.0,
            "st_epa": 0.2, "n": 62.0, "kickoff_et": before,
        })
    rows.append({
        **rows[0], "game_id": "at-cutoff", "kickoff_et": cutoff,
    })
    return pd.DataFrame(rows)


def _patch_refit_bans(monkeypatch) -> None:
    # Lookups: projection_over_prob imports opp and calls opp.over_prob.
    # team_volume is the spread/imp_pts reader inside this module.
    # build_week_model looks up market_ratings in team_ratings_v5.
    # game_model_v5_service holds that module as v5 and calls v5.build_week_model.
    # props bound TheOddsApiAdapter at import.
    monkeypatch.setattr(opp, "over_prob", _boom("over_prob"))
    monkeypatch.setattr(opp, "team_volume", _boom("spread or imp_pts"))
    monkeypatch.setattr(pps, "_anchor_target", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_bulk_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(props, "TheOddsApiAdapter", _boom("odds"))
    monkeypatch.setattr(team_ratings_v5, "market_ratings", _boom("live v5 mkt_d"))
    monkeypatch.setattr(team_ratings_v5, "build_week_model", _boom("live v5 mkt_d"))


def _patch_incumbent_bans(monkeypatch) -> None:
    monkeypatch.setattr(pps, "_anchor_target", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(pps, "_bulk_market_anchors", _boom("prop-anchor shift"))
    monkeypatch.setattr(props, "TheOddsApiAdapter", _boom("odds"))


def test_pinned_replay_bytes_match_the_commit():
    digest = hashlib.sha256((FIXTURE / "pinned_v5_replay" / "v5_replay.py").read_bytes()).hexdigest()
    assert digest == PINNED_SHA
    assert v5_replay.BOOTSTRAP_REPS == 999


def test_refit_runs_replay_without_banned_calls(monkeypatch):
    _patch_refit_bans(monkeypatch)
    cutoff = datetime(2024, 9, 5, 20, 20, tzinfo=v5_replay.SCREEN_CUTOFF.tzinfo)
    ratings, book = v5_replay.ratings_as_of(
        _rating_rows(cutoff), 2024, 1, ["AAA", "BBB"], cutoff,
    )
    assert "epa" in ratings
    assert book is not None
    trained = v5_replay.replay_week(_design(32, 1), _design(1, 2))
    assert trained["ok"] is True
    assert len(trained["margin_coef"]) == len(v5_replay.MARGIN_COLUMNS)


def test_refit_cutoff_excludes_the_exact_kickoff():
    cutoff = datetime(2024, 9, 5, 20, 20, tzinfo=v5_replay.SCREEN_CUTOFF.tzinfo)
    kept = v5_replay.rows_before(_rating_rows(cutoff), cutoff)
    assert "at-cutoff" not in set(kept["game_id"])
    assert len(kept) == 4


def test_refit_banned_hooks_fire_when_called(monkeypatch):
    _patch_refit_bans(monkeypatch)
    with pytest.raises(Banned, match="over_prob"):
        engine.projection_over_prob({"quantiles": {"0.5": 10.0}, "mean": 10.0, "sd": 2.0}, 10.0)
    with pytest.raises(Banned, match="spread or imp_pts"):
        opp.team_volume("t_tgt", 34.0, 3.0, 24.0)
    with pytest.raises(Banned, match="live v5"):
        team_ratings_v5.market_ratings(None, 2024, 1, ["AAA"])
    with pytest.raises(Banned, match="live v5"):
        game_model_v5_service.v5.build_week_model(None, None, 2024, 1, [])
    with pytest.raises(Banned, match="odds"):
        props.TheOddsApiAdapter()
    with pytest.raises(Banned, match="prop-anchor shift"):
        pps._anchor_target("receiving_yards", {"line": 60.5, "books": 3, "over_prob": 0.55}, 20.0)


def test_incumbent_predicts_with_the_anchor_off(monkeypatch):
    _patch_incumbent_bans(monkeypatch)
    out = pps._project_stat_for_game(
        _posterior(), "receiving_yards", _env(), 1.0, None, 1.0,
        anchor=None, opp=_opp(),
    )
    assert out["basis"] == opp.MODEL_VERSION
    assert "market_anchor" not in out
    assert out["quantiles"]


def test_incumbent_anchor_and_odds_guards_fire(monkeypatch):
    _patch_incumbent_bans(monkeypatch)
    with pytest.raises(Banned, match="prop-anchor shift"):
        pps._project_stat_for_game(
            _posterior(), "receiving_yards", _env(), 1.0, None, 1.0,
            anchor={"line": 60.5, "books": 3, "over_prob": 0.55}, opp=_opp(),
        )
    with pytest.raises(Banned, match="odds"):
        props.TheOddsApiAdapter()
    with pytest.raises(Banned, match="prop-anchor shift"):
        pps._market_anchors(None, "Synthetic Player")
