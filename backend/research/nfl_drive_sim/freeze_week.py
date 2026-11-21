"""Freeze one week's total projections. This file does not score a game.

It aborts unless ``TOTALS_PREREG.md`` says ``Status: locked``. Output goes
under ``outputs/totals/freezes/``. The P3 files and the cache are read only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
SIM_CONFIG_SHA256 = "f60cd566e06e87f66d000f8ec8f3afc4bd4e3b8ed91711374c005fe0e3be7ecb"
P3_PREREG_SHA256 = "bb114c35ab5158a8b19223daf61af2213f783cee40fe6da4f160b697a2bab506"
BANNED_OUTPUT = ("crps", "actual", "spread", "odds", "moneyline", "price", "kalshi")


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


def _sigma_and_fit(history: pd.DataFrame, test: pd.DataFrame, cutoff) -> tuple[np.ndarray | None, float | None]:
    """Walk-forward total sigma from games strictly before ``cutoff``, then the new week."""
    rows = rows_before_cutoff(history, cutoff)
    rows = rows[rows["game_type"].astype(str).eq("REG")].sort_values(["kickoff_et", "game_id"])
    residuals: list[float] = []
    if ols_is_identified(len(rows)):
        for (_season, _week), group in rows.groupby(["season", "week"], sort=False):
            group_cut = group["kickoff_et"].min()
            earlier = rows_before_cutoff(rows, group_cut)
            fit = replay_week(earlier, group) if ols_is_identified(len(earlier)) else {"ok": False}
            if fit.get("ok"):
                actual = group["total"].to_numpy(dtype=float)
                residuals.extend((actual - np.asarray(fit["total"], dtype=float)).tolist())
    sigma_now = sample_sd(np.asarray(residuals, dtype=float))
    fit_now = replay_week(rows, test) if ols_is_identified(len(rows)) else {"ok": False}
    if not fit_now.get("ok"):
        return None, sigma_now
    return np.asarray(fit_now["total"], dtype=float), sigma_now


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


def write_freeze(directory: Path, rows: list[dict], features: pd.DataFrame, inputs: dict) -> Path:
    """Hash the projection and the input list. Does not score."""
    directory.mkdir(parents=True, exist_ok=True)
    frame = projection_frame(rows)
    feature_frame = projection_frame(features.to_dict(orient="records"))
    proj = directory / "projections.csv"
    feat = directory / "feature_rows.csv"
    info = directory / "inputs.json"
    manifest = directory / "manifest.sha256"
    frame.to_csv(proj, index=False)
    feature_frame.to_csv(feat, index=False)
    info.write_text(json.dumps(inputs, indent=2, sort_keys=True) + "\n")
    lines = [
        f"{_sha256(proj)}  projections.csv\n",
        f"{_sha256(feat)}  feature_rows.csv\n",
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


def freeze_week(season: int, week: int, on_or_after: object | None = None, update: Path | None = None) -> Path:
    """Write one week's frozen totals. Raises while the prereg is a draft."""
    if not prereg_is_locked():
        raise FreezeLocked("TOTALS_PREREG.md is not locked. Refusing to freeze a week.")
    pins = assert_frozen_inputs()
    schedule, plays = _load_history(season)
    schedule, plays = apply_update(schedule, plays, update)
    schedule = _with_kickoff(schedule)
    slate, cutoff = select_week_games(schedule, season, week, on_or_after)
    assert_history_covers(schedule, plays, cutoff)
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
    totals, sigma = _sigma_and_fit(history, test, cutoff)
    rows = []
    for index, game in enumerate(slate.itertuples(index=False)):
        gid = str(game.game_id)
        info = qbs.loc[gid] if gid in qbs.index else None
        home_id = None if info is None else info["home_qb"]
        away_id = None if info is None else info["away_qb"]
        home = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(1.0))]
        away = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(0.0))]
        sim_mean = np.nan
        pmf_text = ""
        v5_total = np.nan if totals is None else float(totals[index])
        model_ok = (
            packed is not None and totals is not None and sigma is not None
            and np.isfinite(v5_total) and len(home) == 1 and len(away) == 1
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
                pmf = empirical_pmf(draw["total"])
                sim_mean = float(np.mean(draw["total"]))
                if pmf is None or not np.isfinite(sim_mean) or abs(float(pmf.sum()) - 1.0) > 1e-8:
                    reason = "model_failure"
                else:
                    pmf_text = " ".join(f"{value:.8f}" for value in pmf)
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
            "drop_reason": reason or "",
            "sim_total_mean": sim_mean,
            "sim_total_pmf": pmf_text,
            "v5_total": v5_total,
            "v5_sigma_total": float(sigma) if sigma is not None else np.nan,
            "n_sims": int(cfg.N_SIMS),
            "seed": int(sim_seed(gid)),
            "ypp_on": False,
        })
    stamp = pd.Timestamp(cutoff).tz_convert("America/New_York").strftime("%Y%m%dT%H%M")
    directory = OUT / f"{int(season)}_w{int(week)}_{stamp}"
    inputs = {
        "season": int(season),
        "week": int(week),
        "cutoff_et": pd.Timestamp(cutoff).tz_convert("America/New_York").isoformat(),
        "on_or_after": None if on_or_after is None else str(on_or_after),
        "game_ids": [str(game_id) for game_id in slate["game_id"]],
        "ypp_on": False,
        "n_sims": int(cfg.N_SIMS),
        "pins": pins,
        "schedule_sha256": _sha256(LOCKED_GAMES),
        "features_sha256": _sha256(DERIVED / "features_v5.parquet"),
        "update": None if update is None else str(update),
        "scored": False,
    }
    manifest = write_freeze(directory, rows, test, inputs)
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
