"""Leaderboard coverage regression tests (the "11 QBs" bug).

The projection leaderboard used to join its historical candidate pool (nflverse
GSIS ids) against Player.gsis_id verbatim. Sleeper leaves gsis_id null for many
players and whitespace-pads it for others, so most of the pool silently failed
the join and the QB board showed a fraction of the league. These tests pin the
three fixes:

1. gsis normalization (padded ids still match),
2. name+position fallback matching (null ids still match),
3. supplemental rookie candidates (no NFL history, but depth-chart starters).

No DB and no network: frames, game environments, and the roster query are all
stubbed. That keeps this a fast unit suite that runs anywhere.
"""
from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.models.player import Player
from app.services import player_predictions_service as pps
from app.services.players_service import normalize_gsis_id
from app.utils.seasons import latest_completed_season

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clean_gsis_strips_sleeper_padding():
    assert pps._clean_gsis(" 00-0034796") == "00-0034796"
    assert pps._clean_gsis("00-0034796 ") == "00-0034796"
    assert pps._clean_gsis("") is None
    assert pps._clean_gsis("   ") is None
    assert pps._clean_gsis(None) is None
    assert pps._clean_gsis(1234) is None  # non-str garbage


def test_normalize_gsis_id_matches_service_helper():
    assert normalize_gsis_id(" 00-001 ") == "00-001"
    assert normalize_gsis_id(None) is None
    assert normalize_gsis_id("") is None


def test_normalize_name_suffixes_and_punctuation():
    assert pps._normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert pps._normalize_name("Kenneth Walker III") == "kenneth walker"
    assert pps._normalize_name("A.J. Brown") == "a j brown"
    assert pps._normalize_name("  Marvin  Harrison   Jr ") == "marvin harrison"
    assert pps._normalize_name(None) == ""


def test_coverage_summary_counts_and_missing():
    rows = [
        {"position": "QB", "team": "T1"},
        {"position": "QB", "team": "T2"},
        {"position": "WR", "team": "T1"},
        {"position": "WR", "team": None},  # never counts
    ]
    cov = pps._coverage_summary(rows, ["T1", "T2", "T3"])
    assert cov["QB"] == {"teams": 2, "total_teams": 3, "missing": ["T3"]}
    assert cov["WR"] == {"teams": 1, "total_teams": 3, "missing": ["T2", "T3"]}
    assert cov["RB"]["teams"] == 0 and cov["RB"]["missing"] == ["T1", "T2", "T3"]


# ---------------------------------------------------------------------------
# Leaderboard build — stubbed end-to-end
# ---------------------------------------------------------------------------

_TEAMS = ["T1", "T2", "T3"]


class _StubResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _StubDB:
    """Answers the single roster SELECT the builder issues."""

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
    """5 weeks of synthetic usage for two vet QBs + one WR."""
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
            **stat_zero, "player_id": "00-002", "player_display_name": "Bob Passer",
            "position": "QB", "week": week, "recent_team": "T2", "opponent_team": "T1",
            "attempts": 32.0, "completions": 21.0, "passing_yards": 245.0,
            "passing_tds": 1.6, "interceptions": 0.9, "carries": 4.0,
            "rushing_yards": 18.0, "rushing_tds": 0.15, "fantasy_points_ppr": 18.0,
        })
        rows.append({
            **stat_zero, "player_id": "00-003", "player_display_name": "Wide Guy",
            "position": "WR", "week": week, "recent_team": "T1", "opponent_team": "T3",
            "targets": 9.0, "receptions": 6.0, "receiving_yards": 82.0,
            "receiving_tds": 0.5, "fantasy_points_ppr": 20.0,
        })
    return pd.DataFrame(rows)


def _envs():
    out = {}
    for i, team in enumerate(_TEAMS):
        opp = _TEAMS[(i + 1) % len(_TEAMS)]
        out[team] = [
            {
                "week": w, "gameday": "2099-01-01", "game_id": f"g{team}{w}",
                "game_script": "neutral", "predicted_total": 45.0,
                "opponent": opp, "is_home": True,
                "exp_pts_for": 23.0, "exp_pts_against": 22.0,
            }
            for w in (1, 2)
        ]
    return out


