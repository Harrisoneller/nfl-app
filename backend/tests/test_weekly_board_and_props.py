"""Weekly slate board + prop-bet tracking units.

Same philosophy as test_player_projections_leaderboard: no DB, no network —
frames, environments and rosters are stubbed, so this runs anywhere and pins
the new Players-hub surfaces:

1. weekly_projection_board — slate-week resolution, bye handling, positional
   tiers, and fantasy bands ordered p10 ≤ mean ≤ p90.
2. player-prop bet legs — schema normalization, grading against the weekly
   frame (over/under/push/anytime-TD), and pending-until-data behavior.
3. fantasy insights helpers — VORP sd recovery from the p10–p90 band.
"""
from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.models.player import Player
from app.schemas.bet import BetLegCreate
from app.services import bet_service as bs
from app.services import player_predictions_service as pps
from app.services.fantasy_insights_service import _sd_from_band
from app.utils.seasons import latest_completed_season

_TEAMS = ["T1", "T2", "T3"]


# ---------------------------------------------------------------------------
# Weekly board
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _StubDB:
    def __init__(self, players):
        self._players = players

    def execute(self, _stmt):
        return _StubResult(self._players)


def _mk_player(pid, name, pos, team, gsis=None, depth=None, status="Active"):
    return Player(
        id=pid, gsis_id=gsis, full_name=name, position=pos, team_id=team,
        status=status,
        metadata_json={"depth_chart_order": depth} if depth is not None else {},
    )


def _weekly_frame(season: int) -> pd.DataFrame:
    rows = []
    stat_zero = {s: 0.0 for s in pps._ALL_STATS}
    for week in range(1, 6):
        rows.append({
            **stat_zero, "player_id": "00-001", "player_display_name": "Aaron Vet",
            "position": "QB", "week": week, "recent_team": "T1", "opponent_team": "T2",
            "attempts": 35.0, "completions": 24.0, "passing_yards": 280.0,
            "passing_tds": 2.0, "interceptions": 0.7, "carries": 3.0,
            "rushing_yards": 12.0, "rushing_tds": 0.1, "fantasy_points_ppr": 21.0,
        })
        rows.append({
            **stat_zero, "player_id": "00-003", "player_display_name": "Wide Guy",
            "position": "WR", "week": week, "recent_team": "T1", "opponent_team": "T3",
            "targets": 9.0, "receptions": 6.0, "receiving_yards": 82.0,
            "receiving_tds": 0.5, "fantasy_points_ppr": 20.0,
        })
        rows.append({
            **stat_zero, "player_id": "00-004", "player_display_name": "Bye Runner",
            "position": "RB", "week": week, "recent_team": "T3", "opponent_team": "T1",
            "carries": 16.0, "rushing_yards": 70.0, "rushing_tds": 0.5,
            "targets": 3.0, "receptions": 2.4, "receiving_yards": 16.0,
            "receiving_tds": 0.05, "fantasy_points_ppr": 15.0,
        })
    return pd.DataFrame(rows)


def _envs():
    """T1/T2 play in week 1; T3's first remaining game is week 2 (bye week 1)."""
    out = {}
    for team in _TEAMS:
        first_week = 2 if team == "T3" else 1
        opp = "T2" if team != "T2" else "T1"
        out[team] = [
            {
                "week": w, "gameday": "2099-01-01", "game_id": f"g{team}{w}",
                "game_script": "neutral", "predicted_total": 45.0,
                "opponent": opp, "is_home": True,
                "exp_pts_for": 23.0, "exp_pts_against": 22.0,
            }
            for w in range(first_week, first_week + 2)
        ]
    return out


