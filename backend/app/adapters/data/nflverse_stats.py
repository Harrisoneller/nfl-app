"""Direct nflverse loaders for data nfl_data_py can no longer reach.

Why this exists
---------------
``nfl_data_py.import_weekly_data`` / ``import_seasonal_data`` read
``player_stats/player_stats_{year}.parquet``. nflverse stopped publishing that
release after the 2024 season — 2025 and later return **404** — and moved
player stats to ``stats_player/stats_player_week_{year}.parquet`` with a
renamed schema. The adapter swallowed the 404, so every player surface quietly
served the newest season it *could* find: in Sept 2026 the "2026" game log for
a receiver was his 2024 game log, and projections had no 2025/2026 evidence.

This module reads the new release and maps it back onto the legacy column
names every consumer already uses (``recent_team``, ``interceptions``,
``sacks``, ``sack_yards`` …), and rebuilds the seasonal frame exactly the way
``nfl_data_py.import_seasonal_data`` did (same share columns: tgt_sh, ay_sh,
wopr, dom …). Consumers do not change.

Schedules are read from ``schedules/games.csv`` on the same release host — the
canonical nflverse schedule with closing ``spread_line``/``total_line`` and the
starting QB ids the v5 game model's QB layer needs.

Pure functions + a tiny in-process cache; the async adapter wraps them with the
same timeout/circuit-breaker policy as every other nflverse call.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

from ...logging_config import get_logger

log = get_logger(__name__)

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
WEEKLY_URL = RELEASE + "/stats_player/stats_player_week_{season}.parquet"
SCHEDULES_URL = RELEASE + "/schedules/games.csv"

# New-schema name -> legacy (nfl_data_py player_stats) name. The new columns are
# kept as well, so code written against either schema works.
LEGACY_RENAMES: dict[str, str] = {
    "team": "recent_team",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
    "sack_yards_lost": "sack_yards",
}

_TTL_CURRENT_S = 3 * 3600
_TTL_PAST_S = 7 * 24 * 3600
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    if val is not None:
        _cache[key] = (now, val)
    return val


def to_legacy_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Map a ``stats_player_week`` frame onto the legacy weekly schema."""
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    for new, old in LEGACY_RENAMES.items():
        if new in out.columns and old not in out.columns:
            out[old] = out[new]
    if "fantasy_points_ppr" not in out.columns and "fantasy_points" in out.columns:
        out["fantasy_points_ppr"] = out["fantasy_points"]
    return out