@pytest.fixture()
def stubbed(monkeypatch):
    season = latest_completed_season() + 1  # offseason build: no obs data yet
    frames = {
        latest_completed_season(): _weekly_frame(latest_completed_season()),
        latest_completed_season() - 1: _weekly_frame(latest_completed_season() - 1),
    }

    async def fake_frame(s):
        return frames.get(s)

    async def fake_envs(_db, _season):
        return _envs()

    async def fake_def_factors(_season):
        return {}

    monkeypatch.setattr(pps, "_player_weekly_frame", fake_frame)
    monkeypatch.setattr(pps, "league_game_environments", fake_envs)
    monkeypatch.setattr(pps, "positional_defense_factors", fake_def_factors)
    # No cross-test bleed through the shared in-process cache.
    monkeypatch.setattr(pps.cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(pps.cache, "set", lambda *_a, **_k: None)
    return season


def _roster():
    return [
        # Padded gsis — the exact Sleeper quirk that broke the join.
        _mk_player("s1", "Aaron Vet", "QB", "T1", gsis=" 00-001", depth=1),
        # Null gsis — must be recovered by name+position fallback.
        _mk_player("s2", "Bob Passer", "QB", "T2", gsis=None, depth=1),
        # Rookie: no NFL history at all, but holds the QB1 slot.
        _mk_player("s3", "Cal Rook", "QB", "T3", gsis=None, depth=1),
        # Clean gsis control.
        _mk_player("s4", "Wide Guy", "WR", "T1", gsis="00-003", depth=1),
        # Backup QB — depth 2 must stay off the board.
        _mk_player("s5", "Deep Bench", "QB", "T1", gsis=None, depth=2),
        # Inactive vet — roster gate must drop despite matching name.
        _mk_player("s6", "Bob Passer", "QB", "T2", gsis=None, depth=1,
                   status="Inactive"),
    ]


def test_leaderboard_recovers_all_qbs(stubbed):
    season = stubbed
    board = asyncio.run(pps._build_leaderboard_rows(_StubDB(_roster()), season))
    rows = board["rows"]

    qbs = [r for r in rows if r["position"] == "QB"]
    assert {r["name"] for r in qbs} == {"Aaron Vet", "Bob Passer", "Cal Rook"}

    by_name = {r["name"]: r for r in qbs}
    # Vets projected from history, rookie from the archetype prior.
    assert by_name["Aaron Vet"]["rookie"] is False
    assert by_name["Bob Passer"]["rookie"] is False
    assert by_name["Cal Rook"]["rookie"] is True
    # Every QB carries the full stat kit with sane ordering.
    for r in qbs:
        py = r["stats"]["passing_yards"]
        assert py["p10"] <= py["mean"] <= py["p90"]
        assert r["fantasy_ppr"]["mean"] > 0
    # Vet history should out-project the modest rookie archetype.
    assert (
        by_name["Aaron Vet"]["stats"]["passing_yards"]["mean"]
        > by_name["Cal Rook"]["stats"]["passing_yards"]["mean"]
    )

    cov = board["coverage"]
    assert cov["QB"] == {"teams": 3, "total_teams": 3, "missing": []}
    assert cov["WR"]["missing"] == ["T2", "T3"]


def test_leaderboard_endpoint_shape(stubbed):
    season = stubbed
    resp = asyncio.run(
        pps.projection_leaderboard(_StubDB(_roster()), season=season, position="QB")
    )
    assert resp["count"] == 3
    assert resp["coverage"]["QB"]["teams"] == 3
    ranks = [p["rank"] for p in resp["players"]]
    assert ranks == sorted(ranks) == list(range(1, 4))
    # Default sort is the fantasy composite, descending.
    means = [p["fantasy_ppr"]["mean"] for p in resp["players"]]
    assert means == sorted(means, reverse=True)


def test_backup_and_inactive_players_stay_off_the_board(stubbed):
    season = stubbed
    board = asyncio.run(pps._build_leaderboard_rows(_StubDB(_roster()), season))
    names = {r["name"] for r in board["rows"]}
    assert "Deep Bench" not in names  # QB2 role multiplier < leaderboard min
    assert len([r for r in board["rows"] if r["name"] == "Bob Passer"]) == 1
