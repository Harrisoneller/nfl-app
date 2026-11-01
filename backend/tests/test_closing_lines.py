"""Stored closing lines: one sign, and grading that does not look elsewhere."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.bet import BetLeg
from app.models.game import Game
from app.models.odds_snapshot import OddsSnapshot
from app.services import bet_service, scores_service
from app.services.closing_line import grade_stored_close


def test_ats_and_clv_cover_home_away_and_pickem():
    # Home favored by 4. Model likes home by 1, so it takes away +4.
    # Final margin -1 does not cover +4. Away wins the bet.
    # Away's model number is +1 against a close of +4, so CLV is 1 - 4 = -3.
    home_fav = grade_stored_close(
        closing_spread=4.0,
        closing_total=47.5,
        model_margin=1.0,
        model_total=44.0,
        home_score=20,
        away_score=21,
    )
    assert home_fav["spread"]["result"] == "won"
    assert home_fav["spread"]["clv"] == -3.0
    assert home_fav["total"]["side"] == "under"
    assert home_fav["total"]["result"] == "won"  # 41 under 47.5

    # Away favored by 3 (home margin -3). Model likes home by 7 and takes home.
    # Home wins by 10 and covers +3.
    away_fav = grade_stored_close(
        closing_spread=-3.0,
        closing_total=None,
        model_margin=7.0,
        model_total=None,
        home_score=27,
        away_score=17,
    )
    assert away_fav["spread"]["result"] == "won"
    assert away_fav["total"] is None

    # Pick'em the model also calls a pick'em: no side.
    pickem = grade_stored_close(
        closing_spread=0.0,
        closing_total=44.0,
        model_margin=0.0,
        model_total=44.0,
        home_score=24,
        away_score=17,
    )
    assert pickem["spread"]["result"] is None
    assert pickem["spread"]["clv"] is None
    assert pickem["total"]["side"] is None

    # Landing on the number is a push, even when the model had a side.
    push = grade_stored_close(
        closing_spread=0.0,
        closing_total=40.0,
        model_margin=3.0,
        model_total=45.0,
        home_score=20,
        away_score=20,
    )
    assert push["spread"]["result"] == "push"
    assert push["total"]["result"] == "push"

    # No stored close means no grade, even if a caller could have guessed one.
    missing = grade_stored_close(
        closing_spread=None,
        closing_total=None,
        model_margin=7.0,
        model_total=44.0,
        home_score=24,
        away_score=10,
    )
    assert missing == {"spread": None, "total": None}


def test_schedule_sync_stores_nflverse_close(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    row = {
        "game_id": "2023_01_DET_KC",
        "season": 2023,
        "game_type": "REG",
        "week": 1,
        "gameday": "2023-09-07",
        "gametime": "20:20",
        "home_team": "KC",
        "away_team": "DET",
        "home_score": 20,
        "away_score": 21,
        "spread_line": 4.0,
        "total_line": 54.0,
        "stadium": "Arrowhead",
    }

    async def schedules(_season):
        return [row]

    monkeypatch.setattr(scores_service._nfl_data, "schedules", schedules)
    assert asyncio.run(scores_service.refresh_season_schedule(db, 2023)) == 1
    game = db.get(Game, "2023_01_DET_KC")
    assert game.closing_spread == 4.0
    assert game.closing_total == 54.0
    db.close()


def test_snapshot_fill_flips_sportsbook_sign_and_grading_ignores_it():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    OddsSnapshot.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    kick = datetime(2024, 9, 5, 20, tzinfo=timezone.utc)
    db.add(Game(
        id="g1",
        season=2024,
        week=1,
        start_time=kick,
        home_team_id="KC",
        away_team_id="BAL",
        home_score=27,
        away_score=20,
        status="final",
        raw={},
    ))
    db.add(OddsSnapshot(
        event_id="odds-1",
        captured_at=kick,
        commence_time=kick,
        snapshot_label="T4",
        book="dk",
        home_team_id="KC",
        away_team_id="BAL",
        home_spread=-3.0,  # sportsbook: KC favored by 3
        total=46.5,
        raw={},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 1
    game = db.get(Game, "g1")
    assert game.closing_spread == 3.0  # home margin, positive = home favored
    assert game.closing_total == 46.5

    # A second snapshot must not overwrite the stored nflverse-style number.
    game.closing_spread = 4.0
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 0
    assert db.get(Game, "g1").closing_spread == 4.0

    graded = grade_stored_close(
        closing_spread=game.closing_spread,
        closing_total=game.closing_total,
        model_margin=1.0,
        model_total=50.0,
        home_score=27,
        away_score=20,
    )
    # Close +4, model +1 takes away. Actual margin +7 covers, so the model lost.
    assert graded["spread"]["result"] == "lost"
    db.close()


def test_same_matchup_in_two_seasons_keeps_its_own_close():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    OddsSnapshot.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    kick_2023 = datetime(2023, 9, 7, 20, tzinfo=timezone.utc)
    kick_2024 = datetime(2024, 9, 5, 20, tzinfo=timezone.utc)
    db.add(Game(
        id="2023", season=2023, week=1, start_time=kick_2023,
        home_team_id="KC", away_team_id="BAL", status="final", raw={},
    ))
    db.add(Game(
        id="2024", season=2024, week=1, start_time=kick_2024,
        home_team_id="KC", away_team_id="BAL", status="final", raw={},
    ))
    db.add(OddsSnapshot(
        event_id="2024-only",
        captured_at=kick_2024,
        commence_time=kick_2024,
        snapshot_label="T4",
        book="dk",
        home_team_id="KC",
        away_team_id="BAL",
        home_spread=-6.0,
        total=47.0,
        raw={"season": 2024, "week": 1},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 1
    assert db.get(Game, "2023").closing_spread is None
    assert db.get(Game, "2024").closing_spread == 6.0
    db.close()


def test_a_future_games_close_is_never_used():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    OddsSnapshot.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    kick = datetime(2024, 9, 5, 20, tzinfo=timezone.utc)
    db.add(Game(
        id="this-year", season=2024, week=1, start_time=kick,
        home_team_id="KC", away_team_id="BAL", status="final", raw={},
    ))
    future = datetime(2025, 9, 4, 20, tzinfo=timezone.utc)
    db.add(OddsSnapshot(
        event_id="next-year",
        captured_at=kick,
        commence_time=future,
        snapshot_label="T4",
        book="dk",
        home_team_id="KC",
        away_team_id="BAL",
        home_spread=-10.0,
        total=51.0,
        raw={"season": 2025, "week": 1},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 0
    assert db.get(Game, "this-year").closing_spread is None
    assert db.get(Game, "this-year").closing_total is None
    db.close()


def test_in_game_snapshot_cannot_win_the_latest_capture():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    OddsSnapshot.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    kick = datetime(2024, 9, 5, 20, tzinfo=timezone.utc)
    db.add(Game(
        id="g", season=2024, week=1, start_time=kick,
        home_team_id="KC", away_team_id="BAL", status="final", raw={},
    ))
    # No T4 label, so the fallback is the latest capture. The in-game row
    # is later and must not replace the pre-kick number.
    db.add(OddsSnapshot(
        event_id="pre", captured_at=kick.replace(hour=18), commence_time=kick,
        snapshot_label="T2", book="dk",
        home_team_id="KC", away_team_id="BAL",
        home_spread=-3.0, total=46.0, raw={},
    ))
    db.add(OddsSnapshot(
        event_id="live", captured_at=kick.replace(minute=20), commence_time=kick,
        snapshot_label="T2", book="fd",
        home_team_id="KC", away_team_id="BAL",
        home_spread=-14.0, total=40.0, raw={},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 1
    game = db.get(Game, "g")
    assert game.closing_spread == 3.0
    assert game.closing_total == 46.0

    only_live = Game(
        id="late", season=2024, week=2, start_time=kick,
        home_team_id="BUF", away_team_id="MIA", status="final", raw={},
    )
    db.add(only_live)
    db.add(OddsSnapshot(
        event_id="only-live", captured_at=kick.replace(minute=30), commence_time=kick,
        snapshot_label=None, book="dk",
        home_team_id="BUF", away_team_id="MIA",
        home_spread=-7.0, total=48.0, raw={},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 0
    assert db.get(Game, "late").closing_spread is None
    db.close()


def test_bet_clv_uses_stored_close_for_both_sides():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    db.add(Game(
        id="2023_01_DET_KC",
        season=2023,
        home_team_id="KC",
        away_team_id="DET",
        closing_spread=4.0,
        closing_total=54.0,
        status="final",
        raw={},
    ))
    db.commit()

    home = BetLeg(
        market="spread",
        selection="KC",
        selection_label="KC -3",
        line=-3.0,
        odds_american=-110,
        odds_decimal=1.91,
        event_id="2023_01_DET_KC",
        home_team_id="KC",
        away_team_id="DET",
    )
    bet_service._apply_clv(db, home)
    # Stored home margin +4 is home -4. Betting home -3 beat that close by 1.
    assert home.closing_line == -4.0
    assert home.clv_line == 1.0
    assert home.beat_close is True

    away = BetLeg(
        market="spread",
        selection="DET",
        selection_label="DET +3.5",
        line=3.5,
        odds_american=-110,
        odds_decimal=1.91,
        event_id="2023_01_DET_KC",
        home_team_id="KC",
        away_team_id="DET",
    )
    bet_service._apply_clv(db, away)
    assert away.closing_line == 4.0
    assert away.clv_line == -0.5

    pickem_game = db.get(Game, "2023_01_DET_KC")
    pickem_game.closing_spread = 0.0
    db.commit()
    pick = BetLeg(
        market="spread",
        selection="KC",
        selection_label="KC +1",
        line=1.0,
        odds_american=-110,
        odds_decimal=1.91,
        event_id="2023_01_DET_KC",
        home_team_id="KC",
        away_team_id="DET",
    )
    bet_service._apply_clv(db, pick)
    assert pick.closing_line == 0.0
    assert pick.clv_line == 1.0

    # No stored close: do not invent one from a snapshot.
    bare = Game(id="bare", season=2024, status="final", raw={})
    db.add(bare)
    db.commit()
    naked = BetLeg(
        market="spread",
        selection="KC",
        selection_label="KC -3",
        line=-3.0,
        odds_american=-110,
        odds_decimal=1.91,
        event_id="bare",
        home_team_id="KC",
        away_team_id="DET",
    )
    bet_service._apply_clv(db, naked)
    assert naked.clv_line is None
    db.close()
