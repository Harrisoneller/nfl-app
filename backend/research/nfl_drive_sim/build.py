"""Build the P1 drive table, reconciliation file, and season summaries.

Run from ``backend``::

    python -m research.nfl_drive_sim.build

The full drive table is written to the gitignored cache. Committed files are
the season summary, the mismatch list, the schema, row counts, coverage gaps,
and a sha256 manifest.
"""

from __future__ import annotations

import pandas as pd

from .cache import FIRST_SEASON, LAST_SEASON, PBP_COLUMNS, NflverseCache, default_cache_dir
from .calendar import prepare_schedule
from .drives import build_drive_table
from .market import assert_no_market_columns
from .pregame import pregame_quarterback_table
from .reconcile import reconcile
from .summarize import write_outputs

SEASON_SOURCES = ("pbp", "rosters", "depth_charts", "injuries", "participation")


def _print(message: str) -> None:
    print(message, flush=True)


def _edge_counts(plays: pd.DataFrame) -> dict[str, int]:
    if len(plays) == 0:
        return {}
    kickoff = plays["play_type"].astype(str).eq("kickoff") if "play_type" in plays.columns else pd.Series(False, index=plays.index)
    touchdown = pd.to_numeric(plays["touchdown"], errors="coerce").fillna(0).eq(1) if "touchdown" in plays.columns else pd.Series(False, index=plays.index)
    own = pd.to_numeric(plays["own_kickoff_recovery"], errors="coerce").fillna(0).eq(1) if "own_kickoff_recovery" in plays.columns else pd.Series(False, index=plays.index)
    safety = pd.to_numeric(plays["safety"], errors="coerce").fillna(0).eq(1) if "safety" in plays.columns else pd.Series(False, index=plays.index)
    penalty = plays["play_type"].astype(str).eq("no_play") if "play_type" in plays.columns else pd.Series(False, index=plays.index)
    return {
        "kickoff_return_touchdowns": int((kickoff & touchdown).sum()),
        "own_kickoff_recoveries": int(own.sum()),
        "safeties": int(safety.sum()),
        "no_play_rows": int(penalty.sum()),
    }


def build(cache: NflverseCache | None = None) -> dict[str, str]:
    store = cache or NflverseCache()
    _print("caching schedules")
    schedules = store.load_schedules()
    split = prepare_schedule(schedules)
    assert_no_market_columns(split.games, "schedules")
    assert_no_market_columns(split.dropped, "dropped")
    _print(f"eligible games {len(split.games)} cutoff or unusable {len(split.dropped)}")

    for name in SEASON_SOURCES:
        for season in range(FIRST_SEASON, LAST_SEASON + 1):
            row = store.ensure_season_file(
                name, season, columns=PBP_COLUMNS if name == "pbp" else None,
            )
            _print(f"{name} {season} {row.get('status')} rows {row.get('rows', '')}")

    drive_frames = []
    extra_frames = []
    edges: dict[str, int] = {}
    known_ids = set(split.games["game_id"].astype(str)) | set(split.dropped["game_id"].astype(str))
    orphans: list[str] = []
    for season in range(FIRST_SEASON, LAST_SEASON + 1):
        plays = store.load_pbp(season)
        if len(plays) == 0:
            _print(f"pbp {season} empty")
            continue
        missing = sorted(set(plays["game_id"].astype(str)) - known_ids)
        orphans.extend(missing)
        built = build_drive_table(plays, schedules)
        drive_frames.append(built.drives)
        extra_frames.append(built.non_offensive)
        for key, count in _edge_counts(plays).items():
            edges[key] = edges.get(key, 0) + count
        _print(f"built {season} drives {len(built.drives)}")
        del plays

    drives = pd.concat(drive_frames, ignore_index=True) if drive_frames else built_empty_drives()
    non_offensive = pd.concat(extra_frames, ignore_index=True) if extra_frames else pd.DataFrame(
        columns=["game_id", "home_points", "away_points"]
    )
    games = split.games[split.games["game_id"].isin(set(drives["game_id"]) | set(non_offensive.get("game_id", [])))].copy()
    # Every loaded pbp game should be checked, including one with no drive row.
    pbp_game_ids = set(games["game_id"])
    if len(drives) or len(non_offensive):
        pbp_game_ids |= set(drives["game_id"].astype(str)) if len(drives) else set()
        if len(non_offensive):
            pbp_game_ids |= set(non_offensive["game_id"].astype(str))
    games = split.games[split.games["game_id"].astype(str).isin(pbp_game_ids)].copy()
    mismatches = reconcile(drives, non_offensive, games)
    _print(f"mismatches {len(mismatches)} of {len(games)}")

    depth_frames = []
    for season in range(FIRST_SEASON, LAST_SEASON + 1):
        chart = store.load_table("depth_charts", season)
        if len(chart):
            depth_frames.append(chart)
    charts = pd.concat(depth_frames, ignore_index=True) if depth_frames else pd.DataFrame()
    quarterbacks = pregame_quarterback_table(charts, games) if len(games) else pd.DataFrame()
    covered = int(quarterbacks["qb_id"].notna().sum()) if len(quarterbacks) else 0
    qb_rows = len(quarterbacks)
    _print(f"pregame qb {covered} of {qb_rows}")

    coverage = _coverage(store, split, edges, covered, qb_rows, drives, orphans)
    hashes = write_outputs(
        drives=drives,
        games=games,
        mismatches=mismatches,
        coverage=coverage,
        drive_table_path=default_cache_dir() / "drive_table.parquet",
    )
    for name, digest in hashes.items():
        _print(f"{digest}  {name}")
    return hashes


