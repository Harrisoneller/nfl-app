"""America/New_York cutoffs.

Adapted from the single-feature study's calendar rule. A naive timestamp is an
Eastern wall clock, the nflverse ``gameday`` plus ``gametime`` convention. An
aware timestamp is converted to Eastern before the calendar date is read.

The schedule parser is the only place a naive wall clock is accepted. Pre-game
row filters require an aware timestamp so a raw clock cannot skip that parse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")
SCREEN_CUTOFF = datetime(2027, 1, 1, tzinfo=EASTERN)
FIRST_SEASON = 2015
LAST_SEASON = 2026
# nflverse leaves a missing gametime as 1:00 PM Eastern.
DEFAULT_GAMETIME = "13:00"


class PregameError(ValueError):
    """A timestamp is missing, naive where an instant is required, or not pre-game."""


def eastern_kickoff(value: object) -> datetime | None:
    """Return the kickoff as an aware America/New_York time.

    Naive values are Eastern wall clocks. Aware values are converted.
    """
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


def before_screen_cutoff(value: object) -> bool:
    """True when the Eastern kickoff is strictly before 2027-01-01."""
    kick = eastern_kickoff(value)
    if kick is None:
        return False
    return kick < SCREEN_CUTOFF


def assert_pregame(asof: object, kickoff: object, label: str) -> None:
    """Raise unless ``asof`` is a timestamp strictly before kickoff."""
    stamp = eastern_kickoff(asof)
    start = eastern_kickoff(kickoff)
    if stamp is None or start is None:
        raise PregameError(f"{label} needs a pre-kickoff timestamp and a kickoff")
    if stamp >= start:
        raise PregameError(f"{label} is not strictly before kickoff")


def parse_wall_kickoff(gameday: object, gametime: object) -> tuple[datetime | None, bool]:
    """Localize an nflverse date and clock as America/New_York.

    Returns the kickoff and whether a missing clock was filled with 13:00.
    """
    if gameday is None:
        return None, False
    try:
        if pd.isna(gameday):
            return None, False
    except TypeError:
        return None, False
    day = str(gameday)[:10]
    defaulted = False
    if gametime is None:
        defaulted = True
    else:
        try:
            missing = pd.isna(gametime)
        except TypeError:
            missing = False
        text = "" if missing else str(gametime).strip()
        if text in ("", "nan", "NaT", "None"):
            defaulted = True
        else:
            clock_text = text[:8]
    if defaulted:
        clock_text = DEFAULT_GAMETIME
    hour_text, minute_text = clock_text.split(":")[:2]
    local = datetime.fromisoformat(
        f"{day}T{int(hour_text):02d}:{int(minute_text):02d}:00"
    ).replace(tzinfo=EASTERN)
    return local, defaulted


def _as_eastern_series(values: list[datetime | None]) -> pd.Series:
    stamps = []
    for value in values:
        if value is None:
            stamps.append(pd.NaT)
        else:
            stamps.append(pd.Timestamp(value))
    series = pd.Series(stamps, dtype="object")
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    return parsed.dt.tz_convert(EASTERN)


@dataclass
class ScheduleSplit:
    """Eligible games and the rows the cutoff removed."""

    games: pd.DataFrame
    dropped: pd.DataFrame
    defaulted_gametime: int


def prepare_schedule(frame: pd.DataFrame) -> ScheduleSplit:
    """Keep 2015–2026 games that kick off strictly before 2027-01-01 Eastern.

    ``gameday`` plus ``gametime`` is the nflverse wall clock. A ``kickoff``
    column is accepted for frames that already carry a timestamp: naive values
    are Eastern wall clocks, and aware values are converted before the cutoff.
    """
    if frame is None or len(frame) == 0:
        empty = pd.DataFrame(columns=[
            "game_id", "season", "week", "game_type", "kickoff_et",
            "home_team", "away_team", "home_score", "away_score",
        ])
        dropped = pd.DataFrame(columns=[
            "game_id", "season", "week", "game_type", "kickoff_et", "reason",
        ])
        return ScheduleSplit(empty, dropped, 0)

    work = frame.copy()
    defaulted = 0
    if "gameday" in work.columns:
        parsed: list[datetime | None] = []
        for day, clock in zip(work["gameday"], work.get("gametime", pd.Series([None] * len(work)))):
            kick, filled = parse_wall_kickoff(day, clock)
            parsed.append(kick)
            defaulted += int(filled and kick is not None)
        work["kickoff_et"] = _as_eastern_series(parsed).to_numpy()
    elif "kickoff" in work.columns:
        work["kickoff_et"] = _as_eastern_series(
            [eastern_kickoff(value) for value in work["kickoff"]]
        ).to_numpy()
    elif "kickoff_et" in work.columns:
        work["kickoff_et"] = _as_eastern_series(
            [eastern_kickoff(value) for value in work["kickoff_et"]]
        ).to_numpy()
    else:
        raise PregameError("schedule needs gameday or kickoff")

    work["season"] = pd.to_numeric(work.get("season"), errors="coerce")
    reasons = []
    for row in work.itertuples(index=False):
        kick = row.kickoff_et
        season = row.season
        if pd.isna(kick):
            reasons.append("missing_kickoff")
        elif pd.isna(season) or int(season) < FIRST_SEASON or int(season) > LAST_SEASON:
            reasons.append("season_out_of_range")
        elif pd.Timestamp(kick) >= pd.Timestamp(SCREEN_CUTOFF):
            reasons.append("on_or_after_cutoff")
        else:
            reasons.append("")
    work["_drop_reason"] = reasons
    keep_cols = [
        "game_id", "season", "week", "game_type", "kickoff_et",
        "home_team", "away_team", "home_score", "away_score",
        "roof", "temp", "wind", "overtime",
    ]
    for column in keep_cols:
        if column not in work.columns:
            work[column] = pd.NA
    kept = work[work["_drop_reason"].eq("")].loc[:, keep_cols].copy()
    dropped = work[work["_drop_reason"].ne("")].copy()
    dropped = dropped.rename(columns={"_drop_reason": "reason"})
    drop_cols = ["game_id", "season", "week", "game_type", "kickoff_et", "reason"]
    dropped = dropped.loc[:, [column for column in drop_cols if column in dropped.columns]].copy()
    kept["game_id"] = kept["game_id"].astype(str)
    if len(dropped):
        dropped["game_id"] = dropped["game_id"].astype(str)
    kept = kept.drop_duplicates("game_id", keep="first")
    return ScheduleSplit(kept, dropped, defaulted)


def assert_output_kickoffs(series: pd.Series, label: str = "kickoff_et") -> None:
    """Raise unless every output kickoff is aware America/New_York and before the cutoff."""
    if len(series) == 0:
        return
    dtype = series.dtype
    tz = getattr(dtype, "tz", None)
    if tz is None or str(tz) != "America/New_York":
        raise PregameError(f"{label} must be tz-aware America/New_York")
    if series.isna().any():
        raise PregameError(f"{label} is missing")
    if not bool((series < pd.Timestamp(SCREEN_CUTOFF)).all()):
        raise PregameError(f"{label} includes a kickoff on or after 2027-01-01 Eastern")
