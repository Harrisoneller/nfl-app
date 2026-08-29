"""Authoritative NFL week assignment, server-side.

Why this module exists
----------------------
Sparky used to select "this week's slate" with a rolling time window: anchor on
the earliest still-upcoming kickoff, take everything within 6 days 12 hours.
Both halves of that are wrong for the NFL.

- **The width is wrong.** An NFL week is Thursday through Monday (TNF, Sunday
  slates, MNF), and late-season flex / Saturday games plus the international
  window stretch it. A 6.5-day cap silently drops the tail.
- **The anchor is wrong.** Anchoring on the earliest *upcoming* kickoff means
  the window slides forward mid-week as games finish. The slate you saw Thursday
  morning is not the slate you see Saturday morning, and neither is "the week".

Weeks are a real, discrete thing in the NFL and the app already stores
them: ``Game.week``, populated from the schedule feed. ``odds_service.week_calendar``
already built the mapping — it just returned it to the API and nothing on the
server consumed it. This module makes that mapping usable server-side so Sparky,
the odds board and anything else agree on what "Week 3" means.

How a week is assigned
----------------------
Two passes, in order, because they have different reliability:

1. **Team-pair match.** ``(home_id, away_id)`` for the season is looked up in the
   schedule. This is exact — it is the official week from the feed — and it is
   used whenever both teams resolve and the pair is on the schedule.
2. **Kickoff-date fallback.** For anything the first pass cannot match, the
   kickoff is placed into the week whose observed kickoff range contains it, and
   failing that, into the nearest week by date distance.

Pass 2 exists for unmatched names (Odds API vs schedule home/away swaps on
neutral-site / London / Mexico games) so a game we cannot pair is still
placed by date rather than dropped.

The index is built once per request from a single query and answers in memory;
callers should not be doing per-game lookups against the database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.game import Game

#: Season types counted as part of the week structure: 2 = regular, 3 = post.
#: Preseason (1) has no betting board worth speaking of.
SEASON_TYPES = (2, 3)

#: How far outside a week's observed kickoff range a game may sit and still be
#: assigned to it by the date fallback. Generous on purpose: a week's range is
#: derived from scheduled games, and an odds event can precede the first
#: scheduled kickoff we know about (early lines, a rescheduled game).
_DATE_SLACK = timedelta(days=2)


@dataclass(frozen=True)
class WeekBounds:
    week: int
    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass
class WeekIndex:
    """Season schedule, indexed for week lookup."""

    season: int
    by_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    bounds: dict[int, WeekBounds] = field(default_factory=dict)

    # ------------------------------------------------------------------ #

    @property
    def weeks(self) -> list[int]:
        return sorted(self.bounds)

    @property
    def available(self) -> bool:
        """False when the schedule is not loaded — callers must degrade, not fail.

        True offseason, a fresh database, or a season whose schedule has not been
        synced yet. A week filter that silently matches nothing would empty the
        board, which is strictly worse than showing an unbucketed slate.
        """
        return bool(self.bounds)

    def assign(
        self,
        home_id: str | None,
        away_id: str | None,
        kickoff: datetime | None,
    ) -> int | None:
        """The official week for a game, or None if it cannot be placed."""
        if home_id and away_id:
            wk = self.by_pair.get((home_id, away_id))
            if wk is None:
                # The odds feed's home/away can disagree with the schedule's for
                # neutral-site games, which are common on international and playoff weeks.
                wk = self.by_pair.get((away_id, home_id))
            if wk is not None:
                return wk
        return self._by_date(kickoff)

    def _by_date(self, kickoff: datetime | None) -> int | None:
        if kickoff is None or not self.bounds:
            return None
        ts = _as_utc(kickoff)

        for wk in self.weeks:
            b = self.bounds[wk]
            if b.start - _DATE_SLACK <= ts <= b.end + _DATE_SLACK:
                return wk

        # Outside every range: attach to the nearest week rather than dropping
        # the game. A game we place slightly wrong is a game the user can still
        # see and reason about; a game we drop is the bug this module fixes.
        best, best_gap = None, None
        for wk in self.weeks:
            b = self.bounds[wk]
            gap = min(abs((ts - b.start).total_seconds()), abs((ts - b.end).total_seconds()))
            if best_gap is None or gap < best_gap:
                best, best_gap = wk, gap
        return best

    def current_week(self, now: datetime | None = None) -> int | None:
        """The week a user means by "this week".

        Defined as the first week that is not yet over — so a Sunday-night
        viewer still sees the week they are watching rather than being pushed to
        next Thursday, and a Tuesday viewer sees the week about to be played.
        Falls back to the last week of the season once the season is done.
        """
        if not self.bounds:
            return None
        ts = _as_utc(now or datetime.now(timezone.utc))
        for wk in self.weeks:
            if ts <= self.bounds[wk].end + _DATE_SLACK:
                return wk
        return self.weeks[-1]

    def bounds_for(self, week: int) -> WeekBounds | None:
        return self.bounds.get(week)

    def as_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "weeks": [self.bounds[w].as_dict() for w in self.weeks],
            "current_week": self.current_week(),
            "available": self.available,
        }


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def _as_utc(dt: datetime) -> datetime:
    """Normalize to aware UTC.

    Postgres returns aware datetimes and SQLite returns naive ones for the same
    column, so a comparison between a stored kickoff and ``datetime.now(utc)``
    raises TypeError on SQLite and silently works on Postgres. Every datetime
    entering this module goes through here.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def build(db: Session, season: int | None = None) -> WeekIndex:
    """Load the season's schedule and index it for week lookup."""
    from ..utils.seasons import current_or_upcoming_season

    if season is None:
        season = current_or_upcoming_season()

    rows = (
        db.query(Game.week, Game.home_team_id, Game.away_team_id, Game.start_time)
        .filter(Game.season == season)
        .filter(Game.week.isnot(None))
        .filter(Game.season_type.in_(SEASON_TYPES))
        .all()
    )

    by_pair: dict[tuple[str, str], int] = {}
    raw_bounds: dict[int, tuple[datetime, datetime]] = {}
    for wk, home, away, start in rows:
        week = int(wk)
        if home and away:
            by_pair[(home, away)] = week
        if start is None:
            continue
        ts = _as_utc(start)
        lo, hi = raw_bounds.get(week, (ts, ts))
        raw_bounds[week] = (min(lo, ts), max(hi, ts))

    return WeekIndex(
        season=season,
        by_pair=by_pair,
        bounds={w: WeekBounds(w, lo, hi) for w, (lo, hi) in raw_bounds.items()},
    )
