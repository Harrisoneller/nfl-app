"""Season helpers.

NFL season N runs roughly Sep N → Feb N+1. Three states matter:

- **completed**  — Super Bowl already played (>= March 1 of year N+1).
- **current/upcoming** — season N hasn't completed yet. From Mar 1, Y onward,
  season Y is the "current/upcoming" season; before Sep it's previewing
  (schedule released, training camp, etc.), after Sep it's live.
"""
from __future__ import annotations

from datetime import date

SEASON_RANGE_START = 2020


def latest_completed_season(today: date | None = None) -> int:
    """Most recent season whose Super Bowl has been played."""
    t = today or date.today()
    if t.month >= 3:
        return t.year - 1
    return t.year - 2


def current_or_upcoming_season(today: date | None = None) -> int:
    """The season that is upcoming/in-progress as of `today`."""
    return latest_completed_season(today) + 1


def is_season_upcoming(season: int, today: date | None = None) -> bool:
    """True if `season` hasn't reached its kickoff yet (no live PBP)."""
    t = today or date.today()
    # NFL season starts in early September.
    return season > latest_completed_season(t)


def available_seasons(today: date | None = None) -> list[int]:
    """All seasons offered in the dropdown, newest first.

    Always includes the current/upcoming season at index 0 so users can
    see preview information (schedule, rosters) before PBP is available.
    """
    upcoming = current_or_upcoming_season(today)
    out = [upcoming]
    out.extend(range(upcoming - 1, SEASON_RANGE_START - 1, -1))
    return out


def is_nfl_live_period(today: date | None = None) -> bool:
    """True during regular season + playoffs (roughly Sep–Feb).

    Used to gate high-frequency jobs (live ESPN scoreboard). Mar–Aug is treated
    as offseason for those intervals.
    """
    t = today or date.today()
    return t.month >= 9 or t.month <= 2


def season_info(season: int, today: date | None = None) -> dict:
    """Metadata about a single season for the UI."""
    upcoming = is_season_upcoming(season, today)
    return {
        "season": season,
        "is_upcoming": upcoming,
        "is_latest_completed": season == latest_completed_season(today),
    }
