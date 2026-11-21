"""Freeze one week's total projections. This file does not score a game.

It aborts unless ``TOTALS_PREREG.md`` says ``Status: locked``. Output goes
under ``outputs/totals/freezes/``. The P3 files and the cache are read only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .calendar import parse_wall_kickoff
from .clock import fit_clock, fit_late_table, fit_scrimmage_lengths
from .drives import build_drive_table
from .market import assert_no_market_columns, strip_market_columns
from .p2_data import qb_game_table, team_game_table
from .p2_fit import fit_drive_model, fit_pace, fit_ypp
from .p3_features import _depth_charts, _feature_values, _schedule, _v5_rows
from .p3_sources import DERIVED, LOCKED_GAMES
from .priors import with_priors
from .run_p2 import _attach_opponent_defense, _banks, _card, _rates
from .run_p3 import assign_quarterbacks, rows_before_cutoff
from .simulate import simulate_game
from .teams import canon
from .v5_replay import (
    empirical_pmf,
    game_dropped,
    ols_is_identified,
    ratings_as_of,
    replay_week,
    sample_sd,
    sim_seed,
)

ROOT = Path(__file__).resolve().parent
PREREG = ROOT / "TOTALS_PREREG.md"
SIM_CONFIG_PATH = ROOT / "sim_config.py"
P3_PREREG = ROOT / "P3_PREREG.md"
OUT = ROOT / "outputs" / "totals" / "freezes"
WINDOW1_PATH = ROOT / "outputs" / "totals" / "window1_game_ids.csv"
WINDOW1_N = 121
WINDOW1_SHA256 = "1a935f89a6ae60612c530493095adc1bcb93b99e5059176766dfee7502aa82ee"
LAST_REG_WEEK = 18
SIM_CONFIG_SHA256 = "f60cd566e06e87f66d000f8ec8f3afc4bd4e3b8ed91711374c005fe0e3be7ecb"
P3_PREREG_SHA256 = "bb114c35ab5158a8b19223daf61af2213f783cee40fe6da4f160b697a2bab506"
BANNED_OUTPUT = ("crps", "actual", "spread", "odds", "moneyline", "price", "kalshi")
# The website importer matches this version exactly. A column change is a new version.
FORMAT_VERSION = "totals-freeze-v1"
FREEZE_COLUMNS = (
    "format_version",
    "game_id",
    "season",
    "week",
    "kickoff_et",
    "home_team",
    "away_team",
    "home_qb",
    "away_qb",
    "drop_reason",
    "sim_margin_mean",
    "sim_total_mean",
    "sim_margin_pmf",
    "sim_total_pmf",
    "v5_margin",
    "v5_total",
    "v5_sigma_margin",
    "v5_sigma_total",
    "n_sims",
    "seed",
    "ypp_on",
)


class FreezeLocked(RuntimeError):
    """QA has not locked the totals preregistration."""


class FreezeError(RuntimeError):
    """The freeze inputs are not the pinned files, or a projection is late."""


def prereg_is_locked(path: Path | None = None) -> bool:
    """True only when the prereg carries the exact lock line."""
    text = (path or PREREG).read_text()
    return any(line.strip() == "Status: locked" for line in text.splitlines())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_frozen_inputs() -> dict[str, str]:
    """Refuse a sim config or a P3 prereg whose bytes are not the pin."""
    pins = {
        "sim_config.py": (_sha256(SIM_CONFIG_PATH), SIM_CONFIG_SHA256),
        "P3_PREREG.md": (_sha256(P3_PREREG), P3_PREREG_SHA256),
    }
    for name, (digest, locked) in pins.items():
        if digest != locked:
            raise FreezeError(f"{name} hash {digest} != {locked}")
    return {name: locked for name, (_digest, locked) in pins.items()}


def _with_kickoff(games: pd.DataFrame) -> pd.DataFrame:
    if "kickoff_et" in games.columns and games["kickoff_et"].notna().all():
        out = games.copy()
        out["kickoff_et"] = pd.to_datetime(out["kickoff_et"], utc=True)
        return out
    kicks = []
    for rec in games.itertuples(index=False):
        kick, _filled = parse_wall_kickoff(getattr(rec, "gameday", None), getattr(rec, "gametime", None))
        kicks.append(kick)
    out = games.copy()
    out["kickoff_et"] = kicks
    return out.dropna(subset=["kickoff_et"])


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def load_window1(path: Path | None = None) -> pd.DataFrame:
    """The pinned 121-game window. A hash or count mismatch aborts."""
    source = Path(path) if path is not None else WINDOW1_PATH
    digest = _sha256(source)
    if digest != WINDOW1_SHA256:
        raise FreezeError(f"window1 hash {digest} != {WINDOW1_SHA256}")
    frame = pd.read_csv(source)
    needed = ["game_id", "kickoff_et", "week"]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise FreezeError(f"window1 missing columns {missing}")
    if len(frame) != WINDOW1_N:
        raise FreezeError(f"window1 has {len(frame)} games, expected {WINDOW1_N}")
    if frame["game_id"].astype(str).duplicated().any():
        raise FreezeError("window1 has a duplicate game_id")
    ids = set(frame["game_id"].astype(str))
    if "2026_11_ARI_KC" not in ids or "2026_11_IND_HOU" in ids:
        raise FreezeError("window1 does not match the pinned kickoff rule")
    return frame


def _cancelled(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "cancelled", "canceled"}
    return bool(value)


def assemble_slate(
    window: pd.DataFrame,
    schedule: pd.DataFrame,
    week: int,
) -> tuple[pd.DataFrame, pd.Timestamp | None, list[dict[str, str]]]:
    """Pinned games whose current kickoff falls in ``week``.

    A move follows the schedule week. A cancellation, a missing row, or a
    move past week 18 is logged and left out. The other games still freeze.
    """
    pinned = window.loc[:, ["game_id", "week"]].copy()
    pinned["game_id"] = pinned["game_id"].astype(str)
    pinned = pinned.rename(columns={"week": "original_week"})
    sched = _with_kickoff(schedule).copy() if schedule is not None and len(schedule) else pd.DataFrame()
    if len(sched):
        sched["game_id"] = sched["game_id"].astype(str)
        if "cancelled" not in sched.columns:
            sched["cancelled"] = False
    merged = pinned.merge(sched, on="game_id", how="left", indicator=True)
    keep: list[int] = []
    exclusions: list[dict[str, str]] = []
    for idx, rec in merged.iterrows():
        original = int(rec["original_week"])
        missing = rec["_merge"] != "both" or pd.isna(rec.get("week"))
        played = None if missing else int(rec["week"])
        cancelled = False if missing else _cancelled(rec.get("cancelled"))
        change = schedule_change(original, played, cancelled or missing)
        gid = str(rec["game_id"])
        if missing or cancelled or change == "excluded":
            if original == int(week) or played == int(week):
                if missing:
                    reason = "missing"
                elif cancelled:
                    reason = "cancelled"
                else:
                    reason = "after_week_18"
                exclusions.append({"game_id": gid, "reason": reason})
            continue
        if played != int(week):
            if original == int(week):
                exclusions.append({"game_id": gid, "reason": "moved"})
            continue
        keep.append(idx)
    if not keep:
        return merged.iloc[0:0].copy(), None, exclusions
    slate = merged.loc[keep].drop(columns=["original_week", "_merge"]).reset_index(drop=True)
    slate = slate.sort_values(["kickoff_et", "game_id"]).reset_index(drop=True)
    cutoff = pd.to_datetime(slate["kickoff_et"], utc=True).min()
    return slate, pd.Timestamp(cutoff), exclusions


def refuse_started_game(kickoff, now) -> None:
    """A game already past kickoff is not frozen later."""
    if _utc(now) >= _utc(kickoff):
        raise FreezeError("a game is already past kickoff")


def assert_freeze_in_time(kickoffs, now) -> None:
    """A missed slate is excluded. It is not backfilled and it is not rerun."""
    stamps = [_utc(kick) for kick in kickoffs]
    if not stamps:
        raise FreezeError("empty slate")
    if _utc(now) >= min(stamps):
        raise FreezeError("missed freeze is excluded and is never backfilled or rerun")
    for stamp in stamps:
        refuse_started_game(stamp, now)


def schedule_change(original_week: int, played_week: int | None, cancelled: bool) -> str:
    """Where a postponed or cancelled game belongs. ``excluded`` is not a drop."""
    if cancelled or played_week is None:
        return "excluded"
    played = int(played_week)
    if played > LAST_REG_WEEK or played < 1:
        return "excluded"
    if played == int(original_week):
        return "same_week"
    return "other_week"


def counts_toward_drop(change: str) -> bool:
    """A cancellation or a post-week-18 move is outside the drop rate."""
    return change != "excluded"


def first_valid_freeze(attempts: list[tuple[object, object]]):
    """The earliest freeze strictly before the actual kickoff. A late one does not count."""
    valid = [_utc(frozen_at) for frozen_at, kickoff in attempts if _utc(frozen_at) < _utc(kickoff)]
    if not valid:
        return None
    return min(valid)


def select_week_games(
    games: pd.DataFrame,
    season: int,
    week: int,
    on_or_after: object | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Regular-season games in one week, and the cutoff that freezes them.

    ``on_or_after`` drops an earlier kickoff in that week. The week-11
    remainder uses 2026-11-22 so Thursday is not projected. The cutoff is
    the earliest remaining kickoff. Training rows have to be strictly before it.
    """
    frame = _with_kickoff(games)
    frame = frame[frame["season"].astype(int).eq(int(season))]
    frame = frame[frame["week"].astype(int).eq(int(week))]
    frame = frame[frame["game_type"].astype(str).eq("REG")]
    if on_or_after is not None:
        limit = pd.Timestamp(on_or_after)
        if limit.tzinfo is None:
            limit = limit.tz_localize("America/New_York")
        kicks = pd.to_datetime(frame["kickoff_et"], utc=True)
        frame = frame.loc[kicks >= limit.tz_convert("UTC")]
    if len(frame) == 0:
        raise FreezeError(f"no regular-season games for {season} week {week}")
    frame = frame.sort_values(["kickoff_et", "game_id"]).reset_index(drop=True)
    cutoff = pd.to_datetime(frame["kickoff_et"], utc=True).min()
    return frame, pd.Timestamp(cutoff)


