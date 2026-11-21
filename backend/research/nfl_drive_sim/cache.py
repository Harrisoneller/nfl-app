"""Local parquet cache for the free nflverse releases.

Downloads land under ``backend/research/nfl_drive_sim/.cache`` and are
gitignored. The manifest next to them records the source URL, the sha256 of
the file we kept, and any market column names that were present upstream and
left out of the cache. Values of those columns are not stored.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import pyarrow.parquet as pq

from .market import is_market_column, strip_market_columns

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
USER_AGENT = "nfl-drive-sim-research"
FIRST_SEASON = 2015
LAST_SEASON = 2026

PBP_COLUMNS = (
    "play_id",
    "game_id",
    "home_team",
    "away_team",
    "season_type",
    "season",
    "week",
    "posteam",
    "defteam",
    "yardline_100",
    "qtr",
    "quarter_seconds_remaining",
    "half_seconds_remaining",
    "game_seconds_remaining",
    "down",
    "ydstogo",
    "yards_gained",
    "posteam_timeouts_remaining",
    "defteam_timeouts_remaining",
    "posteam_score",
    "defteam_score",
    "score_differential",
    "total_home_score",
    "total_away_score",
    "play_type",
    "touchdown",
    "td_team",
    "return_touchdown",
    "safety",
    "field_goal_result",
    "field_goal_attempt",
    "extra_point_result",
    "extra_point_attempt",
    "two_point_attempt",
    "two_point_conv_result",
    "punt_attempt",
    "interception",
    "fumble",
    "fumble_lost",
    "fumbled_1_team",
    "pass",
    "rush",
    "qb_dropback",
    "qb_kneel",
    "qb_spike",
    "passer_player_id",
    "rusher_player_id",
    "kickoff_attempt",
    "own_kickoff_recovery",
    "own_kickoff_recovery_td",
    "penalty",
    "first_down",
    "timeout",
    "fixed_drive",
    "fixed_drive_result",
    "drive",
    "drive_time_of_possession",
)

# Extra play columns for the research sim. Cached separately from the P1 extract.
P2_PBP_COLUMNS = PBP_COLUMNS + (
    "no_huddle",
    "epa",
    "cpoe",
    "success",
    "sack",
    "qb_hit",
    "complete_pass",
    "out_of_bounds",
)

SCHEDULE_COLUMNS = (
    "game_id",
    "season",
    "game_type",
    "week",
    "gameday",
    "weekday",
    "gametime",
    "away_team",
    "away_score",
    "home_team",
    "home_score",
    "location",
    "overtime",
    "roof",
    "surface",
    "temp",
    "wind",
    "away_rest",
    "home_rest",
    "div_game",
    "stadium_id",
    "stadium",
)

SOURCE_URLS = {
    "pbp": RELEASE + "/pbp/play_by_play_{season}.parquet",
    "pbp_model": RELEASE + "/pbp/play_by_play_{season}.parquet",
    "rosters": RELEASE + "/rosters/roster_{season}.parquet",
    "depth_charts": RELEASE + "/depth_charts/depth_charts_{season}.parquet",
    "injuries": RELEASE + "/injuries/injuries_{season}.parquet",
    "participation": RELEASE + "/pbp_participation/pbp_participation_{season}.parquet",
    "schedules": RELEASE + "/schedules/games.csv",
}


def default_cache_dir() -> Path:
    return Path(__file__).resolve().parent / ".cache"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".partial")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    temporary.replace(dest)


class NflverseCache:
    """Download the research sources once and read the pruned parquet after that."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self._manifest = self._read_manifest()

    def _read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {"sources": []}
        return json.loads(self.manifest_path.read_text())

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self._manifest, indent=2) + "\n")

    def _entry(self, name: str, season: int | None) -> dict | None:
        for row in self._manifest["sources"]:
            if row.get("name") != name or row.get("season") != season:
                continue
            if row.get("status") == "missing":
                return row
            if row.get("status") == "ok":
                path = self.root / row["path"]
                if path.exists():
                    return row
        return None

    def _remember(self, row: dict) -> None:
        kept = [
            item for item in self._manifest["sources"]
            if not (item.get("name") == row["name"] and item.get("season") == row["season"])
        ]
        kept.append(row)
        self._manifest["sources"] = kept
        self._write_manifest()

    def _stamp(self, **row: object) -> dict:
        row["downloaded_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        return row

    def ensure_season_file(self, name: str, season: int, *, columns: tuple[str, ...] | None) -> dict:
        """Cache one season file. A 404 is recorded and is not raised."""
        existing = self._entry(name, season)
        if existing is not None:
            return existing
        url = SOURCE_URLS[name].format(season=season)
        raw_path = self.root / "raw" / f"{name}_{season}.parquet"
        try:
            _download(url, raw_path)
        except HTTPError as exc:
            if exc.code == 404:
                row = self._stamp(
                    name=name, season=season, url=url, status="missing",
                    detail="404",
                )
                self._remember(row)
                return row
            raise
        except URLError as exc:
            row = self._stamp(name=name, season=season, url=url, status="error", detail=str(exc.reason)[:200])
            self._remember(row)
            return row
        schema_names = list(pq.read_schema(raw_path).names)
        market_seen = [column for column in schema_names if is_market_column(column)]
        use = [column for column in (columns or tuple(schema_names)) if column in schema_names]
        use = [column for column in use if not is_market_column(column)]
        frame = pq.read_table(raw_path, columns=use).to_pandas()
        frame, removed = strip_market_columns(frame)
        dest = self.root / name / f"{season}.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(dest, index=False)
        raw_path.unlink(missing_ok=True)
        row = self._stamp(
            name=name,
            season=season,
            url=url,
            path=str(dest.relative_to(self.root)),
            sha256=_sha256(dest),
            bytes=dest.stat().st_size,
            rows=len(frame),
            status="ok",
            market_columns_removed=sorted(set(market_seen) | set(removed)),
        )
        self._remember(row)
        return row

    def ensure_schedules(self) -> dict:
        existing = self._entry("schedules", None)
        if existing is not None:
            return existing
        url = SOURCE_URLS["schedules"]
        raw_path = self.root / "raw" / "games.csv"
        _download(url, raw_path)
        frame = pd.read_csv(raw_path)
        market_seen = [column for column in frame.columns if is_market_column(str(column))]
        keep = [column for column in SCHEDULE_COLUMNS if column in frame.columns]
        frame = frame.loc[:, keep].copy()
        frame, removed = strip_market_columns(frame)
        frame = frame[pd.to_numeric(frame["season"], errors="coerce").between(FIRST_SEASON, LAST_SEASON)]
        dest = self.root / "schedules" / "games.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(dest, index=False)
        raw_path.unlink(missing_ok=True)
        row = self._stamp(
            name="schedules",
            season=None,
            url=url,
            path=str(dest.relative_to(self.root)),
            sha256=_sha256(dest),
            bytes=dest.stat().st_size,
            rows=len(frame),
            status="ok",
            market_columns_removed=sorted(set(market_seen) | set(removed)),
        )
        self._remember(row)
        return row

    def load_schedules(self) -> pd.DataFrame:
        row = self.ensure_schedules()
        if row.get("status") != "ok":
            raise FileNotFoundError("schedule cache is not available")
        frame = pd.read_parquet(self.root / row["path"])
        _, removed = strip_market_columns(frame)
        if removed:
            raise RuntimeError(f"cached schedule still has market columns: {removed}")
        return frame

    def load_pbp_model(self, season: int) -> pd.DataFrame:
        """Play-by-play with the extra columns the pace and yards models use."""
        row = self.ensure_season_file("pbp_model", season, columns=P2_PBP_COLUMNS)
        if row.get("status") != "ok":
            return pd.DataFrame()
        frame = pd.read_parquet(self.root / row["path"])
        frame, removed = strip_market_columns(frame)
        if removed:
            raise RuntimeError(f"cached pbp_model still has market columns: {removed}")
        if "season" not in frame.columns:
            frame["season"] = season
        return frame

    def load_pbp(self, season: int) -> pd.DataFrame:
        row = self.ensure_season_file("pbp", season, columns=PBP_COLUMNS)
        if row.get("status") != "ok":
            return pd.DataFrame()
        frame = pd.read_parquet(self.root / row["path"])
        frame, removed = strip_market_columns(frame)
        if removed:
            raise RuntimeError(f"cached pbp still has market columns: {removed}")
        if "season" not in frame.columns:
            frame["season"] = season
        return frame

    def load_table(self, name: str, season: int) -> pd.DataFrame:
        columns = None
        row = self.ensure_season_file(name, season, columns=columns)
        if row.get("status") != "ok":
            return pd.DataFrame()
        frame = pd.read_parquet(self.root / row["path"])
        frame, removed = strip_market_columns(frame)
        if removed:
            raise RuntimeError(f"cached {name} still has market columns: {removed}")
        return frame

    def coverage(self) -> pd.DataFrame:
        """One row per source attempt, including 404s."""
        rows = []
        for row in self._manifest["sources"]:
            rows.append({
                "name": row.get("name"),
                "season": row.get("season") if row.get("season") is not None else "",
                "status": row.get("status"),
                "rows": row.get("rows", ""),
                "detail": row.get("detail", ""),
                "market_columns_removed": ",".join(row.get("market_columns_removed") or []),
            })
        return pd.DataFrame(rows)