@pytest.fixture()
def stubbed(monkeypatch):
    season = latest_completed_season() + 1
    frames = {
        latest_completed_season(): _weekly_frame(latest_completed_season()),
        latest_completed_season() - 1: _weekly_frame(latest_completed_season() - 1),
    }

    async def fake_frame(s):
        return frames.get(s)

    async def fake_envs(_db, _season):
        return _envs()

    async def fake_def_factors(_season):
        return {"T2": {"pass": 1.15, "rush": 0.9, "recv_WR": 1.1, "recv_RB": 1.0, "recv_TE": 1.0}}

    monkeypatch.setattr(pps, "_player_weekly_frame", fake_frame)
    monkeypatch.setattr(pps, "league_game_environments", fake_envs)
    monkeypatch.setattr(pps, "positional_defense_factors", fake_def_factors)
    monkeypatch.setattr(pps, "_bulk_market_anchors", lambda _db: {})
    monkeypatch.setattr(pps.cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(pps.cache, "set", lambda *_a, **_k: None)
    return season


def _roster():
    return [
        _mk_player("s1", "Aaron Vet", "QB", "T1", gsis="00-001", depth=1),
        _mk_player("s4", "Wide Guy", "WR", "T1", gsis="00-003", depth=1),
        _mk_player("s7", "Bye Runner", "RB", "T3", gsis="00-004", depth=1),
    ]


def test_weekly_board_tiers_byes_and_bands(stubbed):
    season = stubbed
    board = asyncio.run(
        pps.weekly_projection_board(_StubDB(_roster()), season=season, scoring="ppr")
    )
    assert board["week"] == 1  # earliest remaining week across the league
    rows = {r["name"]: r for r in board["players"]}

    # T3 is idle in week 1 → bye row with no projection.
    assert rows["Bye Runner"]["bye"] is True
    assert rows["Bye Runner"]["tier"] == "Bye"
    assert "fantasy" not in rows["Bye Runner"]

    # Active players carry the full kit.
    qb = rows["Aaron Vet"]
    assert qb["bye"] is False and qb["week"] == 1 and qb["opponent"] == "T2"
    f = qb["fantasy"]["ppr"]
    assert f["p10"] <= f["mean"] <= f["p90"]
    assert qb["pos_rank"] == 1 and qb["tier"] == "Start"  # QB1 of 1
    # T2 leaks passing yards (factor 1.15) → A/B matchup grade.
    assert qb["matchup_grade"] in ("A", "B")

    wr = rows["Wide Guy"]
    assert wr["tier"] == "Must start"  # WR1 of 1

    # Position filter works off the same cached board.
    only_wr = asyncio.run(
        pps.weekly_projection_board(
            _StubDB(_roster()), season=season, scoring="ppr", position="WR"
        )
    )
    assert {r["position"] for r in only_wr["players"]} == {"WR"}


def test_tier_label_boundaries():
    assert pps._tier_label("RB", 12) == "Must start"
    assert pps._tier_label("RB", 13) == "Start"
    assert pps._tier_label("RB", 25) == "Flex"
    assert pps._tier_label("RB", 41) == "Sit"
    assert pps._tier_label("QB", 12) == "Start"
    assert pps._tier_label("QB", 13) == "Stream"
    assert pps._tier_label("TE", 19) == "Sit"


# ---------------------------------------------------------------------------
# Player-prop bet legs
# ---------------------------------------------------------------------------


def test_prop_leg_schema_normalizes_and_validates():
    leg = BetLegCreate(
        market="player_prop", selection="Over", odds_american=-115, line=72.5,
        player_name="Ja'Marr Chase", prop_market="player_reception_yds",
    )
    assert leg.selection == "over"
    assert "O 72.5" in bs._default_label(leg)

    # Anytime TD: lineless, "yes" → over.
    atd = BetLegCreate(
        market="player_prop", selection="yes", odds_american=140,
        player_name="X Y", prop_market="player_anytime_td",
    )
    assert atd.selection == "over" and atd.line is None

    with pytest.raises(ValueError):
        BetLegCreate(market="player_prop", selection="over", odds_american=-110,
                     line=1.5, prop_market="player_receptions")  # no player_name
    with pytest.raises(ValueError):
        BetLegCreate(market="player_prop", selection="over", odds_american=-110,
                     player_name="X", prop_market="player_receptions")  # no line


class _FakeGame:
    season, week = 2025, 7


def _leg(**kw):
    class _L:
        prop_market = kw.get("prop_market", "player_reception_yds")
        player_name = kw.get("player_name", "Ja'Marr Chase")
        line = kw.get("line", 72.5)
        selection = kw.get("selection", "over")
    return _L()


def test_prop_grading_against_weekly_frame(monkeypatch):
    df = pd.DataFrame({
        "player_display_name": ["Ja'Marr Chase"],
        "week": [7],
        "receiving_yards": [101.0],
        "rushing_tds": [0.0],
        "receiving_tds": [0.0],
    })
    from app.cache import cache
    monkeypatch.setattr(cache, "get", lambda k: df if k == "player_weekly_indexed:2025" else None)

    actual = bs._prop_actual(_leg(), _FakeGame())
    assert actual == 101.0
    assert bs._grade_player_prop(_leg(), actual) == "won"
    assert bs._grade_player_prop(_leg(selection="under"), actual) == "lost"
    assert bs._grade_player_prop(_leg(line=101.0), 101.0) == "push"
    # Anytime TD with no line: zero TDs → over ("yes") loses.
    atd = _leg(prop_market="player_anytime_td", line=None)
    assert bs._grade_player_prop(atd, bs._prop_actual(atd, _FakeGame())) == "lost"


def test_prop_grading_stays_pending_without_frame(monkeypatch):
    from app.cache import cache
    monkeypatch.setattr(cache, "get", lambda _k: None)
    assert bs._prop_actual(_leg(), _FakeGame()) is None  # → leg stays pending


# ---------------------------------------------------------------------------
# Fantasy insights helpers
# ---------------------------------------------------------------------------


def test_vorp_sd_recovered_from_band():
    sd = _sd_from_band({"p10": 100.0, "p90": 200.0})
    assert abs(sd - 100.0 / 2.563104) < 0.01
    assert _sd_from_band(None) == 0.0
    assert _sd_from_band({"p10": None, "p90": 5}) == 0.0
