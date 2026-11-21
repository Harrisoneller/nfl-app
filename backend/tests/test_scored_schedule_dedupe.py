"""A completed ESPN score must not be dropped for a scoreless duplicate."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.game import Game
from app.services import scores_service
from app.services.predictions_service import _schedule_from_db


def test_post_score_is_kept_over_a_scoreless_duplicate():
    """Week 11: IND 3 @ HOU 26. ESPN status is post, not final."""
    final = scores_service._scoreboard_values({
        "status": "post",
        "season": 2026,
        "week": 11,
        "home_score": 26,
        "away_score": 3,
        "home": {},
        "away": {},
    }, {})
    assert final["home_score"] == 26
    assert final["away_score"] == 3

    scheduled = scores_service._scoreboard_values({
        "status": "pre",
        "season": 2026,
        "week": 11,
        "home_score": 0,
        "away_score": 0,
        "home": {},
        "away": {},
    }, {})
    assert scheduled["home_score"] is None

    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    kick = datetime(2026, 11, 19, 1, 15, tzinfo=UTC)
    db.add(Game(
        id="2026_11_IND_HOU", season=2026, week=11, season_type=2,
        start_time=kick, status="scheduled",
        home_team_id="HOU", away_team_id="IND",
        home_score=None, away_score=None, raw={},
    ))
    db.add(Game(
        id="401873100", season=2026, week=11, season_type=2,
        start_time=kick, status="post",
        home_team_id="HOU", away_team_id="IND",
        home_score=26, away_score=3, raw={},
    ))
    db.commit()

    rows = scores_service.list_team_schedule(db, "HOU", 2026)
    assert len(rows) == 1
    assert rows[0].home_score == 26
    assert rows[0].away_score == 3

    slate = _schedule_from_db(db, 2026)
    assert int(slate.iloc[0]["home_score"]) == 26
    assert int(slate.iloc[0]["away_score"]) == 3
    db.close()