def built_empty_drives() -> pd.DataFrame:
    from .drives import DRIVE_COLUMNS
    return pd.DataFrame(columns=list(DRIVE_COLUMNS))


def _coverage(store, split, edges, covered, qb_rows, drives, orphans: list[str]) -> pd.DataFrame:
    rows = []
    source_status = store.coverage()
    for row in source_status.itertuples(index=False):
        if row.status != "ok":
            rows.append({
                "kind": "source_missing",
                "season": row.season,
                "game_id": "",
                "count": 1,
                "detail": f"{row.name} {row.status} {row.detail}",
            })
        elif row.market_columns_removed:
            rows.append({
                "kind": "market_columns_removed",
                "season": row.season,
                "game_id": "",
                "count": len(str(row.market_columns_removed).split(",")),
                "detail": f"{row.name}: {row.market_columns_removed}",
            })
    reason_counts = split.dropped["reason"].value_counts() if len(split.dropped) else {}
    for reason, count in dict(reason_counts).items():
        if reason == "season_out_of_range":
            rows.append({
                "kind": "season_out_of_range",
                "season": "",
                "game_id": "",
                "count": int(count),
                "detail": "Schedule rows outside 2015-2026 were not loaded.",
            })
    listed = split.dropped[split.dropped["reason"].isin(["on_or_after_cutoff", "missing_kickoff"])] if len(split.dropped) else split.dropped
    for game in listed.itertuples(index=False):
        kick = getattr(game, "kickoff_et", "")
        kick_text = "" if kick is None or pd.isna(kick) else pd.Timestamp(kick).isoformat()
        rows.append({
            "kind": game.reason,
            "season": getattr(game, "season", ""),
            "game_id": game.game_id,
            "count": 1,
            "detail": kick_text,
        })
    for game_id in orphans:
        rows.append({
            "kind": "missing_kickoff",
            "season": "",
            "game_id": game_id,
            "count": 1,
            "detail": "Play-by-play game has no schedule kickoff and was not loaded.",
        })
    for key, count in edges.items():
        rows.append({
            "kind": "edge_count", "season": "", "game_id": "", "count": count, "detail": key,
        })
    rows.append({
        "kind": "pregame_qb",
        "season": "",
        "game_id": "",
        "count": covered,
        "detail": (
            f"{covered} of {qb_rows} team-games have a depth-chart QB1. "
            "Weekly charts through 2024 have no publication timestamp, so qb_asof is "
            "kickoff minus one hour in America/New_York. The 2025-2026 file has a dt "
            "snapshot; qb_asof is the latest snapshot strictly before kickoff. "
            "The id is not the passer who played."
        ),
    })
    rows.append({
        "kind": "assumption",
        "season": "",
        "game_id": "",
        "count": "",
        "detail": (
            "nflverse gameday and gametime are an America/New_York wall clock. "
            "A missing gametime would be filled with 13:00 Eastern. "
            "Aware timestamps are converted to Eastern before the 2027-01-01 cutoff. "
            "End-of-game clock expiration is labeled END_OF_HALF. "
            "nflverse has no onside_kick column; own_kickoff_recovery marks a kick the kicking team recovered."
        ),
    })
    if len(drives):
        rows.append({
            "kind": "drives",
            "season": "",
            "game_id": "",
            "count": len(drives),
            "detail": "Offensive drive rows. The full table is cached, not committed.",
        })
    if not rows:
        return pd.DataFrame(columns=["kind", "season", "game_id", "count", "detail"])
    return pd.DataFrame(rows, columns=["kind", "season", "game_id", "count", "detail"])


if __name__ == "__main__":
    build()
