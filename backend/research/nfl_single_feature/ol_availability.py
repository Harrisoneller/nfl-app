"""Snap-weighted offensive-line starter availability.

One feature, home minus away:

    index = (snap-weighted share of usual starters who are Out or Doubtful)
            + 1.0 * (usual center is Out or Doubtful)

The expected stage-2 sign on home margin is negative. Questionable is
available. Status comes only from a report timestamped before kickoff.
Who played the game is not a status.

This module does not fetch injuries or snap counts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calendar import assert_pregame
from .design import DesignError
from .specs import (
    OL_CENTER_POSITIONS,
    OL_CENTER_WEIGHT,
    OL_MAX_STARTERS,
    OL_MISSING_STATUS,
    OL_POSITIONS,
    OL_STARTER_GAMES,
    OL_STARTER_SHARE,
    REGIME_LAST,
)


def _norm_status(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def _is_missing_status(value: object) -> bool:
    return _norm_status(value) in OL_MISSING_STATUS


def _naive_timestamp(value: object) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT
    try:
        if pd.isna(value):
            return pd.NaT
    except (TypeError, ValueError):
        pass
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        raise DesignError("report and kickoff timestamps must be timezone-naive")
    return stamp


def _prior_window(snaps: pd.DataFrame, team: str, season: int, week: int) -> pd.DataFrame:
    hist = snaps[
        (snaps["team"] == team)
        & (snaps["season"].astype(int) > REGIME_LAST)
        & (
            (snaps["season"].astype(int) < season)
            | ((snaps["season"].astype(int) == season) & (snaps["week"].astype(int) < week))
        )
    ].copy()
    if len(hist) == 0:
        return hist
    hist = hist[np.isfinite(hist["offense_snaps"].astype(float))]
    games = (
        hist.groupby(["season", "week", "game_id"], as_index=False)["team_offense_snaps"].first()
    )
    games = games[np.isfinite(games["team_offense_snaps"].astype(float))]
    games = games[games["team_offense_snaps"].astype(float) > 0]
    games = games.sort_values(["season", "week", "game_id"]).tail(OL_STARTER_GAMES)
    if len(games) == 0:
        return hist.iloc[0:0]
    return hist.merge(games[["season", "week", "game_id"]], on=["season", "week", "game_id"], how="inner")


def _starters(window: pd.DataFrame) -> pd.DataFrame:
    if len(window) == 0:
        return pd.DataFrame(columns=["player_id", "position", "offense_snaps", "share"])
    team_snaps = float(
        window.groupby(["season", "week", "game_id"])["team_offense_snaps"].first().sum()
    )
    if team_snaps <= 0:
        return pd.DataFrame(columns=["player_id", "position", "offense_snaps", "share"])
    ordered = window.sort_values(["season", "week", "game_id"])
    position = ordered.groupby("player_id")["position"].last()
    player_snaps = ordered.groupby("player_id")["offense_snaps"].sum()
    table = pd.DataFrame({
        "player_id": player_snaps.index,
        "offense_snaps": player_snaps.to_numpy(dtype=float),
        "position": position.reindex(player_snaps.index).astype(str).str.strip().str.upper().to_numpy(),
    })
    table["share"] = table["offense_snaps"] / team_snaps
    starters = table[
        table["position"].isin(OL_POSITIONS) & (table["share"] >= OL_STARTER_SHARE)
    ]
    starters = starters.sort_values(["share", "player_id"], ascending=[False, True])
    return starters.head(OL_MAX_STARTERS).reset_index(drop=True)


def _final_status(
    injuries: pd.DataFrame,
    team: str,
    season: int,
    week: int,
    player_id: str,
    kickoff: pd.Timestamp,
) -> tuple[object | None, bool]:
    """Return ``(status, verified)``.

    No rows means the player is not on the report: status None, verified True.
    Rows that are missing a time, or are not strictly before kickoff, do not
    count. If those are the only rows, the report is unverified.
    """
    rows = injuries[
        (injuries["team"] == team)
        & (injuries["season"].astype(int) == season)
        & (injuries["week"].astype(int) == week)
        & (injuries["player_id"] == player_id)
    ]
    if len(rows) == 0:
        return None, True
    if pd.isna(kickoff):
        return None, False
    times = [_naive_timestamp(value) for value in rows["report_time"]]
    verified_at = [stamp for stamp in times if pd.notna(stamp) and stamp < kickoff]
    if not verified_at:
        return None, False
    latest = max(verified_at)
    picked = rows.iloc[times.index(latest)]
    return picked["report_status"], True


def _side_index(
    snaps: pd.DataFrame,
    injuries: pd.DataFrame,
    team: str,
    season: int,
    week: int,
    kickoff: pd.Timestamp,
) -> tuple[float, str]:
    starters = _starters(_prior_window(snaps, team, season, week))
    if len(starters) == 0:
        return np.nan, "no_starters"
    missing_flags: list[bool] = []
    for player_id in starters["player_id"]:
        status, verified = _final_status(injuries, team, season, week, player_id, kickoff)
        if not verified:
            return np.nan, "unverified_report"
        missing_flags.append(_is_missing_status(status) if status is not None else False)
    starters = starters.copy()
    starters["missing"] = missing_flags
    weight = starters["offense_snaps"].to_numpy(dtype=float)
    share = float(weight[starters["missing"].to_numpy()].sum() / weight.sum())
    centers = starters[starters["position"].isin(OL_CENTER_POSITIONS)]
    center_out = bool(len(centers) and bool(centers.iloc[0]["missing"]))
    index = share + OL_CENTER_WEIGHT * float(center_out)
    return index, "ok"


def _qb_changes(games: pd.DataFrame) -> pd.DataFrame:
    home = games[["season", "week", "game_id", "home", "home_qb"]].rename(
        columns={"home": "team", "home_qb": "qb"},
    )
    away = games[["season", "week", "game_id", "away", "away_qb"]].rename(
        columns={"away": "team", "away_qb": "qb"},
    )
    both = pd.concat([home, away], ignore_index=True)
    both = both.sort_values(["team", "season", "week", "game_id"])
    both["prev_qb"] = both.groupby("team")["qb"].shift(1)
    changed = both["prev_qb"].notna() & (both["qb"] != both["prev_qb"])
    both["qb_change"] = changed
    flagged = both.groupby("game_id")["qb_change"].any()
    return flagged


def ol_matchup_feature(
    snaps: pd.DataFrame,
    injuries: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Home index minus away index. Exclusions are NaN, not a filled zero.

    Snaps from the current game never enter the starter set. A player listed
    Out who later plays is still Out. A player listed Questionable who does
    not play is still available.
    """
    snap_need = {"season", "week", "game_id", "team", "player_id", "position", "offense_snaps", "team_offense_snaps"}
    inj_need = {"season", "week", "team", "player_id", "report_status", "report_time"}
    game_need = {
        "game_id", "season", "week", "kickoff", "home", "away",
        "home_qb", "away_qb", "home_qb_asof", "away_qb_asof",
    }
    for label, frame, need in (
        ("snaps", snaps, snap_need),
        ("injuries", injuries, inj_need),
        ("games", games, game_need),
    ):
        missing = need - set(frame.columns)
        if missing:
            raise DesignError(f"{label} missing columns: {sorted(missing)}")
    snaps = snaps[snaps["season"].astype(int) > REGIME_LAST].copy()
    injuries = injuries[injuries["season"].astype(int) > REGIME_LAST].copy()
    for game in games.itertuples(index=False):
        assert_pregame(game.home_qb_asof, game.kickoff, "home_qb")
        assert_pregame(game.away_qb_asof, game.kickoff, "away_qb")
    qb_change = _qb_changes(games)
    rows: list[dict[str, object]] = []
    for game in games.itertuples(index=False):
        if bool(qb_change.get(game.game_id, False)):
            rows.append({
                "game_id": game.game_id,
                "season": int(game.season),
                "week": int(game.week),
                "ol_d": np.nan,
                "ol_exclude_reason": "qb_change",
            })
            continue
        kickoff = _naive_timestamp(game.kickoff)
        home_index, home_reason = _side_index(
            snaps, injuries, game.home, int(game.season), int(game.week), kickoff,
        )
        away_index, away_reason = _side_index(
            snaps, injuries, game.away, int(game.season), int(game.week), kickoff,
        )
        if home_reason != "ok" or away_reason != "ok":
            reason = home_reason if home_reason != "ok" else away_reason
            value = np.nan
        else:
            reason = "ok"
            value = float(home_index - away_index)
        rows.append({
            "game_id": game.game_id,
            "season": int(game.season),
            "week": int(game.week),
            "ol_d": value,
            "ol_exclude_reason": reason,
        })
    return pd.DataFrame(rows)
