"""Pre-game row filter and depth-chart quarterback identity.

History is every row whose kickoff is strictly earlier in America/New_York.
The target game shares its own kickoff, so it is excluded, and so is any
game that kicks off at the same time or later. Ordering is by the kickoff,
not by game id or week number.

Weekly depth charts have no publication timestamp. The chart's QB1 for that
season, week, team, and game type is treated as known one hour before kickoff.
That is the same assumption the single-feature study used. It is not an
observed report time, and it is not the passer who played.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .calendar import EASTERN, PregameError, assert_pregame, eastern_kickoff
from .teams import canon

QB_ASOF_LEAD = timedelta(hours=1)

# Columns a pre-game helper may return. The in-game first passer is not one of them.
PREGAME_COLUMNS = (
    "game_id",
    "side",
    "team",
    "qb_id",
    "qb_asof",
)


def _aware_eastern(value: object, label: str) -> datetime:
    """Convert an aware timestamp to Eastern. A naive value raises."""
    if value is None:
        raise PregameError(f"{label} needs an aware timestamp")
    try:
        if pd.isna(value):
            raise PregameError(f"{label} needs an aware timestamp")
    except TypeError:
        pass
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise PregameError(f"{label} needs an aware timestamp")
    if stamp.tzinfo is None:
        raise PregameError(f"{label} is naive; pre-game inputs need an aware timestamp")
    return stamp.tz_convert(EASTERN).to_pydatetime()


def rows_strictly_before(
    frame: pd.DataFrame,
    kickoff: object,
    *,
    kickoff_column: str = "kickoff_et",
) -> pd.DataFrame:
    """Rows whose kickoff is strictly before ``kickoff``.

    Both sides are compared in America/New_York. A naive timestamp raises
    instead of being read as UTC or as a wall clock. An aware timestamp in
    another zone is converted first, so an earlier clock face in that zone
    cannot pass when the Eastern instant is not earlier.
    """
    if kickoff_column not in frame.columns:
        raise PregameError(f"missing {kickoff_column}")
    target = _aware_eastern(kickoff, "target kickoff")
    stamps = []
    for value in frame[kickoff_column]:
        stamps.append(_aware_eastern(value, kickoff_column))
    started = pd.Series(stamps, index=frame.index)
    keep = started.map(lambda stamp: stamp < target)
    return frame.loc[keep].copy()


def _depth_team_is_starter(value: object) -> bool:
    text = str(value).strip()
    return text in {"1", "1.0"}


def pregame_quarterback(
    depth_charts: pd.DataFrame,
    *,
    season: int,
    week: int,
    team: str,
    game_type: str,
    kickoff: object,
) -> tuple[str | None, datetime | None]:
    """Depth-chart QB1 and the as-of time, or ``(None, None)``.

    A weekly chart has no publication time. Its as-of stamp is the Eastern
    kickoff minus one hour. A 2025+ snapshot chart has ``dt``; the as-of stamp
    is the latest snapshot strictly before kickoff. ``assert_pregame`` checks
    either stamp. The passer from the target game is not an input.
    """
    kick = eastern_kickoff(kickoff)
    if kick is None:
        raise PregameError("qb needs a pre-kickoff timestamp and a kickoff")
    if depth_charts is None or len(depth_charts) == 0:
        return None, None
    # Week rows and dt-only snapshot rows are separate paths, including when
    # one concatenated frame contains both. A week match wins, matching the table.
    qb_id = _lookup_indexed(
        _index_quarterbacks(depth_charts),
        season=int(season),
        week=int(week),
        team=str(team),
        game_type=str(game_type),
    )
    if qb_id is not None:
        asof = kick - QB_ASOF_LEAD
        assert_pregame(asof, kick, "qb")
        return qb_id, asof
    return _lookup_snapshot(_index_snapshots(depth_charts), str(team), kick)


def pregame_quarterback_table(
    depth_charts: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Pre-game QB ids for every team in ``games``.

    Same rule as ``pregame_quarterback``. A week row uses kickoff minus one
    hour. A dt-only snapshot row uses the latest ``dt`` strictly before
    kickoff. A team-game with neither has a null id. The in-game passer is
    not read. Returned columns are ``PREGAME_COLUMNS``.
    """
    rows = []
    if games is None or len(games) == 0:
        return pd.DataFrame(columns=list(PREGAME_COLUMNS))
    indexed = _index_quarterbacks(depth_charts)
    snapshots = _index_snapshots(depth_charts)
    for game in games.itertuples(index=False):
        kick = eastern_kickoff(game.kickoff_et)
        for side, team in (("home", game.home_team), ("away", game.away_team)):
            qb_id = _lookup_indexed(
                indexed,
                season=int(game.season),
                week=int(game.week),
                team=str(team),
                game_type=str(game.game_type),
            )
            asof = None
            if qb_id is not None and kick is not None:
                asof = kick - QB_ASOF_LEAD
                assert_pregame(asof, kick, "qb")
            elif kick is not None:
                qb_id, asof = _lookup_snapshot(snapshots, str(team), kick)
            rows.append({
                "game_id": game.game_id, "side": side, "team": team,
                "qb_id": qb_id, "qb_asof": asof,
            })
    frame = pd.DataFrame(rows, columns=list(PREGAME_COLUMNS))
    extra = [column for column in frame.columns if column not in PREGAME_COLUMNS]
    if extra:
        raise PregameError(f"pre-game frame has columns outside the allowlist: {extra}")
    return frame


