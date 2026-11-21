"""Synthetic leak tests and drive-outcome checks for the P1 drive table."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from research.nfl_drive_sim.calendar import (
    EASTERN,
    SCREEN_CUTOFF,
    PregameError,
    assert_pregame,
    before_screen_cutoff,
)
from research.nfl_drive_sim.drives import DRIVE_COLUMNS, OUTCOMES, build_drive_table, classify_drive
from research.nfl_drive_sim.market import MarketColumnError, assert_no_market_columns
from research.nfl_drive_sim.pregame import (
    PREGAME_COLUMNS,
    pregame_quarterback,
    pregame_quarterback_table,
    rows_strictly_before,
)
from research.nfl_drive_sim.reconcile import reconcile

UTC = ZoneInfo("UTC")
CHICAGO = ZoneInfo("America/Chicago")


def test_package_does_not_import_the_served_model():
    """The research package must not import the served rating model.

    The rest of this suite imports the app, so the check is the source of
    the research package, not whether that module happens to be loaded.
    A string scan catches ``import_module`` and ``__import__`` as well as
    ordinary import statements.
    """
    import ast
    from pathlib import Path

    banned = ("team_ratings_v5", "app.services")
    root = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text()
        for name in banned:
            if name in text:
                offenders.append(f"{path.name} mentions {name}")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called = func.id
                elif isinstance(func, ast.Attribute):
                    called = func.attr
                else:
                    called = ""
                if called in {"import_module", "__import__"}:
                    names = [
                        arg.value for arg in node.args
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    ]
            else:
                continue
            if any(any(piece in name for piece in banned) for name in names):
                offenders.append(f"{path.name}:{getattr(node, 'lineno', 0)}")
    assert offenders == []


def test_pregame_rows_exclude_the_target_game_and_later_games():
    target = pd.Timestamp("2026-10-11 17:00", tz="America/New_York")
    frame = pd.DataFrame({
        "game_id": ["early", "same_time", "target", "later"],
        "kickoff_et": [
            pd.Timestamp("2026-10-11 20:30", tz="UTC"),
            target,
            target,
            pd.Timestamp("2026-10-11 20:20", tz="America/New_York"),
        ],
        "marker": ["EARLY", "SAME", "TARGET_PLAY", "LATER_PLAY"],
    })
    kept = rows_strictly_before(frame, target)
    assert list(kept["game_id"]) == ["early"]
    assert "TARGET_PLAY" not in set(kept["marker"])
    assert "LATER_PLAY" not in set(kept["marker"])
    assert "SAME" not in set(kept["marker"])


def test_rows_strictly_before_rejects_a_naive_timestamp():
    frame = pd.DataFrame({
        "game_id": ["naive"],
        "kickoff_et": ["2026-10-11 16:00"],
    })
    target = pd.Timestamp("2026-10-11 17:00", tz="America/New_York")
    with pytest.raises(PregameError):
        rows_strictly_before(frame, target)


def test_naive_wall_clock_does_not_pass_the_cutoff_as_utc():
    # 00:30 with no zone is Eastern. Read as UTC it would still be Dec 31 Eastern.
    assert before_screen_cutoff("2026-12-31 23:30")
    assert not before_screen_cutoff("2027-01-01 00:30")
    assert not before_screen_cutoff(datetime(2027, 1, 1, tzinfo=EASTERN))
    assert before_screen_cutoff(datetime(2026, 12, 31, 23, 59, tzinfo=EASTERN))


def test_non_eastern_timestamps_are_converted_before_the_cutoff():
    # 04:30 UTC on Jan 1 is 23:30 Eastern on Dec 31.
    assert before_screen_cutoff(pd.Timestamp("2027-01-01 04:30", tz="UTC"))
    # 05:00 UTC is exactly 2027-01-01 00:00 Eastern.
    assert not before_screen_cutoff(pd.Timestamp("2027-01-01 05:00", tz="UTC"))
    # 23:30 in Chicago is 00:30 Eastern on Jan 1, not a Dec 31 kickoff.
    central = pd.Timestamp("2026-12-31 23:30", tz="America/Chicago")
    assert not before_screen_cutoff(central)
    assert SCREEN_CUTOFF == datetime(2027, 1, 1, tzinfo=EASTERN)


def test_central_time_does_not_slip_through_the_pregame_filter():
    target = pd.Timestamp("2026-10-11 17:00", tz="America/New_York")
    frame = pd.DataFrame({
        "game_id": ["utc_early", "chicago_later"],
        "kickoff_et": [
            pd.Timestamp("2026-10-11 20:30", tz="UTC"),
            pd.Timestamp("2026-10-11 16:30", tz="America/Chicago"),
        ],
    })
    kept = rows_strictly_before(frame, target)
    assert list(kept["game_id"]) == ["utc_early"]


def test_pregame_assert_compares_instants_in_eastern():
    kick = datetime(2026, 10, 11, 17, 0, tzinfo=EASTERN)
    assert_pregame(datetime(2026, 10, 11, 20, 30, tzinfo=UTC), kick, "qb")
    with pytest.raises(PregameError):
        assert_pregame(datetime(2026, 10, 11, 21, 30, tzinfo=UTC), kick, "qb")
    with pytest.raises(PregameError):
        assert_pregame(kick, kick, "qb")
    assert_pregame("2026-10-11 16:30", kick, "qb")
    with pytest.raises(PregameError):
        assert_pregame("2026-10-11 17:00", kick, "qb")


def test_cutoff_excludes_kickoffs_on_or_after_2027_01_01_et():
    early = _drive_plays("early", drive=1, play_id=1, posteam="KC", play_type="pass", passer_player_id="QB")
    late = _drive_plays(
        "late", drive=1, play_id=1, posteam="KC", play_type="pass", passer_player_id="QB",
        total_home_score=0,
    )
    schedule = pd.DataFrame([
        _game("early", "2026-12-27 13:00", home_score=0, away_score=0),
        _game("late", "2027-01-03 13:00", home_score=0, away_score=0),
        _game("boundary", "2027-01-01 00:00", home_score=0, away_score=0),
        {
            "game_id": "utc_dec",
            "season": 2026,
            "week": 17,
            "game_type": "REG",
            "kickoff": pd.Timestamp("2027-01-01 04:30", tz="UTC"),
            "home_team": "KC",
            "away_team": "BUF",
            "home_score": 0,
            "away_score": 0,
            "spread_line": 3.0,
        },
    ])
    # The January rows use a naive Eastern wall clock via the kickoff column.
    built = build_drive_table(pd.concat([early, late], ignore_index=True), schedule)
    assert set(built.drives["game_id"]) == {"early"}
    assert set(built.dropped["game_id"]) >= {"late", "boundary"}
    assert "utc_dec" not in set(built.dropped["game_id"])
    assert str(built.drives["kickoff_et"].dtype.tz) == "America/New_York"
    for frame in (built.drives, built.non_offensive, built.games, built.dropped):
        assert_no_market_columns(frame)


def test_schedule_row_index_does_not_erase_kickoffs():
    plays = _drive_plays("G", drive=1, play_id=1, posteam="KC", play_type="pass", passer_player_id="QB")
    schedule = _schedule("G", "2024-09-08 13:00")
    schedule.index = [50]
    built = build_drive_table(plays, schedule)
    assert list(built.drives["game_id"]) == ["G"]
    assert str(built.drives["kickoff_et"].dtype.tz) == "America/New_York"


def test_market_columns_do_not_appear_in_any_output_frame():
    plays = _drive_plays("G", drive=1, play_id=1, posteam="KC", play_type="pass", passer_player_id="QB")
    plays["spread_line"] = -3.5
    plays["total_line"] = 44.5
    plays["home_moneyline"] = -150
    plays["vegas_wp"] = 0.6
    schedule = pd.DataFrame([{
        **_game("G", "2024-09-08 13:00", home_score=0, away_score=0),
        "spread_line": -3.5,
        "total_line": 44.5,
        "away_moneyline": 130,
    }])
    built = build_drive_table(plays, schedule)
    banned = {"spread_line", "total_line", "home_moneyline", "away_moneyline", "vegas_wp"}
    for frame in (built.drives, built.non_offensive, built.games, built.dropped):
        assert banned.isdisjoint(frame.columns)
        assert_no_market_columns(frame)
    assert list(built.drives.columns) == list(DRIVE_COLUMNS)
    with pytest.raises(MarketColumnError):
        assert_no_market_columns(plays)


def test_outcomes_are_one_label_and_cover_the_set():
    labels = {name: classify_drive(frame, half_ended=ended) for name, frame, ended in _outcome_snippets()}
    assert set(labels.values()) == set(OUTCOMES)
    for label in labels.values():
        assert isinstance(label, str)
        assert label in OUTCOMES
        assert "|" not in label
    # A punt returned for a touchdown is a punt, not a second touchdown label.
    returned = _play(
        play_id=1, play_type="punt", posteam="KC", defteam="BUF", punt_attempt=1,
        touchdown=1, td_team="BUF", return_touchdown=1,
    )
    assert classify_drive(pd.DataFrame([returned])) == "PUNT"
    with pytest.raises(PregameError):
        from research.nfl_drive_sim.drives import assert_outcomes
        assert_outcomes(pd.Series(["TD|PUNT"]))


def test_classify_edge_cases():
    kneel = _play(play_id=1, play_type="qb_kneel", qb_kneel=1, rusher_player_id="QB_K", yards_gained=-1)
    assert classify_drive(pd.DataFrame([kneel])) == "END_OF_HALF"

    onside = pd.DataFrame([
        _play(play_id=1, play_type="kickoff", kickoff_attempt=1, own_kickoff_recovery=1, posteam="BUF"),
        _play(play_id=2, play_type="punt", posteam="BUF", defteam="KC", punt_attempt=1),
    ])
    built = build_drive_table(onside, _schedule("G", "2024-09-08 13:00"))
    assert len(built.drives) == 1
    assert built.drives.iloc[0]["outcome"] == "PUNT"
    assert built.drives.iloc[0]["start_yardline_100"] == 75

    returned = pd.DataFrame([
        _play(
            play_id=1, play_type="kickoff", kickoff_attempt=1, posteam="KC",
            touchdown=1, td_team="KC", total_home_score=6,
        ),
        _play(
            play_id=2, play_type="extra_point", extra_point_attempt=1,
            posteam="KC", total_home_score=7,
        ),
    ])
    kick_build = build_drive_table(returned, _schedule("G", "2024-09-08 13:00", home_score=7))
    assert len(kick_build.drives) == 0
    assert int(kick_build.non_offensive["home_points"].sum()) == 7

    safety = _play(play_id=1, play_type="punt", punt_attempt=1, safety=1, posteam="BUF", defteam="KC")
    assert classify_drive(pd.DataFrame([safety])) == "SAFETY"

    blocked = _play(
        play_id=1, play_type="field_goal", field_goal_attempt=1, field_goal_result="blocked",
        touchdown=1, td_team="BUF", posteam="KC", defteam="BUF",
    )
    assert classify_drive(pd.DataFrame([blocked])) == "FG_MISSED"

    penalty = _play(play_id=1, play_type="no_play", penalty=1, posteam="KC", yards_gained=-5)
    assert classify_drive(pd.DataFrame([penalty]), half_ended=False) == "OTHER"
    assert classify_drive(pd.DataFrame([penalty]), half_ended=True) == "END_OF_HALF"

    overtime = _play(
        play_id=1, play_type="pass", qtr=5, touchdown=1, td_team="KC",
        posteam="KC", defteam="BUF", pass_=1,
    )
    assert classify_drive(pd.DataFrame([overtime])) == "TD"


def test_timeout_row_cannot_roll_the_score_backward():
    plays = pd.DataFrame([
        _play(play_id=1, play_type="pass", posteam="KC", defteam="BUF", touchdown=1, td_team="KC", total_home_score=6),
        _play(play_id=2, play_type="extra_point", extra_point_attempt=1, posteam="KC", total_home_score=7),
        _play(play_id=3, play_type="no_play", timeout=1, posteam="KC", total_home_score=0),
    ])
    built = build_drive_table(plays, _schedule("G", "2024-09-08 13:00", home_score=7, away_score=0))
    assert built.drives.iloc[0]["outcome"] == "TD"
    assert int(built.drives.iloc[0]["posteam_points"]) == 7
    assert len(reconcile(built.drives, built.non_offensive, built.games)) == 0


def test_misordered_play_id_does_not_replace_a_made_field_goal():
    plays = pd.DataFrame([
        _play(play_id=1, play_type="pass", posteam="KC", quarter_seconds_remaining=200, total_home_score=0),
        _play(
            play_id=2, play_type="field_goal", field_goal_attempt=1, field_goal_result="made",
            posteam="KC", quarter_seconds_remaining=100, total_home_score=3,
        ),
        _play(
            play_id=3, play_type="pass", posteam="KC", quarter_seconds_remaining=180, total_home_score=0,
        ),
        _play(
            play_id=4, drive=2, play_type="kickoff", kickoff_attempt=1, posteam="BUF",
            quarter_seconds_remaining=900, qtr=2, total_home_score=3,
        ),
    ])
    built = build_drive_table(plays, _schedule("G", "2024-09-08 13:00", home_score=3))
    made = built.drives[built.drives["drive"].eq(1)]
    assert list(made["outcome"]) == ["FG_MADE"]
    assert int(made["posteam_points"].iloc[0]) == 3


def test_synthetic_game_reconciles_return_td_and_pick_six():
    plays = pd.DataFrame([
        _play(play_id=1, drive=1, play_type="kickoff", kickoff_attempt=1, posteam="BUF", total_away_score=0),
        _play(
            play_id=2, drive=1, play_type="pass", pass_=1, posteam="BUF", defteam="KC",
            passer_player_id="QB_BUF", yards_gained=20, yardline_100=75,
            posteam_timeouts_remaining=3, defteam_timeouts_remaining=3,
        ),
        _play(
            play_id=3, drive=1, play_type="pass", pass_=1, posteam="BUF", defteam="KC",
            touchdown=1, td_team="BUF", yards_gained=40, total_away_score=6,
        ),
        _play(
            play_id=4, drive=1, play_type="extra_point", extra_point_attempt=1,
            posteam="BUF", total_away_score=7,
        ),
        _play(
            play_id=5, drive=2, play_type="kickoff", kickoff_attempt=1, posteam="KC",
            touchdown=1, td_team="KC", total_home_score=6, total_away_score=7,
        ),
        _play(
            play_id=6, drive=2, play_type="extra_point", extra_point_attempt=1,
            posteam="KC", total_home_score=7, total_away_score=7,
        ),
        _play(
            play_id=7, drive=3, play_type="punt", punt_attempt=1, posteam="BUF", defteam="KC",
            total_home_score=7, total_away_score=7,
        ),
        _play(
            play_id=8, drive=4, play_type="pass", pass_=1, posteam="KC", defteam="BUF",
            interception=1, touchdown=1, td_team="BUF", passer_player_id="QB_KC",
            total_home_score=7, total_away_score=13,
        ),
        _play(
            play_id=9, drive=4, play_type="extra_point", extra_point_attempt=1,
            posteam="BUF", total_home_score=7, total_away_score=14,
        ),
        _play(
            play_id=10, drive=5, play_type="qb_kneel", qb_kneel=1, rush=1, posteam="BUF", defteam="KC",
            rusher_player_id="QB_KNEEL", yards_gained=-1, qtr=4, total_home_score=7, total_away_score=14,
        ),
    ])
    schedule = _schedule("G", "2024-09-08 13:00", home_score=7, away_score=14)
    built = build_drive_table(plays, schedule)
    assert list(built.drives["outcome"]) == ["TD", "PUNT", "TURNOVER", "END_OF_HALF"]
    assert list(built.drives["posteam"]) == ["BUF", "BUF", "KC", "BUF"]
    starters = [None if pd.isna(value) else value for value in built.drives["drive_first_passer_id"]]
    assert starters == ["QB_BUF", None, "QB_KC", "QB_KNEEL"]
    assert int(built.drives.loc[built.drives["outcome"].eq("TD"), "posteam_points"].iloc[0]) == 7
    assert int(built.drives.loc[built.drives["outcome"].eq("TURNOVER"), "defteam_points"].iloc[0]) == 7
    assert bool(built.drives.loc[built.drives["posteam"].eq("KC"), "posteam_is_home"].iloc[0]) is True
    mismatches = reconcile(built.drives, built.non_offensive, built.games)
    assert len(mismatches) == 0
    assert len(built.drives) == 4


def test_a_wrong_final_score_is_listed_and_the_drive_is_kept():
    plays = _drive_plays("G", drive=1, play_id=1, posteam="KC", play_type="pass", total_home_score=0)
    built = build_drive_table(plays, _schedule("G", "2024-09-08 13:00", home_score=21, away_score=0))
    mismatches = reconcile(built.drives, built.non_offensive, built.games)
    assert len(built.drives) == 1
    assert list(mismatches["game_id"]) == ["G"]
    assert list(mismatches["reason"]) == ["score_mismatch"]


def test_an_earlier_drive_filed_late_cannot_erase_the_score():
    plays = pd.DataFrame([
        _play(
            play_id=1, drive=2, play_type="field_goal", field_goal_attempt=1,
            field_goal_result="made", posteam="KC", total_home_score=3,
            quarter_seconds_remaining=100,
        ),
        _play(
            play_id=2, drive=1, play_type="qb_kneel", qb_kneel=1, posteam="KC",
            total_home_score=0, quarter_seconds_remaining=30,
        ),
        _play(play_id=3, drive=2, play_type=None, total_home_score=3, quarter_seconds_remaining=0),
    ])
    built = build_drive_table(plays, _schedule("G", "2024-09-08 13:00", home_score=3))
    made = built.drives[built.drives["outcome"].eq("FG_MADE")]
    assert int(made["posteam_points"].sum()) == 3
    assert len(reconcile(built.drives, built.non_offensive, built.games)) == 0


def test_a_trailing_no_play_cannot_erase_the_score_and_a_safety_still_counts():
    wiped = pd.DataFrame([
        _play(play_id=1, play_type="pass", posteam="KC", touchdown=1, td_team="KC", total_home_score=6),
        _play(play_id=2, play_type="extra_point", extra_point_attempt=1, posteam="KC", total_home_score=7),
        _play(play_id=9, drive=1, play_type="no_play", timeout=0, posteam="KC", total_home_score=0),
    ])
    built = build_drive_table(wiped, _schedule("G", "2024-09-08 13:00", home_score=7))
    assert int(built.drives.iloc[0]["posteam_points"]) == 7
    safety = pd.DataFrame([
        _play(
            play_id=1, play_type="no_play", penalty=1, safety=1, posteam="BUF", defteam="KC",
            total_home_score=2,
        ),
    ])
    safe = build_drive_table(safety, _schedule("G", "2024-09-08 13:00", home_score=2))
    assert list(safe.drives["outcome"]) == ["SAFETY"]
    assert int(safe.drives.iloc[0]["defteam_points"]) == 2


def test_relocated_franchise_codes_still_get_a_home_flag():
    plays = _drive_plays(
        "2015_01_SEA_STL", drive=1, play_id=1, posteam="LA", defteam="SEA",
        home_team="LA", away_team="SEA", play_type="pass",
    )
    schedule = pd.DataFrame([{
        "game_id": "2015_01_SEA_STL",
        "season": 2015,
        "week": 1,
        "game_type": "REG",
        "kickoff": "2015-09-13 13:00",
        "home_team": "STL",
        "away_team": "SEA",
        "home_score": 0,
        "away_score": 0,
    }])
    built = build_drive_table(plays, schedule)
    assert bool(built.drives.iloc[0]["posteam_is_home"]) is True
    assert built.drives.iloc[0]["posteam"] == "LA"


def test_pregame_allowlist_does_not_read_the_in_game_passer():
    assert "drive_first_passer_id" not in PREGAME_COLUMNS
    source = (
        Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim" / "pregame.py"
    ).read_text()
    assert "drive_first_passer_id" not in source
    kick = datetime(2024, 9, 8, 13, 0, tzinfo=EASTERN)
    charts = pd.DataFrame([
        {
            "season": 2024, "week": 1, "team": "BUF", "game_type": "REG",
            "position": "QB", "depth_team": "1", "gsis_id": "QB_CHART",
        },
    ])
    games = pd.DataFrame([{
        "game_id": "G", "season": 2024, "week": 1, "game_type": "REG",
        "kickoff_et": kick, "home_team": "BUF", "away_team": "KC",
        "drive_first_passer_id": "IN_GAME_PASSER",
    }])
    table = pregame_quarterback_table(charts, games)
    assert list(table.columns) == list(PREGAME_COLUMNS)
    assert "drive_first_passer_id" not in table.columns
    assert set(table["qb_id"].dropna()) == {"QB_CHART"}


def test_mixed_depth_chart_uses_week_rows_and_snapshot_rows():
    kick = datetime(2025, 9, 7, 13, 0, tzinfo=EASTERN)
    charts = pd.DataFrame([
        {
            "season": 2025, "week": 1, "team": "BUF", "game_type": "REG",
            "position": "QB", "depth_team": "1", "gsis_id": "QB_WEEK",
            "dt": pd.NA, "pos_abb": pd.NA, "pos_rank": pd.NA,
        },
        {
            "season": pd.NA, "week": pd.NA, "team": "KC", "game_type": pd.NA,
            "position": pd.NA, "depth_team": pd.NA, "gsis_id": "QB_EARLY",
            "dt": "2025-09-01T16:00:00Z", "pos_abb": "QB", "pos_rank": 1,
        },
        {
            "season": pd.NA, "week": pd.NA, "team": "KC", "game_type": pd.NA,
            "position": pd.NA, "depth_team": pd.NA, "gsis_id": "QB_BACKUP",
            "dt": "2025-09-01T16:00:00Z", "pos_abb": "QB", "pos_rank": 2,
        },
        {
            "season": pd.NA, "week": pd.NA, "team": "KC", "game_type": pd.NA,
            "position": pd.NA, "depth_team": pd.NA, "gsis_id": "QB_AFTER",
            "dt": "2025-09-08T16:00:00Z", "pos_abb": "QB", "pos_rank": 1,
        },
    ])
    week_id, week_asof = pregame_quarterback(
        charts, season=2025, week=1, team="BUF", game_type="REG", kickoff=kick,
    )
    snap_id, snap_asof = pregame_quarterback(
        charts, season=2025, week=1, team="KC", game_type="REG", kickoff=kick,
    )
    assert week_id == "QB_WEEK"
    assert week_asof == kick - timedelta(hours=1)
    assert snap_id == "QB_EARLY"
    assert snap_asof is not None and snap_asof < kick
    assert_pregame(snap_asof, kick, "qb")
    games = pd.DataFrame([{
        "game_id": "G", "season": 2025, "week": 1, "game_type": "REG",
        "kickoff_et": kick, "home_team": "BUF", "away_team": "KC",
    }])
    table = pregame_quarterback_table(charts, games)
    by_team = dict(zip(table["team"], table["qb_id"]))
    assert by_team["BUF"] == week_id
    assert by_team["KC"] == snap_id


def test_snapshot_depth_chart_after_kickoff_is_not_the_pregame_qb():
    kick = datetime(2025, 9, 7, 13, 0, tzinfo=EASTERN)
    charts = pd.DataFrame([
        {"dt": "2025-09-01T16:00:00Z", "team": "BUF", "pos_abb": "QB", "pos_rank": 1, "gsis_id": "QB_EARLY"},
        {"dt": "2025-09-01T16:00:00Z", "team": "BUF", "pos_abb": "QB", "pos_rank": 2, "gsis_id": "QB_BACKUP"},
        {"dt": "2025-09-08T16:00:00Z", "team": "BUF", "pos_abb": "QB", "pos_rank": 1, "gsis_id": "QB_AFTER"},
    ])
    qb_id, asof = pregame_quarterback(
        charts, season=2025, week=1, team="BUF", game_type="REG", kickoff=kick,
    )
    assert qb_id == "QB_EARLY"
    assert asof is not None and asof < kick
    assert_pregame(asof, kick, "qb")


def test_pregame_qb_is_the_depth_chart_one_hour_before_kickoff():
    kick = datetime(2024, 9, 8, 13, 0, tzinfo=EASTERN)
    charts = pd.DataFrame([
        {"season": 2024, "week": 1, "team": "BUF", "game_type": "REG", "position": "QB", "depth_team": "1", "gsis_id": "QB_CHART"},
        {"season": 2024, "week": 1, "team": "BUF", "game_type": "REG", "position": "QB", "depth_team": "2", "gsis_id": "QB_BACKUP"},
        {"season": 2024, "week": 1, "team": "BUF", "game_type": "POST", "position": "QB", "depth_team": "1", "gsis_id": "QB_POST"},
        {"season": 2024, "week": 2, "team": "BUF", "game_type": "REG", "position": "QB", "depth_team": "1", "gsis_id": "QB_NEXT"},
    ])
    qb_id, asof = pregame_quarterback(
        charts, season=2024, week=1, team="BUF", game_type="REG", kickoff=kick,
    )
    assert qb_id == "QB_CHART"
    assert asof == kick - timedelta(hours=1)
    assert_pregame(asof, kick, "qb")
    missing, _ = pregame_quarterback(
        charts, season=2024, week=1, team="KC", game_type="REG", kickoff=kick,
    )
    assert missing is None
    later, _ = pregame_quarterback(
        charts, season=2024, week=3, team="BUF", game_type="REG", kickoff=kick,
    )
    assert later is None


def _outcome_snippets():
    return [
        ("td", pd.DataFrame([_play(play_type="pass", touchdown=1, td_team="KC", posteam="KC")]), False),
        ("fg", pd.DataFrame([_play(play_type="field_goal", field_goal_result="made", field_goal_attempt=1)]), False),
        ("miss", pd.DataFrame([_play(play_type="field_goal", field_goal_result="missed", field_goal_attempt=1)]), False),
        ("punt", pd.DataFrame([_play(play_type="punt", punt_attempt=1)]), False),
        ("turnover", pd.DataFrame([_play(play_type="pass", interception=1, posteam="KC")]), False),
        ("downs", pd.DataFrame([_play(play_type="pass", down=4, first_down=0, posteam="KC")]), False),
        ("safety", pd.DataFrame([_play(play_type="pass", safety=1, posteam="KC")]), False),
        ("half", pd.DataFrame([_play(play_type="run", posteam="KC", qtr=2)]), True),
        ("other", pd.DataFrame([_play(play_type="no_play", penalty=1, posteam="KC", qtr=1)]), False),
    ]


def _game(game_id, kickoff, **extra):
    row = {
        "game_id": game_id,
        "season": extra.pop("season", 2026 if str(kickoff).startswith("2026") or str(kickoff).startswith("2027-01-01") else 2024),
        "week": extra.pop("week", 1),
        "game_type": extra.pop("game_type", "REG"),
        "kickoff": kickoff,
        "home_team": extra.pop("home_team", "KC"),
        "away_team": extra.pop("away_team", "BUF"),
        "home_score": extra.pop("home_score", 0),
        "away_score": extra.pop("away_score", 0),
    }
    row.update(extra)
    return row


def _schedule(game_id, kickoff, **extra):
    return pd.DataFrame([_game(game_id, kickoff, **extra)])


def _drive_plays(game_id, **overrides):
    row = _play(**overrides)
    row["game_id"] = game_id
    return pd.DataFrame([row])


def _play(**overrides):
    play_id = overrides.get("play_id", 1)
    row = {
        "game_id": "G",
        "play_id": play_id,
        "fixed_drive": overrides.get("drive", 1),
        "posteam": "KC",
        "defteam": "BUF",
        "home_team": "KC",
        "away_team": "BUF",
        "play_type": "pass",
        "qtr": 1,
        "quarter_seconds_remaining": 10000 - play_id,
        "half_seconds_remaining": 1800,
        "game_seconds_remaining": 3600,
        "yardline_100": 75,
        "down": 1,
        "yards_gained": 0,
        "score_differential": 0,
        "posteam_timeouts_remaining": 3,
        "defteam_timeouts_remaining": 3,
        "total_home_score": 0,
        "total_away_score": 0,
        "touchdown": 0,
        "td_team": None,
        "safety": 0,
        "field_goal_result": None,
        "field_goal_attempt": 0,
        "extra_point_attempt": 0,
        "extra_point_result": None,
        "two_point_attempt": 0,
        "punt_attempt": 0,
        "interception": 0,
        "fumble_lost": 0,
        "pass": 0,
        "rush": 0,
        "qb_kneel": 0,
        "timeout": 0,
        "kickoff_attempt": 0,
        "own_kickoff_recovery": 0,
        "penalty": 0,
        "first_down": 0,
        "return_touchdown": 0,
        "passer_player_id": None,
        "rusher_player_id": None,
    }
    row.update(overrides)
    if "pass_" in row:
        row["pass"] = row.pop("pass_")
    row.pop("drive", None)
    return row