def _placeholders(games: pd.DataFrame, quarterbacks: pd.DataFrame) -> pd.DataFrame:
    """Unplayed team rows. Actual rates stay empty so they cannot train the fit."""
    rows = []
    qb = quarterbacks.set_index("game_id")
    for game in games.itertuples(index=False):
        gid = str(game.game_id)
        info = qb.loc[gid] if gid in qb.index else None
        roof = str(getattr(game, "roof", "")).lower()
        dome = 1.0 if roof in {"dome", "closed"} else 0.0
        wind = getattr(game, "wind", 0.0)
        try:
            wind = 0.0 if dome == 1.0 else float(wind)
        except (TypeError, ValueError):
            wind = 0.0
        for team, opponent, home, qb_col in (
            (game.home_team, game.away_team, 1.0, "home_qb"),
            (game.away_team, game.home_team, 0.0, "away_qb"),
        ):
            rows.append({
                "game_id": gid,
                "season": int(game.season),
                "week": int(game.week),
                "game_type": "REG",
                "kickoff_et": game.kickoff_et,
                "team": canon(team),
                "opponent": canon(opponent),
                "is_home": home,
                "dome": dome,
                "wind": wind,
                "qb_id": None if info is None else info[qb_col],
                "home_score": np.nan,
                "away_score": np.nan,
            })
    return pd.DataFrame(rows)


