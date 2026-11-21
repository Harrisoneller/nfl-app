"""One row per offensive drive, built from play-by-play.

Edge cases are summarized in ``EDGE_CASES.md``. The outcome is exactly one of
``OUTCOMES``. Points use repaired score deltas so a timeout row that still
shows the previous score cannot roll the drive total backward.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .calendar import PregameError, assert_output_kickoffs, prepare_schedule
from .market import assert_no_market_columns, strip_market_columns
from .teams import same_team

OUTCOMES = (
    "TD",
    "FG_MADE",
    "FG_MISSED",
    "PUNT",
    "TURNOVER",
    "DOWNS",
    "SAFETY",
    "END_OF_HALF",
    "OTHER",
)

DRIVE_COLUMNS = (
    "game_id",
    "season",
    "week",
    "game_type",
    "kickoff_et",
    "posteam",
    "defteam",
    "posteam_is_home",
    "drive",
    "start_yardline_100",
    "start_qtr",
    "start_game_seconds_remaining",
    "start_half_seconds_remaining",
    "start_score_diff",
    "start_posteam_timeouts",
    "start_defteam_timeouts",
    "outcome",
    "posteam_points",
    "defteam_points",
    "plays",
    "seconds_elapsed",
    "yards",
    "pass_plays",
    "rush_plays",
    "drive_first_passer_id",
)

SCHEMA_NOTES = {
    "game_id": "nflverse game id",
    "season": "Season year of the game",
    "week": "Week number",
    "game_type": "Schedule game type (REG, WC, DIV, CON, SB)",
    "kickoff_et": "Kickoff as a tz-aware America/New_York timestamp",
    "posteam": "Offensive team on the drive",
    "defteam": "Defensive team on the drive",
    "posteam_is_home": "True when the offense is the home team",
    "drive": "fixed_drive number, else the raw drive number",
    "start_yardline_100": "Yards from the opponent end zone at the first scrimmage play",
    "start_qtr": "Quarter at the first scrimmage play",
    "start_game_seconds_remaining": "Game clock at the start of the first scrimmage play",
    "start_half_seconds_remaining": "Half clock at the start of the first scrimmage play",
    "start_score_diff": "Offense score minus defense score at the start of the first scrimmage play",
    "start_posteam_timeouts": "Offense timeouts remaining at the start of the first scrimmage play",
    "start_defteam_timeouts": "Defense timeouts remaining at the start of the first scrimmage play",
    "outcome": "Exactly one of TD, FG_MADE, FG_MISSED, PUNT, TURNOVER, DOWNS, SAFETY, END_OF_HALF, OTHER",
    "posteam_points": "Points scored by the offense on this drive, including the extra point",
    "defteam_points": "Points scored by the defense on this drive, including a return score and its extra point",
    "plays": "Non-timeout plays on the drive, including penalties, punts, field goals, and extra points",
    "seconds_elapsed": "Drive time of possession in seconds when nflverse publishes it, else the game-clock delta",
    "yards": "Yards gained on pass, rush, kneel, and penalty plays. Punt and kick return yards are excluded",
    "pass_plays": "Pass plays, including spikes and sacks",
    "rush_plays": "Rush plays, including kneels",
    "drive_first_passer_id": "In-game first passer on the drive, else the kneel or rush rusher. Not a pre-game chart id",
}

SCRIMMAGE_TYPES = {"pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike"}
HALF_RESULTS = {"End of half", "End of game"}


@dataclass
class DriveTables:
    """Drives, non-offensive points, the games that were loaded, and cutoff drops."""

    drives: pd.DataFrame
    non_offensive: pd.DataFrame
    games: pd.DataFrame
    dropped: pd.DataFrame


def _col(frame: pd.DataFrame, name: str, default: object = 0) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index)
    return frame[name]


def _flag(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).eq(1)


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        return str(value)
    return str(value)


def assert_outcomes(outcomes: pd.Series) -> None:
    """Raise unless every outcome is one label from the closed set."""
    if outcomes.isna().any():
        raise PregameError("drive outcome is missing")
    bad = sorted({str(value) for value in outcomes if str(value) not in OUTCOMES})
    if bad:
        raise PregameError(f"drive outcome is outside the set: {bad}")


def _order_key(frame: pd.DataFrame) -> pd.Series:
    qtr = pd.to_numeric(_col(frame, "qtr"), errors="coerce").fillna(0)
    remaining = pd.to_numeric(_col(frame, "quarter_seconds_remaining"), errors="coerce")
    if remaining.notna().any():
        return qtr * 100_000 - remaining.fillna(0)
    return pd.to_numeric(_col(frame, "play_id"), errors="coerce").fillna(0)


def _is_kickoff(frame: pd.DataFrame) -> pd.Series:
    play_type = _col(frame, "play_type", "").astype(str)
    return play_type.eq("kickoff") | _flag(_col(frame, "kickoff_attempt"))


def _is_extra_point(frame: pd.DataFrame) -> pd.Series:
    play_type = _col(frame, "play_type", "").astype(str)
    return (
        play_type.eq("extra_point")
        | _flag(_col(frame, "extra_point_attempt"))
        | _flag(_col(frame, "two_point_attempt"))
    )


def _is_timeout(frame: pd.DataFrame) -> pd.Series:
    play_type = _col(frame, "play_type", "").astype(str)
    return _flag(_col(frame, "timeout")) | play_type.eq("timeout")


def _is_admin(frame: pd.DataFrame) -> pd.Series:
    """Timeouts and null play-type markers. Their posted score is often stale."""
    play_type = _col(frame, "play_type", pd.NA)
    missing_type = play_type.isna() | play_type.astype(str).isin(["", "None", "nan", "NaN"])
    return _is_timeout(frame) | missing_type


def _is_scrimmage(frame: pd.DataFrame) -> pd.Series:
    play_type = _col(frame, "play_type", "").astype(str)
    return play_type.isin(SCRIMMAGE_TYPES) & ~_is_extra_point(frame) & ~_is_kickoff(frame) & ~_is_timeout(frame)


def _is_safety(frame: pd.DataFrame) -> pd.Series:
    return _flag(_col(frame, "safety")) & ~_is_kickoff(frame)


def _lost_fumble(play: pd.Series, offense: str) -> bool:
    if not _flag(pd.Series([play.get("fumble_lost")])).iloc[0]:
        return False
    fumbler = _text(play.get("fumbled_1_team"))
    if fumbler == "":
        return _text(play.get("posteam")) == offense
    return fumbler == offense


def _clock_sort(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.copy()
    ordered["_ord"] = _order_key(ordered)
    play_id = pd.to_numeric(_col(ordered, "play_id"), errors="coerce").fillna(0)
    ordered["_play_id"] = play_id
    return ordered.sort_values(["_ord", "_play_id"], kind="mergesort")


def classify_drive(plays: pd.DataFrame, *, half_ended: bool = False) -> str:
    """Classify one drive. Extra points, kickoffs, and timeouts are not the terminal play."""
    if plays is None or len(plays) == 0:
        return "OTHER"
    ordered = _clock_sort(plays)
    scrimmage = ordered.loc[_is_scrimmage(ordered)]
    safeties = ordered.loc[_is_safety(ordered)]
    if len(scrimmage) == 0:
        if len(safeties):
            return "SAFETY"
        if half_ended:
            return "END_OF_HALF"
        return "OTHER"
    offense = ""
    for value in scrimmage["posteam"] if "posteam" in scrimmage.columns else []:
        offense = _text(value)
        if offense:
            break
    if len(safeties):
        return "SAFETY"
    terminal = scrimmage.iloc[-1]
    return _classify_terminal(terminal, offense=offense, half_ended=half_ended)


def _classify_terminal(play: pd.Series, *, offense: str, half_ended: bool) -> str:
    play_type = _text(play.get("play_type"))
    touchdown = bool(_flag(pd.Series([play.get("touchdown")])).iloc[0])
    td_team = _text(play.get("td_team"))
    if bool(_flag(pd.Series([play.get("safety")])).iloc[0]):
        return "SAFETY"
    if play_type in {"pass", "run"} and touchdown and td_team == offense and offense != "":
        return "TD"
    if play_type == "field_goal" or bool(_flag(pd.Series([play.get("field_goal_attempt")])).iloc[0]):
        result = _text(play.get("field_goal_result")).lower()
        if result == "made":
            return "FG_MADE"
        return "FG_MISSED"
    if play_type == "punt" or bool(_flag(pd.Series([play.get("punt_attempt")])).iloc[0]):
        return "PUNT"
    if bool(_flag(pd.Series([play.get("interception")])).iloc[0]) or _lost_fumble(play, offense):
        return "TURNOVER"
    if touchdown and td_team not in ("", offense):
        return "TURNOVER"
    if play_type == "qb_kneel" or bool(_flag(pd.Series([play.get("qb_kneel")])).iloc[0]):
        return "END_OF_HALF"
    down = pd.to_numeric(pd.Series([play.get("down")]), errors="coerce").iloc[0]
    first_down = bool(_flag(pd.Series([play.get("first_down")])).iloc[0])
    if (
        pd.notna(down)
        and int(down) == 4
        and play_type in {"pass", "run"}
        and not first_down
        and not touchdown
    ):
        return "DOWNS"
    if half_ended:
        return "END_OF_HALF"
    return "OTHER"


def _repair_scores(
    raw: list[float | None],
    admin: list[bool],
    no_play: list[bool],
    drive_nums: list[int | None],
) -> list[float]:
    """Carry a score forward through stale administrative rows.

    A timeout or a null play-type marker does not change the score. A
    ``no_play`` row may add points (a penalty safety) but may not subtract
    them. A real play filed after a later drive, with a lower total, is the
    same kind of stale row. A dip that a later row (including the end-of-game
    marker) brings back is mis-ordered. A dip that never comes back is a
    reversed score and is kept.
    """
    current = 0.0
    max_drive: int | None = None
    repaired: list[float] = []
    for index, value in enumerate(raw):
        drive = drive_nums[index]
        if drive is not None:
            max_drive = drive if max_drive is None else max(max_drive, drive)
        out_of_order = drive is not None and max_drive is not None and drive < max_drive
        if value is None or admin[index]:
            repaired.append(current)
            continue
        if out_of_order and value + 1e-9 < current:
            repaired.append(current)
            continue
        if value + 1e-9 >= current:
            current = value
            repaired.append(current)
            continue
        if no_play[index]:
            repaired.append(current)
            continue
        recovers = any(
            later is not None and later + 1e-9 >= current
            for later in raw[index + 1 :]
        )
        if recovers:
            repaired.append(current)
        else:
            current = value
            repaired.append(current)
    return repaired


def _add_score_points(frame: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for _, sub in frame.groupby("game_id", sort=False):
        block = sub.copy()
        admin = _is_admin(block).tolist()
        play_type = _col(block, "play_type", "").astype(str)
        no_play = (play_type.eq("no_play") & ~_is_timeout(block)).tolist()
        drive_nums = [
            None if pd.isna(value) else int(value)
            for value in pd.to_numeric(_col(block, "drive_num"), errors="coerce")
        ]
        for source, dest in (("total_home_score", "home_points_on_play"), ("total_away_score", "away_points_on_play")):
            numeric = pd.to_numeric(_col(block, source), errors="coerce")
            raw: list[float | None] = [None if pd.isna(value) else float(value) for value in numeric]
            repaired = _repair_scores(raw, admin, no_play, drive_nums)
            delta = pd.Series(repaired, index=block.index).diff()
            delta.iloc[0] = repaired[0] if repaired else 0.0
            block[dest] = delta.fillna(0).round().astype(int)
        pieces.append(block)
    if not pieces:
        out = frame.copy()
        out["home_points_on_play"] = pd.Series(dtype="int64")
        out["away_points_on_play"] = pd.Series(dtype="int64")
        return out
    return pd.concat(pieces, ignore_index=True)


def _parse_top(value: object) -> int | None:
    text = _text(value)
    if ":" not in text:
        return None
    minutes, seconds = text.split(":", 1)
    try:
        return int(minutes) * 60 + int(float(seconds))
    except ValueError:
        return None


def _first_text(values: pd.Series) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _starting_qb(scrimmage: pd.DataFrame) -> str | None:
    """First passer on the drive, otherwise the rusher on a kneel or rush."""
    if len(scrimmage) == 0:
        return None
    if "passer_player_id" in scrimmage.columns:
        passing = scrimmage.loc[
            _flag(_col(scrimmage, "pass")) | scrimmage["play_type"].astype(str).eq("pass")
        ]
        found = _first_text(passing["passer_player_id"]) if len(passing) else None
        if found:
            return found
    if "rusher_player_id" not in scrimmage.columns:
        return None
    rushing = scrimmage.loc[
        _flag(_col(scrimmage, "rush"))
        | _flag(_col(scrimmage, "qb_kneel"))
        | scrimmage["play_type"].astype(str).isin(["run", "qb_kneel"])
    ]
    source = rushing if len(rushing) else scrimmage
    return _first_text(source["rusher_player_id"])


def _blank_drives() -> pd.DataFrame:
    return pd.DataFrame(columns=list(DRIVE_COLUMNS))


def _blank_non_offensive() -> pd.DataFrame:
    return pd.DataFrame(columns=["game_id", "home_points", "away_points"])


def build_drive_table(pbp: pd.DataFrame, schedule: pd.DataFrame) -> DriveTables:
    """Build the drive table for games that kick off before the Eastern cutoff.

    Market columns on either input are dropped before the table is built.
    Games on or after 2027-01-01 Eastern are returned in ``dropped`` and their
    plays are not rows.
    """
    pbp, _ = strip_market_columns(pbp)
    schedule, _ = strip_market_columns(schedule)
    split = prepare_schedule(schedule)
    games = split.games
    if len(pbp) == 0 or len(games) == 0:
        drives = _blank_drives()
        assert_no_market_columns(drives, "drives")
        assert_no_market_columns(split.dropped, "dropped")
        return DriveTables(drives, _blank_non_offensive(), games, split.dropped)

    plays = pbp.copy()
    plays["game_id"] = plays["game_id"].astype(str)
    allowed = set(games["game_id"].astype(str))
    plays = plays[plays["game_id"].isin(allowed)].copy()
    if len(plays) == 0:
        drives = _blank_drives()
        assert_no_market_columns(drives, "drives")
        return DriveTables(drives, _blank_non_offensive(), games.iloc[0:0].copy(), split.dropped)

    plays["play_id"] = pd.to_numeric(_col(plays, "play_id"), errors="coerce").fillna(0)
    if "fixed_drive" in plays.columns:
        drive_source = plays["fixed_drive"]
    elif "drive" in plays.columns:
        drive_source = plays["drive"]
    else:
        drive_source = pd.Series(pd.NA, index=plays.index)
    plays["drive_num"] = pd.to_numeric(drive_source, errors="coerce")
    plays = plays.sort_values(["game_id", "play_id"], kind="mergesort").reset_index(drop=True)
    plays = _add_score_points(plays)
    plays["is_kickoff"] = _is_kickoff(plays)
    plays["is_xp"] = _is_extra_point(plays)
    plays["is_timeout"] = _is_timeout(plays)
    plays["is_scrimmage"] = _is_scrimmage(plays)
    plays["is_safety"] = _is_safety(plays)
    plays["_ord"] = _order_key(plays)

    keyed = plays[plays["drive_num"].notna()].copy()
    keyed["drive_num"] = keyed["drive_num"].astype(int)
    def _keys(mask: pd.Series) -> set[tuple[str, int]]:
        picked = keyed.loc[mask, ["game_id", "drive_num"]]
        return set(zip(picked["game_id"], picked["drive_num"]))

    posteam_text = keyed["posteam"].map(_text) if "posteam" in keyed.columns else pd.Series("", index=keyed.index)
    kick_score = keyed["is_kickoff"] & _flag(_col(keyed, "touchdown"))
    kick_score_drives = _keys(kick_score)
    is_penalty = (
        keyed["play_type"].astype(str).eq("no_play")
        & ~keyed["is_timeout"]
        & ~keyed["is_kickoff"]
        & posteam_text.ne("")
    )
    penalty_keys = {
        pair for pair in _keys(is_penalty) if pair not in kick_score_drives
    }
    offensive_keys = _keys(keyed["is_scrimmage"]) | _keys(keyed["is_safety"]) | penalty_keys
    pairs = list(zip(keyed["game_id"], keyed["drive_num"]))
    kicks = keyed["is_kickoff"].tolist()
    keyed["offensive_drive"] = [
        pair in offensive_keys and not is_kick for pair, is_kick in zip(pairs, kicks)
    ]

    game_meta = games.set_index("game_id")
    rows: list[dict[str, object]] = []
    offensive = keyed[keyed["offensive_drive"]]
    for (game_id, drive_num), sub in offensive.groupby(["game_id", "drive_num"], sort=False):
        ordered = sub.sort_values(["_ord", "play_id"], kind="mergesort")
        body = ordered.loc[~ordered["is_kickoff"]]
        scrimmage = body.loc[body["is_scrimmage"]]
        has_penalty = bool(
            (body["play_type"].astype(str).eq("no_play") & ~body["is_timeout"]).any()
        )
        if len(scrimmage) == 0 and not bool(body["is_safety"].any()) and not has_penalty:
            continue
        if len(scrimmage):
            start = scrimmage.iloc[0]
        elif bool(body["is_safety"].any()):
            start = body.loc[body["is_safety"]].iloc[0]
        else:
            start = body.iloc[0]
        offense = _text(start.get("posteam"))
        defense = _text(start.get("defteam"))
        if not offense:
            continue
        result = ""
        if "fixed_drive_result" in ordered.columns and ordered["fixed_drive_result"].notna().any():
            result = _text(ordered["fixed_drive_result"].dropna().iloc[0])
        outcome = classify_drive(body, half_ended=result in HALF_RESULTS)
        meta = game_meta.loc[game_id]
        home = _text(meta["home_team"] if "home_team" in meta.index else start.get("home_team"))
        away = _text(meta["away_team"] if "away_team" in meta.index else start.get("away_team"))
        if home == "" and "home_team" in start.index:
            home = _text(start.get("home_team"))
        home_points = int(body["home_points_on_play"].sum())
        away_points = int(body["away_points_on_play"].sum())
        if same_team(offense, home):
            posteam_points, defteam_points = home_points, away_points
            is_home: bool | None = True
        elif same_team(offense, away):
            posteam_points, defteam_points = away_points, home_points
            is_home = False
        else:
            posteam_points, defteam_points = home_points, away_points
            is_home = None
        counted = body.loc[~body["is_timeout"] & body["play_type"].notna()]
        pass_flag = _flag(_col(counted, "pass")) | counted["play_type"].astype(str).eq("pass")
        rush_flag = (
            _flag(_col(counted, "rush"))
            | _flag(_col(counted, "qb_kneel"))
            | counted["play_type"].astype(str).isin(["run", "qb_kneel"])
        )
        pass_flag = pass_flag & ~counted["is_xp"]
        rush_flag = rush_flag & ~counted["is_xp"]
        yard_rows = counted.loc[pass_flag | rush_flag | counted["play_type"].astype(str).eq("no_play")]
        yards = pd.to_numeric(_col(yard_rows, "yards_gained"), errors="coerce").fillna(0).sum()
        passer = _starting_qb(scrimmage)
        top_seconds = None
        if "drive_time_of_possession" in ordered.columns:
            for value in ordered["drive_time_of_possession"]:
                top_seconds = _parse_top(value)
                if top_seconds is not None:
                    break
        if top_seconds is None and len(scrimmage):
            start_clock = pd.to_numeric(pd.Series([start.get("game_seconds_remaining")]), errors="coerce").iloc[0]
            end_clock = pd.to_numeric(pd.Series([scrimmage.iloc[-1].get("game_seconds_remaining")]), errors="coerce").iloc[0]
            if pd.notna(start_clock) and pd.notna(end_clock) and end_clock <= start_clock:
                top_seconds = int(start_clock - end_clock)
        score_diff = pd.to_numeric(pd.Series([start.get("score_differential")]), errors="coerce").iloc[0]
        if pd.isna(score_diff):
            off_score = pd.to_numeric(pd.Series([start.get("posteam_score")]), errors="coerce").iloc[0]
            def_score = pd.to_numeric(pd.Series([start.get("defteam_score")]), errors="coerce").iloc[0]
            if pd.notna(off_score) and pd.notna(def_score):
                score_diff = off_score - def_score
        rows.append({
            "game_id": game_id,
            "season": int(meta["season"]) if pd.notna(meta["season"]) else pd.NA,
            "week": int(meta["week"]) if pd.notna(meta["week"]) else pd.NA,
            "game_type": _text(meta["game_type"]),
            "kickoff_et": meta["kickoff_et"],
            "posteam": offense,
            "defteam": defense,
            "posteam_is_home": is_home,
            "drive": int(drive_num),
            "start_yardline_100": _number(start.get("yardline_100")),
            "start_qtr": _number(start.get("qtr")),
            "start_game_seconds_remaining": _number(start.get("game_seconds_remaining")),
            "start_half_seconds_remaining": _number(start.get("half_seconds_remaining")),
            "start_score_diff": None if pd.isna(score_diff) else int(score_diff),
            "start_posteam_timeouts": _number(start.get("posteam_timeouts_remaining")),
            "start_defteam_timeouts": _number(start.get("defteam_timeouts_remaining")),
            "outcome": outcome,
            "posteam_points": int(posteam_points),
            "defteam_points": int(defteam_points),
            "plays": len(counted),
            "seconds_elapsed": top_seconds,
            "yards": round(float(yards)),
            "pass_plays": int(pass_flag.sum()),
            "rush_plays": int(rush_flag.sum()),
            "drive_first_passer_id": passer,
        })

    drives = pd.DataFrame(rows, columns=list(DRIVE_COLUMNS))
    if len(drives):
        drives["kickoff_et"] = pd.to_datetime(drives["kickoff_et"], utc=True).dt.tz_convert("America/New_York")
        drives = drives.sort_values(["kickoff_et", "game_id", "drive"], kind="mergesort").reset_index(drop=True)
    assert_outcomes(drives["outcome"]) if len(drives) else None
    assert_output_kickoffs(drives["kickoff_et"]) if len(drives) else None

    non_off = keyed.loc[~keyed["offensive_drive"], ["game_id", "home_points_on_play", "away_points_on_play"]]
    if len(non_off):
        grouped = non_off.groupby("game_id", sort=False)[["home_points_on_play", "away_points_on_play"]].sum()
        non_offensive = grouped.reset_index().rename(columns={
            "home_points_on_play": "home_points",
            "away_points_on_play": "away_points",
        })
        non_offensive["home_points"] = non_offensive["home_points"].astype(int)
        non_offensive["away_points"] = non_offensive["away_points"].astype(int)
    else:
        non_offensive = _blank_non_offensive()

    used = games[games["game_id"].isin(set(plays["game_id"]))].copy()
    for frame, label in (
        (drives, "drives"),
        (non_offensive, "non_offensive"),
        (used, "games"),
        (split.dropped, "dropped"),
    ):
        assert_no_market_columns(frame, label)
    return DriveTables(drives, non_offensive, used, split.dropped)


def _number(value: object) -> int | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return int(numeric)
