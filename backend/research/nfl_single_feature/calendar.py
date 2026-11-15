"""America/New_York cutoffs for every stage.

A naive timestamp is an Eastern wall clock, the nflverse `gameday` plus
`gametime` convention. An aware timestamp is converted to Eastern before the
calendar date is read.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .design import DesignError

EASTERN = ZoneInfo("America/New_York")
SCREEN_CUTOFF = datetime(2027, 1, 1, tzinfo=EASTERN)


def eastern_kickoff(value: object) -> datetime | None:
    """Return the kickoff as an aware America/New_York time."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        return stamp.to_pydatetime().replace(tzinfo=EASTERN)
    return stamp.tz_convert(EASTERN).to_pydatetime()


def is_january_2027_kickoff(value: object) -> bool:
    """True when the Eastern calendar date is in January 2027."""
    kick = eastern_kickoff(value)
    if kick is None:
        return False
    return kick.year == 2027 and kick.month == 1


def before_screen_cutoff(value: object) -> bool:
    """True when the Eastern kickoff is strictly before 2027-01-01."""
    kick = eastern_kickoff(value)
    if kick is None:
        return False
    return kick < SCREEN_CUTOFF


def assert_pregame(asof: object, kickoff: object, label: str) -> None:
    """Raise unless `asof` is a timestamp strictly before kickoff."""
    stamp = eastern_kickoff(asof)
    start = eastern_kickoff(kickoff)
    if stamp is None or start is None:
        raise DesignError(f"{label} needs a pre-kickoff timestamp and a kickoff")
    if stamp >= start:
        raise DesignError(f"{label} is not strictly before kickoff")
