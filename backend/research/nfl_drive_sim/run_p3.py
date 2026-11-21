"""P3 season runner.

The real slate is frozen in ``outputs/p3/game_set.csv``. ``load_game_set``
aborts unless that primary set has 2,534 games. ``run_real`` refuses to
score the cache. Tests call ``score_slate`` on a synthetic fixture only.

The cache is not refreshed here. Parquet hashes live in the manifest stub.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .models import crps_ensemble
from .pregame import pregame_quarterback_table
from .v5_replay import (
    DROP_RATE_LIMIT,
    MARGIN_COLUMNS,
    TOTAL_COLUMNS,
    bootstrap_rng,
    discrete_crps,
    drop_rate_voids,
    empirical_pmf,
    game_dropped,
    normal_integer_pmf,
    ols_is_identified,
    replay_week,
    resolve_qb,
    sample_sd,
    sim_seed,
    webb_mean_interval,
)

PRIMARY_N = 2534
LOCKED_GAME_SET_SHA256 = "123539602360fea7e03b5d42cb0317da2902f5e501bb47d452c7530e114194c6"
REPORT_NAME = "p3_report.md"
PER_GAME_NAME = "per_game_d.csv"
ATS_SENTENCE = (
    "P3 is not a fair ATS or key-number comparison against v5 until a "
    "fourth-quarter endgame model is accepted on a later season."
)
FLAG_MARGIN = 3.0
FLAG_TOTAL = 4.0
FLAG_WP = 0.10


class GameSetError(RuntimeError):
    """The frozen primary set is not the locked 2,534 games."""


class RealRunLocked(RuntimeError):
    """QA has not cleared a real-season run of this runner."""


def load_game_set(path: Path | str) -> pd.DataFrame:
    """Read the frozen slate. Abort unless the file hash and the count match."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LOCKED_GAME_SET_SHA256:
        raise GameSetError(f"game set hash {digest} != locked {LOCKED_GAME_SET_SHA256}")
    frame = pd.read_csv(path)
    primary = frame.loc[frame["role"].eq("primary"), "game_id"].astype(str)
    if int(primary.shape[0]) != PRIMARY_N:
        raise GameSetError(f"primary set is {int(primary.shape[0])}, expected {PRIMARY_N}")
    if primary.duplicated().any():
        raise GameSetError("duplicate primary game_id")
    if primary.str.startswith("2018_").any():
        raise GameSetError("2018 game in the primary set")
    return frame


def rows_before_cutoff(frame: pd.DataFrame, cutoff) -> pd.DataFrame:
    """Rows whose kickoff is strictly before ``cutoff``. An equal kickoff is out."""
    kicks = pd.to_datetime(frame["kickoff_et"], utc=True)
    limit = pd.Timestamp(cutoff)
    if limit.tzinfo is None:
        limit = limit.tz_localize("UTC")
    else:
        limit = limit.tz_convert("UTC")
    return frame.loc[kicks < limit].copy()


def assign_quarterbacks(games: pd.DataFrame, depth_charts: pd.DataFrame) -> pd.DataFrame:
    """P1 depth-chart QB1, then the prior charted start strictly before kickoff.

    The prior start is that team's depth-chart quarterback on an earlier
    completed game. It is not the in-game passer.
    """
    out = games.copy()
    table = pregame_quarterback_table(depth_charts, out)
    home = table[table["side"].eq("home")][["game_id", "qb_id"]].rename(columns={"qb_id": "chart_home_qb"})
    away = table[table["side"].eq("away")][["game_id", "qb_id"]].rename(columns={"qb_id": "chart_away_qb"})
    out = out.merge(home, on="game_id", how="left").merge(away, on="game_id", how="left")
    kicks = pd.to_datetime(out["kickoff_et"], utc=True)
    order = kicks.argsort(kind="mergesort")
    last: dict[str, list[tuple[object, str]]] = {}
    home_qb = [None] * len(out)
    away_qb = [None] * len(out)
    home_how = [None] * len(out)
    away_how = [None] * len(out)
    positions = list(order)
    for pos in positions:
        row = out.iloc[int(pos)]
        kick = kicks.iloc[int(pos)]
        for team_col, chart_col, dest, how in (
            ("home_team", "chart_home_qb", home_qb, home_how),
            ("away_team", "chart_away_qb", away_qb, away_how),
        ):
            team = str(row[team_col])
            prior = [(stamp, qb) for stamp, qb in last.get(team, []) if stamp < kick]
            chosen, source = resolve_qb(row[chart_col], prior)
            dest[int(pos)] = chosen
            how[int(pos)] = source
        for team, chosen in ((str(row["home_team"]), home_qb[int(pos)]), (str(row["away_team"]), away_qb[int(pos)])):
            if isinstance(chosen, str) and chosen:
                last.setdefault(team, []).append((kick, chosen))
    out["home_qb"] = home_qb
    out["away_qb"] = away_qb
    out["home_qb_source"] = home_how
    out["away_qb_source"] = away_how
    return out


