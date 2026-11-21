"""Build a separate EPA play extract for 2015-2026.

The locked ``.cache`` games parquet and the P1 files are read and not written.
Raw downloads are hashed, market columns are stripped, and the pruned extract
is saved under ``outputs/p3/derived``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .cache import P2_PBP_COLUMNS, _download, _sha256
from .calendar import SCREEN_CUTOFF, parse_wall_kickoff
from .drives import build_drive_table
from .market import assert_no_market_columns, strip_market_columns
from .run_p3 import merge_manifest

RELEASE_TAG = "pbp"
SOURCE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.parquet"
)
SEASONS = tuple(range(2015, 2027))
EXTRA_COLUMNS = ("wp", "special_teams_play")
DERIVED = Path(__file__).resolve().parent / "outputs" / "p3" / "derived"
LOCKED_GAMES = Path(__file__).resolve().parent / ".cache" / "schedules" / "games.parquet"
LOCKED_DRIVES = Path(__file__).resolve().parent / ".cache" / "drive_table.parquet"
MANIFEST = Path(__file__).resolve().parent / "outputs" / "p3" / "manifest.sha256"


class SourceMismatch(RuntimeError):
    """A derived play file does not match the locked schedule or drive table."""


def _final_scores(plays: pd.DataFrame) -> pd.DataFrame:
    work = plays.copy()
    work["game_id"] = work["game_id"].astype(str)
    # A later play_id can carry an earlier score. The game total is the max.
    scores = work.groupby("game_id", sort=False)[["total_home_score", "total_away_score"]].max()
    return scores.rename(columns={"total_home_score": "home_score", "total_away_score": "away_score"}).reset_index()


def _completed_schedule(games: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for rec in games.itertuples(index=False):
        kick, _filled = parse_wall_kickoff(rec.gameday, rec.gametime)
        if kick is None or kick >= SCREEN_CUTOFF:
            continue
        if pd.isna(rec.home_score) or pd.isna(rec.away_score):
            continue
        rows.append({
            "game_id": str(rec.game_id),
            "season": int(rec.season),
            "week": int(rec.week),
            "game_type": str(rec.game_type),
            "kickoff_et": kick.isoformat(),
            "home_team": str(rec.home_team),
            "away_team": str(rec.away_team),
            "home_score": int(rec.home_score),
            "away_score": int(rec.away_score),
        })
    return pd.DataFrame(rows)


def verify_plays(plays: pd.DataFrame, games: pd.DataFrame, drives: pd.DataFrame) -> None:
    """Abort when game ids or final scores disagree with the locked inputs."""
    scores = _final_scores(plays).set_index("game_id")
    schedule = _completed_schedule(games).set_index("game_id")
    missing_sched = schedule.index.difference(scores.index)
    if len(missing_sched):
        sample = list(missing_sched[:5])
        raise SourceMismatch(f"completed schedule games missing from the derived plays: {sample}")
    shared = scores.index.intersection(schedule.index)
    home = scores.loc[shared, "home_score"]
    away = scores.loc[shared, "away_score"]
    bad = (
        home.ne(schedule.loc[shared, "home_score"])
        | away.ne(schedule.loc[shared, "away_score"])
    )
    if bool(bad.fillna(True).any()):
        sample = list(home.index[bad.fillna(True)][:5])
        raise SourceMismatch(f"final scores disagree with the locked games parquet: {sample}")
    built = build_drive_table(plays, games)
    drive_ids = set(drives["game_id"].astype(str))
    built_ids = set(built.drives["game_id"].astype(str))
    missing = sorted(drive_ids - built_ids)
    if missing:
        raise SourceMismatch(f"P1 drive-table games missing from the derived plays: {missing[:5]}")
    recon = built.drives.groupby("game_id", sort=False)[["posteam_points", "defteam_points"]].sum()
    old = drives.groupby("game_id", sort=False)[["posteam_points", "defteam_points"]].sum()
    both = recon.index.intersection(old.index)
    points_bad = (
        recon.loc[both, "posteam_points"].ne(old.loc[both, "posteam_points"])
        | recon.loc[both, "defteam_points"].ne(old.loc[both, "defteam_points"])
    )
    if bool(points_bad.any()):
        sample = list(both[points_bad][:5])
        raise SourceMismatch(f"drive points disagree with the P1 drive table: {sample}")


def write_history_sets(games: pd.DataFrame, dest: Path) -> Path:
    """2015 is the training floor. 2018 is history and the sensitivity set."""
    schedule = _completed_schedule(games)
    regular = schedule[schedule["game_type"].eq("REG")]
    train = regular[regular["season"].eq(2015)].copy()
    train["role"] = "train_2015"
    sensitive = regular[regular["season"].eq(2018)].copy()
    sensitive["role"] = "sensitivity_2018"
    frame = pd.concat([train, sensitive], ignore_index=True)
    primary_ids = set(pd.read_csv(dest.parent / "game_set.csv").query("role == 'primary'")["game_id"].astype(str))
    overlap = sorted(set(frame["game_id"].astype(str)) & primary_ids)
    if overlap:
        raise SourceMismatch(f"history sets overlap the primary slate: {overlap[:5]}")
    frame = frame.sort_values(["role", "kickoff_et", "game_id"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(dest, index=False)
    return dest


def build_pbp_models(raw_dir: Path | None = None) -> dict:
    """Download, hash, strip, and verify. The locked cache is not written."""
    raw_root = Path(raw_dir) if raw_dir is not None else Path("/tmp/p3_nflverse_raw")
    raw_root.mkdir(parents=True, exist_ok=True)
    derived = DERIVED / "pbp_model"
    derived.mkdir(parents=True, exist_ok=True)
    games = pd.read_parquet(LOCKED_GAMES)
    drives = pd.read_parquet(LOCKED_DRIVES)
    pin = {"release": RELEASE_TAG, "url_template": SOURCE_URL, "files": []}
    frames = []
    updates = []
    for season in SEASONS:
        url = SOURCE_URL.format(season=season)
        raw_path = raw_root / f"play_by_play_{season}.parquet"
        if not raw_path.exists():
            _download(url, raw_path)
        source_sha = _sha256(raw_path)
        pin["files"].append({
            "season": season,
            "url": url,
            "sha256": source_sha,
            "bytes": raw_path.stat().st_size,
        })
        names = set(pq.read_schema(raw_path).names)
        wanted = [column for column in (*P2_PBP_COLUMNS, *EXTRA_COLUMNS) if column in names]
        wanted = [column for column in wanted if column not in {"spread_line", "total_line"}]
        frame = pq.read_table(raw_path, columns=wanted).to_pandas()
        frame, _removed = strip_market_columns(frame)
        assert_no_market_columns(frame, f"pbp_model {season}")
        if "season" not in frame.columns:
            frame["season"] = season
        dest = derived / f"{season}.parquet"
        frame.to_parquet(dest, index=False)
        updates.append((_sha256(dest), f"derived/pbp_model/{season}.parquet"))
        frames.append(frame)
        print(f"pbp_model {season} rows {len(frame)} source {source_sha[:12]}", flush=True)
    plays = pd.concat(frames, ignore_index=True)
    verify_plays(plays, games, drives)
    pin_path = DERIVED.parent / "source_pin.json"
    pin_path.write_text(json.dumps(pin, indent=2) + "\n")
    history = write_history_sets(games, DERIVED.parent / "history_sets.csv")
    updates.append((hashlib.sha256(pin_path.read_bytes()).hexdigest(), "source_pin.json"))
    updates.append((hashlib.sha256(history.read_bytes()).hexdigest(), "history_sets.csv"))
    merge_manifest(MANIFEST, updates)
    return {"seasons": list(SEASONS), "rows": len(plays), "verified": True}


if __name__ == "__main__":
    print(build_pbp_models(), flush=True)