def _sigma_and_fit(history: pd.DataFrame, test: pd.DataFrame, cutoff):
    """Walk-forward margin and total sigma, then this week's means.

    Margin is home minus away, in points. The returned means use that sign.
    """
    rows = rows_before_cutoff(history, cutoff)
    rows = rows[rows["game_type"].astype(str).eq("REG")].sort_values(["kickoff_et", "game_id"])
    residual_m: list[float] = []
    residual_t: list[float] = []
    if ols_is_identified(len(rows)):
        for (_season, _week), group in rows.groupby(["season", "week"], sort=False):
            group_cut = group["kickoff_et"].min()
            earlier = rows_before_cutoff(rows, group_cut)
            fit = replay_week(earlier, group) if ols_is_identified(len(earlier)) else {"ok": False}
            if fit.get("ok"):
                actual_m = group["margin"].to_numpy(dtype=float)
                actual_t = group["total"].to_numpy(dtype=float)
                residual_m.extend((actual_m - np.asarray(fit["margin"], dtype=float)).tolist())
                residual_t.extend((actual_t - np.asarray(fit["total"], dtype=float)).tolist())
    sigma_m = sample_sd(np.asarray(residual_m, dtype=float))
    sigma_t = sample_sd(np.asarray(residual_t, dtype=float))
    fit_now = replay_week(rows, test) if ols_is_identified(len(rows)) else {"ok": False}
    if not fit_now.get("ok"):
        return None, None, sigma_m, sigma_t
    return (
        np.asarray(fit_now["margin"], dtype=float),
        np.asarray(fit_now["total"], dtype=float),
        sigma_m,
        sigma_t,
    )