def seasonal_from_weekly(data: pd.DataFrame, s_type: str = "REG") -> pd.DataFrame:
    """Rebuild ``nfl_data_py.import_seasonal_data`` output from weekly rows."""
    if data is None or len(data) == 0:
        return data
    data = to_legacy_weekly(data)
    if s_type != "ALL" and "season_type" in data.columns:
        data = data[data["season_type"] == s_type]
    if len(data) == 0:
        return data

    def col(name: str) -> pd.Series:
        return pd.to_numeric(data[name], errors="coerce").fillna(0) if name in data.columns \
            else pd.Series(0.0, index=data.index)

    team_cols = {
        "atts": "attempts", "comps": "completions", "p_yds": "passing_yards",
        "p_tds": "passing_tds", "p_ayds": "passing_air_yards",
        "p_yac": "passing_yards_after_catch", "p_fds": "passing_first_downs",
        "ppr_pts": "fantasy_points_ppr",
    }
    base = data[["recent_team", "season", "week"]].copy()
    for k, v in team_cols.items():
        base[k] = col(v)
    pg = base.groupby(["recent_team", "season", "week"], as_index=False).sum()

    p_cols = [
        "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
        "rushing_2pt_conversions", "receptions", "targets", "receiving_yards",
        "receiving_tds", "receiving_air_yards", "receiving_yards_after_catch",
        "receiving_first_downs", "receiving_epa", "fantasy_points_ppr",
    ]
    ps = data[["player_id", "recent_team", "season", "week"]].copy()
    for c in p_cols:
        ps[c] = col(c)
    alls = ps.merge(pg, how="left", on=["recent_team", "season", "week"]).fillna(0)
    ss = alls.drop(columns=["recent_team", "week"]).groupby(
        ["player_id", "season"], as_index=False).sum(numeric_only=True)

    def div(a: pd.Series, b: pd.Series) -> pd.Series:
        return a / b.where(b != 0)

    ss["tgt_sh"] = div(ss["targets"], ss["atts"])
    ss["ay_sh"] = div(ss["receiving_air_yards"], ss["p_ayds"])
    ss["yac_sh"] = div(ss["receiving_yards_after_catch"], ss["p_yac"])
    ss["wopr"] = ss["tgt_sh"] * 1.5 + ss["ay_sh"] * 0.8
    ss["ry_sh"] = div(ss["receiving_yards"], ss["p_yds"])
    ss["rtd_sh"] = div(ss["receiving_tds"], ss["p_tds"])
    ss["rfd_sh"] = div(ss["receiving_first_downs"], ss["p_fds"])
    ss["rtdfd_sh"] = div(ss["receiving_tds"] + ss["receiving_first_downs"], ss["p_tds"] + ss["p_fds"])
    ss["dom"] = (ss["ry_sh"] + ss["rtd_sh"]) / 2
    ss["w8dom"] = ss["ry_sh"] * 0.8 + ss["rtd_sh"] * 0.2
    ss["yptmpa"] = div(ss["receiving_yards"], ss["atts"])
    ss["ppr_sh"] = div(ss["fantasy_points_ppr"], ss["ppr_pts"])

    # Per-week ratio columns (shares, racr/pacr) are meaningless when summed;
    # the season versions are recomputed above.
    ratio_cols = ("target_share", "air_yards_share", "wopr", "racr", "pacr", "fg_pct", "pat_pct")
    num = data.drop(columns=[c for c in ("recent_team", "week", *ratio_cols) if c in data.columns])
    szn = num.groupby(["player_id", "season", "season_type"], as_index=False).sum(numeric_only=True)
    games = data.groupby(["player_id", "season"]).size().rename("games").reset_index()
    szn = szn.merge(games, how="left", on=["player_id", "season"])
    keep = ["player_id", "season", "tgt_sh", "ay_sh", "yac_sh", "wopr", "ry_sh", "rtd_sh",
            "rfd_sh", "rtdfd_sh", "dom", "w8dom", "yptmpa", "ppr_sh"]
    szn = szn.merge(ss[keep], how="left", on=["player_id", "season"])
    # Season-level versions of the per-week ratio columns dropped above.
    szn["target_share"] = szn["tgt_sh"]
    szn["air_yards_share"] = szn["ay_sh"]
    if {"receiving_yards", "receiving_air_yards"}.issubset(szn.columns):
        szn["racr"] = div(szn["receiving_yards"], szn["receiving_air_yards"])
    if {"passing_yards", "passing_air_yards"}.issubset(szn.columns):
        szn["pacr"] = div(szn["passing_yards"], szn["passing_air_yards"])
    # Identity columns the legacy frame carried through the groupby.
    ident = [c for c in ("player_name", "player_display_name", "position", "position_group",
                         "headshot_url", "recent_team") if c in data.columns]
    if ident:
        last = data.sort_values("week").groupby(["player_id", "season"])[ident].last().reset_index()
        szn = szn.merge(last, how="left", on=["player_id", "season"])
    return szn


def load_weekly(season: int, *, current: bool = False) -> pd.DataFrame | None:
    """Weekly player stats for one season in the legacy schema (None if absent)."""
    def _fetch():
        try:
            return to_legacy_weekly(pd.read_parquet(WEEKLY_URL.format(season=season)))
        except Exception as e:  # noqa: BLE001 — absent season / network: caller falls back
            log.debug("nflverse_weekly_unavailable", season=season, error=str(e)[:160])
            return None
    return _cached(f"weekly:{season}", _TTL_CURRENT_S if current else _TTL_PAST_S, _fetch)


def load_schedules() -> pd.DataFrame | None:
    """All seasons' schedules with lines, scores, starters (nflverse games.csv)."""
    def _fetch():
        try:
            return pd.read_csv(SCHEDULES_URL)
        except Exception as e:  # noqa: BLE001
            log.warning("nflverse_schedules_unavailable", error=str(e)[:160])
            return None
    return _cached("schedules", _TTL_CURRENT_S, _fetch)