def _with_team(charts: pd.DataFrame) -> pd.DataFrame:
    out = charts.copy()
    if "club_code" in out.columns:
        if "team" not in out.columns:
            out["team"] = out["club_code"]
        else:
            out["team"] = out["team"].fillna(out["club_code"])
    return out


def _index_quarterbacks(depth_charts: pd.DataFrame) -> dict[tuple, str]:
    """Weekly depth-chart QB1, keyed by canonical team.

    Rows from the 2025+ snapshot file have no week and are not indexed here.
    """
    if depth_charts is None or len(depth_charts) == 0 or "week" not in depth_charts.columns:
        return {}
    charts = _with_team(depth_charts)
    if "position" not in charts.columns or "team" not in charts.columns:
        return {}
    charts = charts[charts["gsis_id"].notna() & charts["week"].notna()]
    charts = charts[charts["position"].astype(str).str.upper().eq("QB")]
    if "depth_team" in charts.columns:
        charts = charts[charts["depth_team"].map(_depth_team_is_starter)]
    charts = charts.copy()
    charts["season"] = pd.to_numeric(charts["season"], errors="coerce")
    charts["week"] = pd.to_numeric(charts["week"], errors="coerce")
    charts = charts.sort_values("gsis_id")
    index: dict[tuple, str] = {}
    has_type = "game_type" in charts.columns
    for row in charts.itertuples(index=False):
        if pd.isna(row.season) or pd.isna(row.week) or canon(row.team) == "":
            continue
        team_key = (int(row.season), int(row.week), canon(row.team))
        index.setdefault(team_key, str(row.gsis_id))
        if has_type and pd.notna(row.game_type):
            typed = team_key + (str(row.game_type).upper(),)
            index.setdefault(typed, str(row.gsis_id))
    return index


def _lookup_indexed(
    index: dict[tuple, str],
    *,
    season: int,
    week: int,
    team: str,
    game_type: str,
) -> str | None:
    side = canon(team)
    typed = (season, week, side, str(game_type).upper())
    if typed in index:
        return index[typed]
    return index.get((season, week, side))


def _index_snapshots(depth_charts: pd.DataFrame) -> dict[str, list[tuple[datetime, str]]]:
    """Latest-before-kickoff QB1 from the timestamped 2025+ depth chart.

    Each ``dt`` is a full chart. A team-snapshot has one rank-1 quarterback.
    """
    if (
        depth_charts is None
        or len(depth_charts) == 0
        or "dt" not in depth_charts.columns
        or "pos_abb" not in depth_charts.columns
    ):
        return {}
    charts = _with_team(depth_charts)
    charts = charts[charts["pos_abb"].astype(str).str.upper().eq("QB")]
    if "pos_rank" in charts.columns:
        charts = charts[pd.to_numeric(charts["pos_rank"], errors="coerce").eq(1)]
    charts = charts[charts["gsis_id"].notna() & charts["dt"].notna() & charts["team"].notna()]
    charts = charts.sort_values("gsis_id").drop_duplicates(["dt", "team"])
    index: dict[str, list[tuple[datetime, str]]] = {}
    for row in charts.itertuples(index=False):
        stamp = eastern_kickoff(row.dt)
        side = canon(row.team)
        if stamp is None or side == "":
            continue
        index.setdefault(side, []).append((stamp, str(row.gsis_id)))
    for snaps in index.values():
        snaps.sort()
    return index


def _lookup_snapshot(
    index: dict[str, list[tuple[datetime, str]]],
    team: str,
    kickoff: datetime,
) -> tuple[str | None, datetime | None]:
    snaps = index.get(canon(team), [])
    chosen: tuple[str, datetime] | None = None
    for stamp, qb_id in snaps:
        if stamp < kickoff:
            chosen = (qb_id, stamp)
        else:
            break
    if chosen is None:
        return None, None
    assert_pregame(chosen[1], kickoff, "qb")
    return chosen