def assert_history_covers(schedule: pd.DataFrame, plays: pd.DataFrame, cutoff) -> None:
    """Abort when a game before the cutoff has no score or no plays.

    A later week cannot pretend those games did not happen. New files belong
    in an update directory. They are not written into the locked cache.
    """
    frame = _with_kickoff(schedule)
    kicks = pd.to_datetime(frame["kickoff_et"], utc=True)
    scores = pd.to_numeric(frame["home_score"], errors="coerce") + pd.to_numeric(frame["away_score"], errors="coerce")
    earlier = frame.loc[kicks < cutoff].copy()
    missing_score = earlier.loc[~np.isfinite(scores.loc[earlier.index])]
    if len(missing_score):
        sample = missing_score["game_id"].astype(str).head(5).tolist()
        raise FreezeError(f"{len(missing_score)} games before the cutoff have no score: {sample}")
    played = set(plays["game_id"].astype(str))
    missing_plays = earlier.loc[~earlier["game_id"].astype(str).isin(played)]
    if len(missing_plays):
        sample = missing_plays["game_id"].astype(str).head(5).tolist()
        raise FreezeError(f"{len(missing_plays)} games before the cutoff have no plays: {sample}")


def apply_update(schedule: pd.DataFrame, plays: pd.DataFrame, update: Path | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Overlay a later schedule and play file. The locked cache is not written."""
    if update is None:
        return schedule, plays
    games_path = update / "games.parquet"
    plays_path = update / "pbp.parquet"
    if not games_path.exists() or not plays_path.exists():
        raise FreezeError(f"{update} needs games.parquet and pbp.parquet")
    extra_games = pd.read_parquet(games_path)
    extra_plays = pd.read_parquet(plays_path)
    extra_games, removed = strip_market_columns(extra_games)
    if removed:
        raise FreezeError(f"update schedule has market columns: {removed}")
    extra_plays, removed = strip_market_columns(extra_plays)
    if removed:
        raise FreezeError(f"update plays have market columns: {removed}")
    base = schedule[~schedule["game_id"].astype(str).isin(set(extra_games["game_id"].astype(str)))].copy()
    schedule = pd.concat([base, extra_games], ignore_index=True)
    base_plays = plays[~plays["game_id"].astype(str).isin(set(extra_plays["game_id"].astype(str)))].copy()
    plays = pd.concat([base_plays, extra_plays], ignore_index=True)
    return schedule, plays


def prior_feature_rows() -> pd.DataFrame:
    """Feature rows committed by earlier freezes. They carry no scores."""
    frames = []
    if not OUT.exists():
        return pd.DataFrame()
    for path in sorted(OUT.glob("*/feature_rows.csv")):
        frames.append(pd.read_csv(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _history_frame(features: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """Completed games only. The target week is not in this frame."""
    meta = schedule[["game_id", "kickoff_et", "game_type", "home_score", "away_score"]].copy()
    meta["game_id"] = meta["game_id"].astype(str)
    frame = features.merge(meta, on="game_id", how="inner")
    scores = pd.to_numeric(frame["home_score"], errors="coerce") + pd.to_numeric(frame["away_score"], errors="coerce")
    frame = frame[np.isfinite(scores.to_numpy(dtype=float))].copy()
    frame["total"] = scores.loc[frame.index]
    margin = pd.to_numeric(frame["home_score"], errors="coerce") - pd.to_numeric(frame["away_score"], errors="coerce")
    frame["margin"] = margin
    frame["kickoff_et"] = pd.to_datetime(frame["kickoff_et"], utc=True)
    keep = [
        "game_id", "season", "week", "kickoff_et", "game_type", "margin", "total",
        "epa_d", "pts_d", "st_d", "qb_d", "hfa", "pts_s", "epa_s", "env2", "plays_s", "qb_s",
    ]
    cleaned, _removed = strip_market_columns(frame.loc[:, keep])
    assert_no_market_columns(cleaned, "freeze history")
    return cleaned


def _fit_card(train_teams: pd.DataFrame, train_drives: pd.DataFrame, train_plays: pd.DataFrame) -> dict | None:
    """One yards-per-play-off card. Nothing is written into the P3 card directory."""
    if len(train_teams) == 0 or len(train_drives) == 0 or len(train_plays) == 0:
        return None
    pace = fit_pace(train_teams)
    clock = fit_clock(train_plays)
    lengths = fit_scrimmage_lengths(train_drives, train_plays)
    late = fit_late_table(train_drives)
    banks = _banks(train_drives)
    rates = _rates(train_drives)
    worked = train_teams.copy()
    worked["ypp_pass_hat"] = 0.0
    worked["ypp_run_hat"] = 0.0
    ypp = fit_ypp(train_teams, False)
    drive_model = fit_drive_model(train_drives, worked, False)
    if drive_model is None or clock is None or lengths is None or ypp is not None:
        return None
    return {
        "pace": pace, "ypp": ypp, "drive_model": drive_model, "banks": banks,
        "rates": rates, "clock": clock, "lengths": lengths, "late": late,
    }


def _pmf_text(samples: np.ndarray) -> str | None:
    """Space-separated masses on integers −80 through 80. Index 0 is −80."""
    pmf = empirical_pmf(samples)
    if pmf is None or len(pmf) != 161 or abs(float(pmf.sum()) - 1.0) > 1e-8:
        return None
    return " ".join(f"{value:.8f}" for value in pmf)


def ordered_projection(rows: list[dict]) -> pd.DataFrame:
    """The versioned freeze table. Every pinned game is a row, including drops."""
    frame = projection_frame(rows)
    missing = [column for column in FREEZE_COLUMNS if column not in frame.columns]
    extra = [column for column in frame.columns if column not in FREEZE_COLUMNS]
    if missing or extra:
        raise FreezeError(f"freeze columns missing {missing} extra {extra}")
    if not frame["format_version"].eq(FORMAT_VERSION).all():
        raise FreezeError(f"format_version must be {FORMAT_VERSION}")
    return frame.loc[:, list(FREEZE_COLUMNS)]


def projection_frame(rows: list[dict]) -> pd.DataFrame:
    """Tabular projections. A score, a CRPS, or a market column is refused."""
    frame = pd.DataFrame(rows)
    banned = [column for column in frame.columns if any(part in column.lower() for part in BANNED_OUTPUT)]
    if banned:
        raise FreezeError(f"projection columns are not allowed: {banned}")
    cleaned, removed = strip_market_columns(frame)
    if removed:
        raise FreezeError(f"market columns in a projection: {removed}")
    assert_no_market_columns(cleaned, "freeze projections")
    return cleaned


def _existing_freeze_ids(root: Path) -> dict[str, tuple[object, object]]:
    """game_id to (freeze timestamp, kickoff) from freeze directories already on disk."""
    found: dict[str, tuple[object, object]] = {}
    if not root.exists():
        return found
    for proj in sorted(root.glob("*/projections.csv")):
        frame = pd.read_csv(proj)
        if "game_id" not in frame.columns:
            continue
        info = proj.parent / "inputs.json"
        frozen_at = None
        if info.exists():
            frozen_at = json.loads(info.read_text()).get("freeze_timestamp_utc")
        for rec in frame.itertuples(index=False):
            gid = str(rec.game_id)
            kick = getattr(rec, "kickoff_et", None)
            found.setdefault(gid, (frozen_at, kick))
    return found


def write_freeze(
    directory: Path,
    rows: list[dict],
    features: pd.DataFrame,
    inputs: dict,
    exclusions: list[dict[str, str]] | None = None,
    search_root: Path | None = None,
) -> Path:
    """Hash the projection and the input list. Does not score or overwrite."""
    if directory.exists():
        raise FreezeError(f"freeze directory already exists: {directory.name}")
    root = search_root if search_root is not None else directory.parent
    existing = _existing_freeze_ids(root)
    clash = []
    for row in rows:
        gid = str(row["game_id"])
        if gid not in existing:
            continue
        frozen_at, kick = existing[gid]
        prior = None if frozen_at is None or kick is None else first_valid_freeze([(frozen_at, kick)])
        clash.append(gid if prior is None else f"{gid} (first valid freeze)")
    if clash:
        raise FreezeError(f"freeze record already exists for {clash[:5]}")
    directory.mkdir(parents=True, exist_ok=False)
    frame = ordered_projection(rows)
    feature_frame = projection_frame(features.to_dict(orient="records"))
    proj = directory / "projections.csv"
    feat = directory / "feature_rows.csv"
    info = directory / "inputs.json"
    excluded = directory / "exclusions.csv"
    manifest = directory / "manifest.sha256"
    frame.to_csv(proj, index=False)
    feature_frame.to_csv(feat, index=False)
    pd.DataFrame(exclusions or [], columns=["game_id", "reason"]).to_csv(excluded, index=False)
    info.write_text(json.dumps(inputs, indent=2, sort_keys=True) + "\n")
    lines = [
        f"{_sha256(proj)}  projections.csv\n",
        f"{_sha256(feat)}  feature_rows.csv\n",
        f"{_sha256(excluded)}  exclusions.csv\n",
        f"{_sha256(info)}  inputs.json\n",
    ]
    manifest.write_text("".join(lines))
    return manifest


def _load_history(season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Played history through ``season``. Derived files are read, not rewritten."""
    schedule = _schedule()
    plays = pd.concat(
        [
            pd.read_parquet(path)
            for path in sorted((DERIVED / "pbp_model").glob("*.parquet"))
            if int(path.stem) <= int(season)
        ],
        ignore_index=True,
    )
    return schedule, plays


def _stamp(cutoff) -> str:
    return pd.Timestamp(cutoff).tz_convert("America/New_York").strftime("%Y%m%dT%H%M")


def _freeze_inputs(season: int, week: int, cutoff, slate: pd.DataFrame, exclusions: list[dict[str, str]]) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "freeze_timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "margin_sign": "home_minus_away",
        "publish": "all_or_none",
        "score_from": "committed_freeze_files_on_this_branch",
        "season": int(season),
        "week": int(week),
        "cutoff_et": pd.Timestamp(cutoff).tz_convert("America/New_York").isoformat(),
        "on_or_after": None,
        "game_ids": [str(game_id) for game_id in slate["game_id"]],
        "exclusions": exclusions,
        "ypp_on": False,
        "n_sims": int(cfg.N_SIMS),
        "scored": False,
    }


