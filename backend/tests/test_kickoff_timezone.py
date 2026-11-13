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


def test_unrepaired_repaired_and_mixed_weeks_emit_true_utc():
    """A 1:00 PM ET kick is 18:00Z in November and 17:00Z in September.

    Unrepaired rows still store the Eastern clock labeled UTC. Repaired and
    ESPN rows store the real instant. A mixed week must emit both correctly.
    """
    db = _db()
    cases = (
        ("2026_11_BUF_KC", datetime(2026, 11, 15, 13, 0, tzinfo=UTC), {}, datetime(2026, 11, 15, 18, 0, tzinfo=UTC)),
        ("2026_11_DAL_PHI", datetime(2026, 11, 15, 18, 0, tzinfo=UTC), {"kickoff_zone": "America/New_York"}, datetime(2026, 11, 15, 18, 0, tzinfo=UTC)),
        ("401547320", datetime(2026, 11, 15, 18, 0, tzinfo=UTC), {}, datetime(2026, 11, 15, 18, 0, tzinfo=UTC)),
        ("2026_01_NYG_DAL", datetime(2026, 9, 13, 13, 0, tzinfo=UTC), {}, datetime(2026, 9, 13, 17, 0, tzinfo=UTC)),
        ("2026_01_PHI_ATL", datetime(2026, 9, 13, 17, 0, tzinfo=UTC), {"kickoff_zone": "America/New_York"}, datetime(2026, 9, 13, 17, 0, tzinfo=UTC)),
    )
    teams = {
        "2026_11_BUF_KC": ("KC", "BUF"),
        "2026_11_DAL_PHI": ("PHI", "DAL"),
        "401547320": ("NYJ", "NE"),
        "2026_01_NYG_DAL": ("DAL", "NYG"),
        "2026_01_PHI_ATL": ("ATL", "PHI"),
    }
    for gid, stored, raw, _expected in cases:
        week = 10 if gid.startswith("2026_11") or gid.startswith("401") else 1
        home, away = teams[gid]
        db.add(Game(
            id=gid, season=2026, week=week, season_type=2,
            start_time=stored, status="scheduled",
            home_team_id=home, away_team_id=away, raw=raw,
        ))
    db.commit()
    for gid, _stored, _raw, expected in cases:
        assert scores_service.trusted_kickoff_of(db.get(Game, gid)) == expected

    from app.services.predictions_service import _schedule_from_db

    slate = _schedule_from_db(db, 2026)
    by_id = {row.game_id: row for _, row in slate.iterrows()}
    assert by_id["2026_11_BUF_KC"].start_time.startswith("2026-11-15T18:00:00")
    assert by_id["2026_11_DAL_PHI"].start_time.startswith("2026-11-15T18:00:00")
    assert by_id["401547320"].start_time.startswith("2026-11-15T18:00:00")
    assert by_id["2026_11_BUF_KC"].gametime == "13:00"
    assert by_id["2026_01_NYG_DAL"].start_time.startswith("2026-09-13T17:00:00")
    assert by_id["2026_01_NYG_DAL"].gametime == "13:00"
    db.close()


def test_unrepaired_row_still_matches_closing_line_and_sparky():
    db = _db()
    stored = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
    real = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    db.add(Game(
        id="2026_10_BUF_KC", season=2026, week=10, start_time=stored,
        status="scheduled", home_team_id="KC", away_team_id="BUF", raw={},
    ))
    db.add(OddsSnapshot(
        event_id="odds-unrepaired",
        captured_at=datetime(2026, 11, 15, 17, 30, tzinfo=UTC),
        commence_time=real,
        book="dk",
        home_team_id="KC",
        away_team_id="BUF",
        home_spread=-3.5,
        total=47.5,
        raw={"season": 2026, "week": 10},
    ))
    db.commit()
    assert scores_service.fill_closing_lines_from_snapshots(db) == 1
    pred = type("Pred", (), {
        "event_id": "odds-unrepaired",
        "commence_time": real,
        "home_team_id": "KC",
        "away_team_id": "BUF",
    })()
    assert sparky_service._resolve_prediction_game(db, pred).id == "2026_10_BUF_KC"
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
        raw={"kickoff_zone": "America/New_York"},
    ))
    db.commit()
    pred = type("Pred", (), {
        "event_id": "odds-event",
        "commence_time": kick,
        "home_team_id": "KC",
        "away_team_id": "BUF",
    })()
    assert sparky_service._resolve_prediction_game(db, pred).id == "2026_10_BUF_KC"

    game = db.get(Game, "2026_10_BUF_KC")
    game.start_time = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
    game.raw = {}
    db.commit()
    assert sparky_service._resolve_prediction_game(db, pred).id == "2026_10_BUF_KC"
    game.start_time = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
    game.raw = {"kickoff_zone": "America/New_York"}
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