def run_real() -> None:
    """Refuse the cache. A real run waits for QA's spot check."""
    raise RealRunLocked(
        "P3 real run is locked. Do not score the cache until QA clears this runner."
    )


def _kick(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def _design_rows(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["game_id", "season", "week", "kickoff_et", "game_type", "margin", "total", *MARGIN_COLUMNS, *TOTAL_COLUMNS]
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise GameSetError(f"feature rows missing {missing}")
    out = frame.loc[:, columns].copy()
    out["kickoff_et"] = _kick(out["kickoff_et"])
    return out.sort_values(["kickoff_et", "game_id"]).reset_index(drop=True)


def walk_forward_predictions(features: pd.DataFrame) -> dict[str, tuple[float, float, float, float]]:
    """No-market weekly refit. Residuals are regular-season games only."""
    rows = _design_rows(features)
    residuals_m: list[float] = []
    residuals_t: list[float] = []
    predicted: dict[str, tuple[float, float, float, float]] = {}
    for (_, _), group in rows.groupby(["season", "week"], sort=False):
        cutoff = group["kickoff_et"].min()
        earlier = rows_before_cutoff(rows, cutoff)
        train = earlier[earlier["game_type"].astype(str).eq("REG")]
        sigma_m = sample_sd(np.asarray(residuals_m, dtype=float))
        sigma_t = sample_sd(np.asarray(residuals_t, dtype=float))
        if not ols_is_identified(len(train)):
            continue
        fit = replay_week(train, group)
        if not fit["ok"]:
            continue
        if sigma_m is not None and sigma_t is not None:
            for index, game_id in enumerate(group["game_id"].astype(str)):
                predicted[game_id] = (
                    float(fit["margin"][index]),
                    float(fit["total"][index]),
                    float(sigma_m),
                    float(sigma_t),
                )
        regular = group["game_type"].astype(str).eq("REG")
        if regular.any():
            actual_m = group.loc[regular, "margin"].to_numpy(dtype=float)
            actual_t = group.loc[regular, "total"].to_numpy(dtype=float)
            got_m = np.asarray(fit["margin"], dtype=float)[regular.to_numpy()]
            got_t = np.asarray(fit["total"], dtype=float)[regular.to_numpy()]
            residuals_m.extend((actual_m - got_m).tolist())
            residuals_t.extend((actual_t - got_t).tolist())
    return predicted


def _crps_normal(mean: float, sigma: float, actual: float) -> float:
    """Closed-form normal CRPS. Sensitivity only; it does not change the call."""
    if not np.isfinite(sigma) or sigma <= 0:
        return abs(actual - mean)
    z = (actual - mean) / sigma
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return float(sigma * (z * (2.0 * cdf - 1.0) + 2.0 * pdf - 1.0 / math.sqrt(math.pi)))


def _finite_mean(values: np.ndarray) -> float:
    sample = np.asarray(values, dtype=float)
    sample = sample[np.isfinite(sample)]
    if len(sample) == 0:
        return float("nan")
    return float(sample.mean())


def _p_home_from_pmf(pmf: np.ndarray | None) -> float | None:
    if pmf is None:
        return None
    # Grid starts at -80. Strictly positive margins begin at index 81.
    return float(pmf[81:].sum())


def _score_one(game: pd.Series, pred: tuple[float, float, float, float] | None, sim: dict, ypp_on: bool) -> dict:
    actual_m = float(game["home_score"]) - float(game["away_score"])
    actual_t = float(game["home_score"]) + float(game["away_score"])
    home_qb, _home_how = resolve_qb(game.get("home_qb"), [])
    away_qb, _away_how = resolve_qb(game.get("away_qb"), [])
    model_ok = pred is not None and sim is not None
    reason = game_dropped(home_qb, away_qb, model_ok, model_ok)
    row = {
        "game_id": str(game["game_id"]),
        "season": int(game["season"]),
        "week": int(game["week"]),
        "kickoff_et": str(game["kickoff_et"]),
        "role": str(game["role"]),
        "regular_season": str(game["role"]) == "primary",
        "dev_touched_2018": int(game["season"]) == 2018,
        "ypp_on": bool(ypp_on),
        "drop_reason": reason,
        "actual_margin": actual_m,
        "actual_total": actual_t,
        "actual_home_win": float(actual_m > 0),
    }
    if reason is not None or pred is None or sim is None:
        return row
    v5_m, v5_t, sigma_m, sigma_t = pred
    v5_margin_pmf = normal_integer_pmf(v5_m, sigma_m)
    v5_total_pmf = normal_integer_pmf(v5_t, sigma_t)
    sim_margin_pmf = empirical_pmf(sim["margin"])
    sim_total_pmf = empirical_pmf(sim["total"])
    sim_m = float(np.mean(sim["margin"]))
    sim_t = float(np.mean(sim["total"]))
    sim_p = float(np.mean(np.asarray(sim["margin"]) > 0))
    v5_p = _p_home_from_pmf(v5_margin_pmf)
    sim_crps_m = discrete_crps(sim_margin_pmf, actual_m)
    v5_crps_m = discrete_crps(v5_margin_pmf, actual_m)
    sim_crps_t = discrete_crps(sim_total_pmf, actual_t)
    v5_crps_t = discrete_crps(v5_total_pmf, actual_t)
    if None in {v5_p, sim_crps_m, v5_crps_m, sim_crps_t, v5_crps_t}:
        row["drop_reason"] = "model_failure"
        return row
    plays = float(np.mean(sim["home_plays"] + sim["away_plays"]))
    seconds = float(np.mean(sim["home_scr_sec"] + sim["away_scr_sec"]))
    row.update({
        "sim_margin_mean": sim_m,
        "v5_margin": v5_m,
        "sim_total_mean": sim_t,
        "v5_total": v5_t,
        "sim_p_home": sim_p,
        "v5_p_home": v5_p,
        "d_margin_crps": sim_crps_m - v5_crps_m,
        "d_total_crps": sim_crps_t - v5_crps_t,
        "d_margin_mae": abs(sim_m - actual_m) - abs(v5_m - actual_m),
        "d_total_mae": abs(sim_t - actual_t) - abs(v5_t - actual_t),
        "d_brier": (sim_p - row["actual_home_win"]) ** 2 - (v5_p - row["actual_home_win"]) ** 2,
        "sim_margin_crps": sim_crps_m,
        "sim_total_crps": sim_crps_t,
        "sim_margin_crps_sample": crps_ensemble(sim["margin"], actual_m),
        "v5_margin_crps_normal": _crps_normal(v5_m, sigma_m, actual_m),
        "sim_total_crps_sample": crps_ensemble(sim["total"], actual_t),
        "v5_total_crps_normal": _crps_normal(v5_t, sigma_t, actual_t),
        "sim_p_margin_3": float(np.mean(np.rint(np.abs(sim["margin"])) == 3)),
        "sim_p_margin_7": float(np.mean(np.rint(np.abs(sim["margin"])) == 7)),
        "flag_margin": float(abs(sim_m - v5_m) >= FLAG_MARGIN),
        "flag_total": float(abs(sim_t - v5_t) >= FLAG_TOTAL),
        "flag_wp": float(abs(sim_p - v5_p) >= FLAG_WP),
        "margin_at_5min": _finite_mean(sim["margin_at_5min"]),
        "sim_ot": float(np.mean(sim["ot"])),
        "sim_plays": plays,
        "sim_seconds_per_play": seconds / plays if plays else np.nan,
        "actual_seconds_per_play": (
            float(game["actual_seconds_per_play"])
            if "actual_seconds_per_play" in game and pd.notna(game["actual_seconds_per_play"])
            else np.nan
        ),
        "sim_drives": float(np.mean(sim["home_drives"] + sim["away_drives"])),
    })
    if np.isfinite(row["actual_seconds_per_play"]) and np.isfinite(row["sim_seconds_per_play"]):
        row["sec_per_play_bias"] = row["sim_seconds_per_play"] - row["actual_seconds_per_play"]
    return row


def _call(values: np.ndarray, clusters: np.ndarray, reps: int) -> str:
    interval = webb_mean_interval(values, clusters, bootstrap_rng(), reps=reps)
    if interval is None:
        return "INCONCLUSIVE"
    mean, low, high = interval
    if mean < 0 and high < 0:
        return "SUCCESS"
    if low > 0:
        return "FAILURE"
    return "INCONCLUSIVE"


def _ypp_keep(frame: pd.DataFrame, reps: int) -> bool:
    on = frame[frame["ypp_on"].eq(True) & frame["drop_reason"].isna()]
    off = frame[frame["ypp_on"].eq(False) & frame["drop_reason"].isna()]
    both = on.merge(off, on="game_id", suffixes=("_on", "_off"))
    if both.empty:
        return False
    margin_d = both["sim_margin_crps_on"] - both["sim_margin_crps_off"]
    total_d = both["sim_total_crps_on"] - both["sim_total_crps_off"]
    clusters = both["season_on"].astype(str) + "-" + both["week_on"].astype(str)
    margin_iv = webb_mean_interval(margin_d.to_numpy(), clusters.to_numpy(), bootstrap_rng(), reps=reps)
    total_iv = webb_mean_interval(total_d.to_numpy(), clusters.to_numpy(), bootstrap_rng(), reps=reps)
    if margin_iv is None or total_iv is None:
        return False
    return margin_iv[0] < 0 and margin_iv[2] < 0 and total_iv[2] <= 0.05


def score_slate(
    games: pd.DataFrame,
    features: pd.DataFrame,
    simulate,
    *,
    n_sims: int | None = None,
    ypp_values: tuple[bool, ...] = (True, False),
    card_for=None,
    reps: int = 999,
) -> dict:
    """Score ``games`` from synthetic or prebuilt rows. Does not read the cache."""
    sims = cfg.N_SIMS if n_sims is None else int(n_sims)
    predicted = walk_forward_predictions(features)
    rows = []
    ordered = games.copy()
    ordered["kickoff_et"] = _kick(ordered["kickoff_et"])
    ordered = ordered.sort_values(["kickoff_et", "game_id"])
    for ypp_on in ypp_values:
        for game in ordered.itertuples(index=False):
            record = game._asdict() if hasattr(game, "_asdict") else None
            series = ordered.loc[ordered["game_id"].astype(str).eq(str(game.game_id))].iloc[0]
            pred = predicted.get(str(game.game_id))
            sim = None
            if pred is not None and card_for is not None and series.get("home_qb") and series.get("away_qb"):
                card = card_for(series, ypp_on)
                rng = np.random.default_rng(sim_seed(str(game.game_id)))
                sim = simulate(card, sims, rng)
            rows.append(_score_one(series, pred, sim, ypp_on))
            del record
    frame = pd.DataFrame(rows)
    primary = frame[frame["role"].eq("primary")]
    n_completed = int(primary.drop_duplicates("game_id").shape[0])
    dropped_ids = set(primary.loc[primary["drop_reason"].notna(), "game_id"].astype(str))
    # A game dropped on either arm is dropped from both.
    frame.loc[frame["game_id"].astype(str).isin(dropped_ids), "drop_reason"] = frame.loc[
        frame["game_id"].astype(str).isin(dropped_ids), "drop_reason"
    ].fillna("model_failure")
    n_dropped = len(dropped_ids)
    void = drop_rate_voids(n_dropped, n_completed) if n_completed else True
    kept = frame[frame["role"].eq("primary") & frame["drop_reason"].isna()]
    keep_layer = _ypp_keep(kept, reps) if not void else False
    chosen = kept[kept["ypp_on"].eq(keep_layer)]
    if void or chosen.empty:
        decision = "VOID" if void else "INCONCLUSIVE"
    else:
        clusters = chosen["season"].astype(str) + "-" + chosen["week"].astype(str)
        decision = _call(chosen["d_margin_crps"].to_numpy(dtype=float), clusters.to_numpy(), reps)
    counts = {
        "missing_qb": int(primary.loc[primary["drop_reason"].eq("missing_qb"), "game_id"].nunique()),
        "model_failure": int(primary.loc[primary["drop_reason"].eq("model_failure"), "game_id"].nunique()),
    }
    return {
        "games": frame,
        "decision": decision,
        "void": void or (n_dropped / n_completed > DROP_RATE_LIMIT if n_completed else True),
        "ypp_keep": keep_layer,
        "drops": counts,
        "n_primary": n_completed,
        "n_dropped": n_dropped,
    }


def render_report(result: dict) -> str:
    """The locked report. The first paragraph is the ATS wording rule."""
    games = result["games"]
    primary = games[games["role"].eq("primary") & games["drop_reason"].isna()]
    lines = [
        "# P3 report",
        "",
        ATS_SENTENCE,
        "",
        f"Primary decision: {result['decision']}.",
        f"Yards-per-play layer kept: {result['ypp_keep']}.",
        f"Primary games: {result['n_primary']}. Dropped: {result['n_dropped']}.",
        f"Drops by reason: {result['drops']}.",
        "Secondary scores are a Holm family. They do not flip the primary call.",
        "Playoffs are descriptive. 2018 cannot choose the layer.",
        "",
    ]
    if not primary.empty and "d_margin_crps" in primary.columns:
        chosen = primary[primary["ypp_on"].eq(result["ypp_keep"])]
        if not chosen.empty:
            lines.append(f"Mean margin-CRPS d: {float(chosen['d_margin_crps'].mean()):.6f}.")
            lines.append(f"Margin-3 share, actual: {float((chosen['actual_margin'].abs() == 3).mean()):.3f}.")
            lines.append(f"Margin-7 share, actual: {float((chosen['actual_margin'].abs() == 7).mean()):.3f}.")
            tied = chosen["actual_margin"].eq(0).mean()
            lines.append(f"Regulation-tie rate, actual: {float(tied):.3f}.")
            lines.append(f"Overtime-entry rate, sim: {float(chosen['sim_ot'].mean()):.3f}.")
            lines.append(f"Seconds per play, sim mean: {float(chosen['sim_seconds_per_play'].mean()):.3f}.")
            lines.append(f"Margin at 5:00, sim mean: {float(chosen['margin_at_5min'].mean()):.3f}.")
            lines.append(f"Flag rate, margin: {float(chosen['flag_margin'].mean()):.3f}.")
            lines.append(f"Flag rate, total: {float(chosen['flag_total'].mean()):.3f}.")
            lines.append(f"Flag rate, home-win probability: {float(chosen['flag_wp'].mean()):.3f}.")
    lines.extend([
        "",
        "The game-average pace oracle is a handicapped benchmark.",
        "A gap whose interval covers zero is not a win for the predicted pace.",
        "Continuous CRPS is a sensitivity check. It does not change the primary call.",
        "",
    ])
    lines.extend(result.get("notes") or [])
    if result.get("notes"):
        lines.append("")
    return "\n".join(lines)


def merge_manifest(path: Path, updates: list[tuple[str, str]]) -> None:
    """Add or replace named hashes. Every other line stays."""
    order: list[str] = []
    current: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "  " not in line:
                continue
            digest, name = line.split("  ", 1)
            if name not in current:
                order.append(name)
            current[name] = digest
    for digest, name in updates:
        if name not in current:
            order.append(name)
        current[name] = digest
    path.write_text("".join(f"{current[name]}  {name}\n" for name in order))


def write_results(directory: Path | str, result: dict) -> None:
    """Write the per-game file and the named report, and merge their hashes."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    game_path = out / PER_GAME_NAME
    report_path = out / REPORT_NAME
    result["games"].to_csv(game_path, index=False)
    report_path.write_text(render_report(result))
    updates = []
    for path in (game_path, report_path):
        updates.append((hashlib.sha256(path.read_bytes()).hexdigest(), path.name))
    merge_manifest(out / "manifest.sha256", updates)


def main() -> None:
    run_real()
