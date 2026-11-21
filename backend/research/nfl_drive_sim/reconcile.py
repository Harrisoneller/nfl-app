"""Game score check. Mismatches are listed; the drives are kept."""

from __future__ import annotations

import pandas as pd

MISMATCH_COLUMNS = (
    "game_id",
    "season",
    "week",
    "game_type",
    "kickoff_et",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_points",
    "away_points",
    "home_diff",
    "away_diff",
    "reason",
)


def reconcile(
    drives: pd.DataFrame,
    non_offensive: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Compare built points with the schedule's final score for every loaded game.

    Built points are drive points plus non-offensive scores (kickoff returns
    and any other play that is not on an offensive drive). A game that does
    not match is a row in the result. It is not removed from the drive table.
    """
    if len(games) == 0:
        return pd.DataFrame(columns=list(MISMATCH_COLUMNS))
    home = pd.Series(0, index=games["game_id"].astype(str))
    away = pd.Series(0, index=games["game_id"].astype(str))
    if len(drives):
        offense_home = drives["posteam_is_home"].eq(True)
        unknown = drives["posteam_is_home"].isna()
        drive_home = pd.Series(0, index=drives.index, dtype="int64")
        drive_away = pd.Series(0, index=drives.index, dtype="int64")
        known = ~unknown
        drive_home.loc[known & offense_home] = drives.loc[known & offense_home, "posteam_points"].to_numpy()
        drive_away.loc[known & offense_home] = drives.loc[known & offense_home, "defteam_points"].to_numpy()
        drive_home.loc[known & ~offense_home] = drives.loc[known & ~offense_home, "defteam_points"].to_numpy()
        drive_away.loc[known & ~offense_home] = drives.loc[known & ~offense_home, "posteam_points"].to_numpy()
        summed = pd.DataFrame({
            "game_id": drives["game_id"].astype(str),
            "home_points": drive_home,
            "away_points": drive_away,
            "unknown": unknown.to_numpy(),
        })
        totals = summed.groupby("game_id", sort=False)[["home_points", "away_points"]].sum()
        unknown_games = set(summed.loc[summed["unknown"], "game_id"])
        home = home.add(totals["home_points"], fill_value=0)
        away = away.add(totals["away_points"], fill_value=0)
    else:
        unknown_games = set()
    if len(non_offensive):
        extra = non_offensive.copy()
        extra["game_id"] = extra["game_id"].astype(str)
        extra = extra.groupby("game_id", sort=False)[["home_points", "away_points"]].sum()
        home = home.add(extra["home_points"], fill_value=0)
        away = away.add(extra["away_points"], fill_value=0)
    rows = []
    for game in games.itertuples(index=False):
        game_id = str(game.game_id)
        built_home = round(float(home.get(game_id, 0)))
        built_away = round(float(away.get(game_id, 0)))
        home_score = pd.to_numeric(pd.Series([game.home_score]), errors="coerce").iloc[0]
        away_score = pd.to_numeric(pd.Series([game.away_score]), errors="coerce").iloc[0]
        kick = getattr(game, "kickoff_et", None)
        kick_text = "" if kick is None or pd.isna(kick) else pd.Timestamp(kick).isoformat()
        reason = ""
        if game_id in unknown_games:
            reason = "unknown_home_flag"
        elif pd.isna(home_score) or pd.isna(away_score):
            reason = "missing_final_score"
        elif round(float(home_score)) != built_home or round(float(away_score)) != built_away:
            reason = "score_mismatch"
        if not reason:
            continue
        final_home = None if pd.isna(home_score) else round(float(home_score))
        final_away = None if pd.isna(away_score) else round(float(away_score))
        rows.append({
            "game_id": game_id,
            "season": getattr(game, "season", pd.NA),
            "week": getattr(game, "week", pd.NA),
            "game_type": getattr(game, "game_type", ""),
            "kickoff_et": kick_text,
            "home_team": getattr(game, "home_team", ""),
            "away_team": getattr(game, "away_team", ""),
            "home_score": final_home,
            "away_score": final_away,
            "home_points": built_home,
            "away_points": built_away,
            "home_diff": None if final_home is None else built_home - final_home,
            "away_diff": None if final_away is None else built_away - final_away,
            "reason": reason,
        })
    if not rows:
        return pd.DataFrame(columns=list(MISMATCH_COLUMNS))
    return pd.DataFrame(rows, columns=list(MISMATCH_COLUMNS))