def freeze_week(
    season: int,
    week: int,
    on_or_after: object | None = None,
    update: Path | None = None,
    now: object | None = None,
    prereg_path: Path | None = None,
    window: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
    out_root: Path | None = None,
    projector=None,
) -> Path:
    """Write one week's frozen totals. Raises while the prereg is a draft.

    The game ids start in the pinned window file. The slate uses each game's
    current schedule week and kickoff. ``on_or_after`` cannot add or drop games.
    ``projector`` is the synthetic path used by tests. It does not touch the cache.
    """
    del on_or_after
    if not prereg_is_locked(prereg_path):
        raise FreezeLocked("TOTALS_PREREG.md is not locked. Refusing to freeze a week.")
    current = datetime.now(UTC) if now is None else now
    if projector is not None:
        if window is None or schedule is None or out_root is None:
            raise FreezeError("synthetic freeze needs a window, schedule, and output directory")
        slate, cutoff, exclusions = assemble_slate(window, schedule, week)
        if cutoff is None or len(slate) == 0:
            raise FreezeError("no games left to freeze")
        assert_freeze_in_time(list(slate["kickoff_et"]), current)
        rows, features = projector(slate, cutoff)
        directory = Path(out_root) / f"{int(season)}_w{int(week)}_{_stamp(cutoff)}"
        for item in exclusions:
            print(f"excluded {item['game_id']} {item['reason']}", flush=True)
        return write_freeze(
            directory, rows, features, _freeze_inputs(season, week, cutoff, slate, exclusions),
            exclusions, search_root=Path(out_root),
        )
    pins = assert_frozen_inputs()
    window = load_window1() if window is None else window
    schedule, plays = _load_history(season)
    schedule, plays = apply_update(schedule, plays, update)
    schedule = _with_kickoff(schedule)
    slate, cutoff, exclusions = assemble_slate(window, schedule, week)
    if cutoff is None or len(slate) == 0:
        raise FreezeError("no games left to freeze")
    assert_freeze_in_time(list(slate["kickoff_et"]), current)
    excluded_ids = {item["game_id"] for item in exclusions}
    history_schedule = schedule[~schedule["game_id"].astype(str).isin(excluded_ids)]
    assert_history_covers(history_schedule, plays, cutoff)
    drives = build_drive_table(plays, schedule).drives
    identified = assign_quarterbacks(schedule, _depth_charts())
    kicks = pd.to_datetime(slate["kickoff_et"], utc=True)
    if (kicks < cutoff).any():
        raise FreezeError("a projected game kicks off before the cutoff")
    qbs = identified.set_index("game_id")
    featured = team_game_table(plays, drives, schedule)
    home_qb = identified[["game_id", "home_qb"]].rename(columns={"home_qb": "qb_id"})
    home_qb["is_home"] = 1.0
    away_qb = identified[["game_id", "away_qb"]].rename(columns={"away_qb": "qb_id"})
    away_qb["is_home"] = 0.0
    featured = featured.merge(
        pd.concat([home_qb, away_qb], ignore_index=True), on=["game_id", "is_home"], how="left",
    )
    featured = pd.concat([featured, _placeholders(slate, identified)], ignore_index=True)
    featured = _attach_opponent_defense(with_priors(featured, qb_game_table(plays, schedule)))
    train_teams = rows_before_cutoff(featured, cutoff)
    train_ids = set(train_teams["game_id"].astype(str))
    packed = _fit_card(
        train_teams,
        drives[drives["game_id"].astype(str).isin(train_ids)],
        plays[plays["game_id"].astype(str).isin(train_ids)],
    )
    features = pd.read_parquet(DERIVED / "features_v5.parquet")
    earlier_features = prior_feature_rows()
    if len(earlier_features):
        features = pd.concat([features, earlier_features], ignore_index=True)
        features = features.drop_duplicates("game_id", keep="first")
    history = _history_frame(features, schedule)
    v5_rows = rows_before_cutoff(_v5_rows(plays, schedule), cutoff)
    teams = sorted({canon(team) for team in list(slate["home_team"]) + list(slate["away_team"])})
    ratings, book = ratings_as_of(v5_rows, int(season), int(week), teams, cutoff, screen=False)
    test_rows = []
    for game in slate.itertuples(index=False):
        gid = str(game.game_id)
        info = qbs.loc[gid] if gid in qbs.index else None
        home_id = None if info is None else info["home_qb"]
        away_id = None if info is None else info["away_qb"]
        values = _feature_values(
            ratings, book, canon(game.home_team), canon(game.away_team), home_id, away_id,
            str(getattr(game, "location", "")) == "Neutral",
        )
        values["game_id"] = gid
        values["season"] = int(season)
        values["week"] = int(week)
        test_rows.append(values)
    test = pd.DataFrame(test_rows)
    margins, totals, sigma_m, sigma_t = _sigma_and_fit(history, test, cutoff)
    rows = []
    for index, game in enumerate(slate.itertuples(index=False)):
        gid = str(game.game_id)
        info = qbs.loc[gid] if gid in qbs.index else None
        home_id = None if info is None else info["home_qb"]
        away_id = None if info is None else info["away_qb"]
        home = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(1.0))]
        away = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(0.0))]
        sim_margin = np.nan
        sim_total = np.nan
        margin_pmf = ""
        total_pmf = ""
        v5_margin = np.nan if margins is None else float(margins[index])
        v5_total = np.nan if totals is None else float(totals[index])
        model_ok = (
            packed is not None and margins is not None and totals is not None
            and sigma_m is not None and sigma_t is not None
            and np.isfinite(v5_margin) and np.isfinite(v5_total)
            and len(home) == 1 and len(away) == 1
        )
        reason = game_dropped(
            home_id if isinstance(home_id, str) else None,
            away_id if isinstance(away_id, str) else None,
            model_ok, model_ok,
        )
        if reason is None:
            try:
                card = _card(
                    home.iloc[0], away.iloc[0], packed["pace"], packed["ypp"], packed["drive_model"],
                    packed["banks"], packed["rates"], False, packed["clock"], packed["lengths"],
                    packed["late"], playoff=False,
                )
                draw = simulate_game(card, cfg.N_SIMS, np.random.default_rng(sim_seed(gid)))
                sim_margin = float(np.mean(draw["margin"]))
                sim_total = float(np.mean(draw["total"]))
                margin_text = _pmf_text(draw["margin"])
                total_text = _pmf_text(draw["total"])
                if margin_text is None or total_text is None or not np.isfinite(sim_margin) or not np.isfinite(sim_total):
                    reason = "model_failure"
                else:
                    margin_pmf = margin_text
                    total_pmf = total_text
            except (RuntimeError, ValueError, KeyError, IndexError):
                reason = "model_failure"
        rows.append({
            "game_id": gid,
            "season": int(season),
            "week": int(week),
            "kickoff_et": pd.Timestamp(game.kickoff_et).isoformat(),
            "home_team": canon(game.home_team),
            "away_team": canon(game.away_team),
            "home_qb": home_id if isinstance(home_id, str) else "",
            "away_qb": away_id if isinstance(away_id, str) else "",
            "format_version": FORMAT_VERSION,
            "drop_reason": reason or "",
            "sim_margin_mean": sim_margin,
            "sim_total_mean": sim_total,
            "sim_margin_pmf": margin_pmf,
            "sim_total_pmf": total_pmf,
            "v5_margin": v5_margin,
            "v5_total": v5_total,
            "v5_sigma_margin": float(sigma_m) if sigma_m is not None else np.nan,
            "v5_sigma_total": float(sigma_t) if sigma_t is not None else np.nan,
            "n_sims": int(cfg.N_SIMS),
            "seed": int(sim_seed(gid)),
            "ypp_on": False,
        })
    directory = OUT / f"{int(season)}_w{int(week)}_{_stamp(cutoff)}"
    inputs = _freeze_inputs(season, week, cutoff, slate, exclusions)
    inputs["pins"] = pins
    inputs["schedule_sha256"] = _sha256(LOCKED_GAMES)
    inputs["features_sha256"] = _sha256(DERIVED / "features_v5.parquet")
    inputs["update"] = None if update is None else str(update)
    for item in exclusions:
        print(f"excluded {item['game_id']} {item['reason']}", flush=True)
    manifest = write_freeze(directory, rows, test, inputs, exclusions, search_root=OUT)
    print(f"froze {len(rows)} games at {directory}", flush=True)
    print(manifest.read_text(), flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze one week of total projections. Does not score.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--on-or-after", default=None, help="Eastern date. Drops earlier kickoffs in the week.")
    parser.add_argument("--update", type=Path, default=None, help="Later games.parquet and pbp.parquet. Not the locked cache.")
    args = parser.parse_args()
    freeze_week(args.season, args.week, args.on_or_after, args.update)


if __name__ == "__main__":
    main()
