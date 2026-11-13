"""nflverse kickoffs are Eastern wall times stored as real UTC."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.game import Game
from app.models.odds_snapshot import OddsSnapshot
from app.services import scores_service, sparky_service

EST = datetime(2026, 11, 15, 13, 0, tzinfo=ZoneInfo("America/New_York"))
EDT = datetime(2026, 9, 13, 13, 0, tzinfo=ZoneInfo("America/New_York"))


def test_est_and_edt_wall_clocks_become_utc():
    assert scores_service._parse_kickoff("2026-11-15", "13:00") == EST.astimezone(UTC)
    assert scores_service._parse_kickoff("2026-11-15", "13:00") == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    assert scores_service._parse_kickoff("2026-09-13", "13:00") == EDT.astimezone(UTC)
    assert scores_service._parse_kickoff("2026-09-13", "13:00") == datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
    # A missing clock is still 1:00 PM Eastern, not 13:00 UTC.
    assert scores_service._parse_kickoff("2026-11-15", None) == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)


def test_eastern_date_is_kept_for_late_games():
    # 8:20 PM EST is 01:20 UTC the next day. The NFL date stays Sunday.
    start = scores_service._parse_kickoff("2026-11-15", "20:20")
    assert start == datetime(2026, 11, 16, 1, 20, tzinfo=UTC)
    gameday, gametime = scores_service.eastern_schedule_clock(start)
    assert gameday == "2026-11-15"
    assert gametime == "20:20"


def _db():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    OddsSnapshot.__table__.create(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_pre_kick_snapshot_counts_against_real_utc_kickoff():
    db = _db()
    kick = scores_service._parse_kickoff("2026-11-15", "13:00")
    assert kick is not None
    db.add(Game(
        id="2026_10_BUF_KC",
        season=2026,
        week=10,
        start_time=kick,
        status="scheduled",
        home_team_id="KC",
        away_team_id="BUF",
        raw={"kickoff_zone": "America/New_York"},
    ))
    # 17:30 UTC is before 18:00 UTC (1:00 PM ET) and after the old bogus 13:00Z label.
    db.add(OddsSnapshot(
        event_id="odds-1",
        captured_at=datetime(2026, 11, 15, 17, 30, tzinfo=UTC),
        commence_time=kick,
        book="dk",
        home_team_id="KC",
        away_team_id="BUF",
        home_spread=-3.5,
        total=47.5,
        raw={"season": 2026, "week": 10},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 1
    game = db.get(Game, "2026_10_BUF_KC")
    assert game.closing_spread == 3.5

    # The same capture is after a kickoff still labeled 13:00Z, so it must not close.
    game.start_time = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
    game.closing_spread = None
    game.closing_total = None
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 0
    db.close()


def test_sparky_match_uses_real_utc_window():
    db = _db()
    kick = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    db.add(Game(
        id="2026_10_BUF_KC",
        season=2026,
        week=10,
        start_time=kick,
        status="scheduled",
        home_team_id="KC",
        away_team_id="BUF",
        raw={},
    ))
    db.commit()
    pred = type("Pred", (), {
        "event_id": "odds-event",
        "commence_time": kick,
        "home_team_id": "KC",
        "away_team_id": "BUF",
    })()
    assert sparky_service._resolve_prediction_game(db, pred).id == "2026_10_BUF_KC"

    db.get(Game, "2026_10_BUF_KC").start_time = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
    db.commit()
    assert sparky_service._resolve_prediction_game(db, pred) is None
    db.close()


def test_kickoff_repair_is_idempotent():
    db = _db()
    game = Game(
        id="2026_10_BUF_KC",
        season=2026,
        week=10,
        start_time=datetime(2026, 11, 15, 13, 0, tzinfo=UTC),
        status="scheduled",
        home_team_id="KC",
        away_team_id="BUF",
        raw={},
    )
    db.add(game)
    db.commit()
    assert scores_service.apply_kickoff_repair(game) is True
    assert game.start_time == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    assert game.raw["kickoff_zone"] == "America/New_York"
    assert scores_service.apply_kickoff_repair(game) is False
    assert game.start_time == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)

    espn = Game(
        id="401547320",
        season=2026,
        week=10,
        start_time=datetime(2026, 11, 15, 18, 0, tzinfo=UTC),
        status="pre",
        raw={},
    )
    assert scores_service.kickoff_repair_needed(espn) is False
    db.close()
