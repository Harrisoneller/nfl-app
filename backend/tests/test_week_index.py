"""Tests for NFL week assignment.

Sparky used to select "this week" with a rolling time window anchored on the
earliest still-upcoming kickoff, six and a half days wide. The two failures that
produced were not the ones you would guess from reading it, so they are pinned
here as behaviour rather than described in a comment:

  - the board **shrank through the week** as games were played, because anything
    more than four hours old fell out of the window;
  - **weeks bled together**, because once Sunday's games finished the anchor
    moved to the last game of the week and the window stretched into the next
    one.

Both are asserted below against a schedule shaped like a real NFL season:
Thursday night through Monday night, with a late MNF that naive Saturday-end
bucketing would push into the next week.

Pure — no DB. `WeekIndex` is constructed directly so these run anywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.week_index import WeekBounds, WeekIndex

U = timezone.utc


def _index() -> WeekIndex:
    """A 2025-shaped opening to the NFL season (Thu–Mon weeks)."""
    bounds = {
        1: WeekBounds(1, datetime(2025, 9, 4, 20, tzinfo=U), datetime(2025, 9, 8, 23, tzinfo=U)),
        2: WeekBounds(2, datetime(2025, 9, 11, 20, tzinfo=U), datetime(2025, 9, 15, 23, tzinfo=U)),
        3: WeekBounds(3, datetime(2025, 9, 18, 20, tzinfo=U), datetime(2025, 9, 22, 23, tzinfo=U)),
        4: WeekBounds(4, datetime(2025, 9, 25, 20, tzinfo=U), datetime(2025, 9, 29, 23, tzinfo=U)),
    }
    by_pair = {
        ("KC", "LAC"): 1,     # TNF
        ("BUF", "MIA"): 1,
        ("PHI", "DAL"): 1,
        ("SF", "SEA"): 1,     # MNF
        ("BAL", "PIT"): 2,
        ("GB", "MIN"): 2,
        ("DET", "CHI"): 3,
        ("NYJ", "NYG"): 4,
    }
    return WeekIndex(season=2025, by_pair=by_pair, bounds=bounds)


# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #


def test_exact_team_pair_match_wins():
    idx = _index()
    assert idx.assign("BUF", "MIA", datetime(2025, 9, 7, 17, tzinfo=U)) == 1
    assert idx.assign("BAL", "PIT", datetime(2025, 9, 14, 17, tzinfo=U)) == 2


def test_reversed_pair_matches_for_neutral_site_games():
    """The odds feed's home/away can disagree with the schedule's — common in
    London / Mexico / Super Bowl weeks."""
    idx = _index()
    assert idx.assign("MIA", "BUF", datetime(2025, 9, 7, 17, tzinfo=U)) == 1


def test_monday_night_stays_in_week_one():
    """Week 1 ends on a Monday. A bucketer that assumes weeks end on
    Sunday pushes this into Week 2."""
    idx = _index()
    assert idx.assign("SF", "SEA", datetime(2025, 9, 8, 23, tzinfo=U)) == 1


def test_thursday_night_is_not_folded_into_the_previous_week():
    idx = _index()
    assert idx.assign("KC", "LAC", datetime(2025, 9, 4, 20, tzinfo=U)) == 1
    assert idx.assign("BAL", "PIT", datetime(2025, 9, 11, 20, tzinfo=U)) == 2


def test_unmatched_teams_fall_back_to_kickoff_date():
    """International / flex games whose Odds API home/away don't match the
    schedule still have to land in a week — dropping them empties the board."""
    idx = _index()
    assert idx.assign(None, None, datetime(2025, 9, 7, 17, tzinfo=U)) == 1
    assert idx.assign("KC", None, datetime(2025, 9, 14, 20, tzinfo=U)) == 2


def test_a_game_outside_every_range_attaches_to_the_nearest_week():
    """A game we place slightly wrong is still visible and reasoned about; a
    game we drop is invisible. Never drop."""
    idx = _index()
    assert idx.assign(None, None, datetime(2025, 9, 17, 12, tzinfo=U)) in (2, 3)
    assert idx.assign(None, None, datetime(2025, 8, 1, 12, tzinfo=U)) == 1


def test_assignment_is_stable_regardless_of_when_it_is_asked():
    """The old window's defining flaw: the answer depended on `now`."""
    idx = _index()
    kickoff = datetime(2025, 9, 7, 17, tzinfo=U)
    assert idx.assign("PHI", "DAL", kickoff) == 1
    # Same call, a week later in wall-clock time — same answer, by construction.
    assert idx.assign("PHI", "DAL", kickoff) == 1


