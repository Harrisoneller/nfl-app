"""Committed season summaries. The drive table itself stays in the local cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .drives import DRIVE_COLUMNS, OUTCOMES, SCHEMA_NOTES

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def season_summary(
    drives: pd.DataFrame,
    games: pd.DataFrame,
    mismatches: pd.DataFrame,
) -> pd.DataFrame:
    """Per-season drive counts, outcome shares, and the score-mismatch rate."""
    rows = []
    seasons = sorted(set(pd.to_numeric(games["season"], errors="coerce").dropna().astype(int)))
    if len(drives):
        seasons = sorted(set(seasons) | set(pd.to_numeric(drives["season"], errors="coerce").dropna().astype(int)))
    frames = [("ALL", games, drives, mismatches)]
    for season in seasons:
        frames.append((
            str(season),
            games[pd.to_numeric(games["season"], errors="coerce").eq(season)] if len(games) else games,
            drives[pd.to_numeric(drives["season"], errors="coerce").eq(season)] if len(drives) else drives,
            mismatches[pd.to_numeric(mismatches["season"], errors="coerce").eq(season)] if len(mismatches) else mismatches,
        ))
    for label, season_games, season_drives, season_mismatches in frames:
        n_games = int(season_games["game_id"].nunique()) if len(season_games) else 0
        n_drives = len(season_drives)
        n_team_games = n_games * 2
        row: dict[str, object] = {
            "season": label,
            "n_games": n_games,
            "n_team_games": n_team_games,
            "n_drives": n_drives,
            "mean_drives_per_team_game": (n_drives / n_team_games) if n_team_games else None,
            "n_mismatch": len(season_mismatches),
            "n_checked": n_games,
            "mismatch_rate": (len(season_mismatches) / n_games) if n_games else None,
        }
        counts = season_drives["outcome"].value_counts() if n_drives else pd.Series(dtype="int64")
        for outcome in OUTCOMES:
            count = int(counts.get(outcome, 0))
            row[f"n_{outcome}"] = count
            row[f"share_{outcome}"] = (count / n_drives) if n_drives else None
        rows.append(row)
    # ALL was inserted first. Move it to the bottom.
    if rows:
        rows.append(rows.pop(0))
    return pd.DataFrame(rows)


def drive_schema(drives: pd.DataFrame) -> pd.DataFrame:
    observed = {column: str(dtype) for column, dtype in drives.dtypes.items()} if len(drives.columns) else {}
    rows = []
    for column in DRIVE_COLUMNS:
        rows.append({
            "column": column,
            "dtype": observed.get(column, ""),
            "description": SCHEMA_NOTES[column],
        })
    return pd.DataFrame(rows)


def drive_counts(drives: pd.DataFrame) -> pd.DataFrame:
    if len(drives) == 0:
        return pd.DataFrame({"season": ["ALL"], "n_rows": [0]})
    counts = drives.groupby(drives["season"].astype(int), sort=True).size().rename("n_rows").reset_index()
    counts["season"] = counts["season"].astype(str)
    total = pd.DataFrame({"season": ["ALL"], "n_rows": [len(drives)]})
    return pd.concat([counts, total], ignore_index=True)


def write_drive_table(drives: pd.DataFrame, path: Path) -> str:
    """Write the full table to the gitignored cache and return its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(drives, preserve_index=False)
    pq.write_table(table, path)
    return _sha256(path)


def write_outputs(
    *,
    drives: pd.DataFrame,
    games: pd.DataFrame,
    mismatches: pd.DataFrame,
    coverage: pd.DataFrame,
    drive_table_path: Path,
    output_dir: Path | None = None,
) -> dict[str, str]:
    """Write the committed CSVs and the sha256 manifest. Return hash by filename."""
    directory = output_dir or OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    summary = season_summary(drives, games, mismatches)
    schema = drive_schema(drives)
    counts = drive_counts(drives)
    files = {
        "season_summary.csv": summary,
        "reconciliation_mismatches.csv": mismatches,
        "drive_table_schema.csv": schema,
        "drive_table_counts.csv": counts,
        "coverage_gaps.csv": coverage,
    }
    hashes: dict[str, str] = {}
    for name, frame in files.items():
        path = directory / name
        frame.to_csv(path, index=False)
        hashes[name] = _sha256(path)
    hashes["drive_table.parquet"] = write_drive_table(drives, drive_table_path)
    lines = [f"{digest}  {name}" for name, digest in hashes.items()]
    manifest = directory / "manifest.sha256"
    manifest.write_text("\n".join(lines) + "\n")
    hashes["manifest.sha256"] = _sha256(manifest)
    return hashes