# --------------------------------------------------------------------------- #
# Current week
# --------------------------------------------------------------------------- #


def test_current_week_holds_through_the_week_being_played():
    """A Sunday-night viewer means the week they are watching, not next
    Thursday. The old window had already moved on by then."""
    idx = _index()
    assert idx.current_week(datetime(2025, 9, 2, 12, tzinfo=U)) == 1   # Tuesday
    assert idx.current_week(datetime(2025, 9, 5, 12, tzinfo=U)) == 1   # Friday
    assert idx.current_week(datetime(2025, 9, 7, 12, tzinfo=U)) == 1   # Sunday
    assert idx.current_week(datetime(2025, 9, 8, 20, tzinfo=U)) == 1   # MNF


def test_current_week_before_the_season_is_week_one():
    idx = _index()
    assert idx.current_week(datetime(2025, 8, 1, 12, tzinfo=U)) == 1


def test_current_week_after_the_season_is_the_last_week():
    idx = _index()
    assert idx.current_week(datetime(2026, 2, 1, 12, tzinfo=U)) == 4


# --------------------------------------------------------------------------- #
# Degraded mode
# --------------------------------------------------------------------------- #


def test_an_empty_index_reports_unavailable_rather_than_guessing():
    """Offseason or a fresh database. Callers degrade to a time window; a week
    filter that silently matched nothing would empty the board, which is worse
    than showing an unbucketed slate."""
    idx = WeekIndex(season=2025)
    assert idx.available is False
    assert idx.current_week() is None
    assert idx.assign("KC", "BUF", datetime(2025, 9, 7, tzinfo=U)) is None


def test_naive_datetimes_do_not_raise():
    """SQLite hands back naive datetimes where Postgres returns aware ones, so
    a naive kickoff reaching the comparison would raise TypeError on one backend
    and work on the other."""
    idx = _index()
    naive = datetime(2025, 9, 7, 17)
    assert idx.assign(None, None, naive) == 1


# --------------------------------------------------------------------------- #
# The regression, stated as the old behaviour
# --------------------------------------------------------------------------- #


def _old_rolling_window(now: datetime, kickoffs: list[tuple[int, datetime]]):
    """The pre-change `_current_event_rows` selection, verbatim."""
    cutoff = now - timedelta(hours=4)
    rows = [k for k in kickoffs if k[1] >= cutoff]
    if not rows:
        return []
    earliest = min(k[1] for k in rows)
    upper = earliest + timedelta(days=6, hours=12)
    return [k for k in rows if k[1] <= upper]


def test_the_old_window_bled_two_weeks_together_and_the_index_does_not():
    """Once Sunday's games finished, the anchor moved to the last game of the
    week and the window reached into the next one — so the board showed a mix of
    two weeks in a single list."""
    kickoffs = [
        (1, datetime(2025, 9, 4, 20, tzinfo=U)),
        (1, datetime(2025, 9, 7, 17, tzinfo=U)),
        (1, datetime(2025, 9, 8, 23, tzinfo=U)),
        (2, datetime(2025, 9, 11, 20, tzinfo=U)),
        (2, datetime(2025, 9, 14, 17, tzinfo=U)),
    ]
    monday = datetime(2025, 9, 8, 12, tzinfo=U)
    old = _old_rolling_window(monday, kickoffs)
    assert {wk for wk, _ in old} == {1, 2}, "the old window really did mix weeks"

    idx = _index()
    week_one = [k for k in kickoffs if idx.assign(None, None, k[1]) == 1]
    assert {wk for wk, _ in week_one} == {1}


def test_the_old_window_lost_games_as_they_were_played():
    """The board shrank through the week. Week membership does not."""
    kickoffs = [
        (1, datetime(2025, 9, 4, 20, tzinfo=U)),
        (1, datetime(2025, 9, 7, 17, tzinfo=U)),
        (1, datetime(2025, 9, 8, 23, tzinfo=U)),
    ]
    tuesday = _old_rolling_window(datetime(2025, 9, 2, 12, tzinfo=U), kickoffs)
    monday = _old_rolling_window(datetime(2025, 9, 8, 12, tzinfo=U), kickoffs)
    assert len(tuesday) == 3
    assert len(monday) == 1, "the old window had dropped two thirds of the week"

    # The index's answer is a property of the game, not of when you ask.
    idx = _index()
    assert all(idx.assign(None, None, k[1]) == 1 for k in kickoffs)
