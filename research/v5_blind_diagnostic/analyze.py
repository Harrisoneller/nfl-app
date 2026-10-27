"""Diagnostic: market-blind v5 versus v6 on the PR #32 games.

This does not change a model, a serving path, or PR #32. v5-blind is
qb-ridge-v5 restricted to an explicit allowlist. Its QB term is the
depth-chart starter 24 hours before kickoff, not the QB who played.
Coefficients are refit with the PR #32 walk-forward. Each remaining
component is then dropped one at a time. v6 is the walk-forward design
shipped on ``prod/v6-blind-main`` (the #32 comparison), not a new search.

    NFLVERSE_DIR=/path/to/nflverse python research/v5_blind_diagnostic/analyze.py

Train: seasons >= 2015 and < the test season, every game type, features from
games strictly before that week. Score: 2016–2025 REG games with a result
and a closing spread, the same intersection the #32 head-to-head used.
Closing lines grade ATS. They are not design columns for v5-blind or v6.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("NFLVERSE_DIR", str(ROOT / "data" / "nflverse")))
V6_REF = os.environ.get("V6_REF", "prod/v6-blind-main")
OUT_JSON = HERE / "results.json"
OUT_CSV = HERE / "per_game.csv"
SEED = 20261026
N_BOOT = 10000
SIGMA = 13.5
# v5-blind may use only these columns. Anything else, including a market
# column, is rejected. Ablations use a subset of this list.
V5_BLIND_ALLOW = ("epa_d", "pts_d", "st_d", "qb_d", "hfa")
V5_BLIND = list(V5_BLIND_ALLOW)
# Full v5 is the PR #32 reference: actual starter plus the market prior.
V5_FULL = ["mkt_d", "epa_d", "pts_d", "st_d", "qb_d_actual", "hfa"]
V6_ALLOW = ("pass_comp", "rush_comp", "sr_comp", "hfa")
V6_FULL = ["pass_comp", "rush_comp", "sr_comp", "hfa"]
PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team", "epa",
    "pass", "rush", "qb_kneel", "qb_spike", "wp", "qtr", "score_differential",
    "special_teams_play", "qb_dropback", "passer_player_id",
]
ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR",
    "OAK": "LV", "SD": "LAC", "STL": "LAR", "HST": "HOU", "BLT": "BAL", "CLV": "CLE",
}
def canonical_team(team: str | None) -> str | None:
    if not isinstance(team, str) or not team.strip():
        return None
    token = team.strip().upper()
    return ALIASES.get(token, token)


def require_allowlist(columns, allow: tuple[str, ...]) -> None:
    """Reject a design that is not an explicit subset of the permitted columns."""
    allowed = set(allow)
    extra = [str(c) for c in columns if str(c) not in allowed]
    if extra or len(list(columns)) != len(set(columns)):
        raise ValueError(
            f"design columns {list(columns)} are not a subset of the allowlist {list(allow)}"
        )


def _self_check() -> None:
    try:
        require_allowlist(["epa_d", "mkt_d", "spread_line"], V5_BLIND_ALLOW)
    except ValueError:
        pass
    else:
        raise RuntimeError("the allowlist did not reject a market column")
    require_allowlist(V5_BLIND, V5_BLIND_ALLOW)
    require_allowlist(V6_FULL, V6_ALLOW)


def crps_normal(y: np.ndarray, mu: np.ndarray, sigma: float = SIGMA) -> np.ndarray:
    z = (np.asarray(y, dtype=float) - np.asarray(mu, dtype=float)) / sigma
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    cdf = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    return sigma * (z * (2.0 * cdf - 1.0) + 2.0 * pdf - 1.0 / math.sqrt(math.pi))


def covers(pred: np.ndarray, result: np.ndarray, close: np.ndarray) -> np.ndarray:
    """1 if the side the model likes beat the close. NaN on a push or a pick-em tie."""
    push = np.isclose(result, close)
    decided = ~push & ~np.isclose(pred, close)
    home = pred > close
    home_covers = result > close
    won = (home & home_covers) | (~home & ~home_covers)
    return np.where(decided, won.astype(float), np.nan)


def bootstrap_mean(diff: np.ndarray, rng: np.random.Generator) -> dict:
    finite = np.asarray(diff, dtype=float)
    finite = finite[np.isfinite(finite)]
    n = int(finite.size)
    if n == 0:
        return {"n": 0, "diff": None, "ci95": [None, None]}
    point = float(finite.mean())
    draws = rng.integers(0, n, size=(N_BOOT, n))
    means = finite[draws].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return {"n": n, "diff": point, "ci95": [float(lo), float(hi)]}


def fresh_rng() -> np.random.Generator:
    return np.random.default_rng(SEED)


def git_show(spec: str) -> bytes:
    return subprocess.check_output(["git", "show", spec], cwd=ROOT)


def load_main_v5():
    """qb-ridge-v5 as it is on main, including the market prior."""
    src = git_show("main:backend/app/services/team_ratings_v5.py")
    path = Path("/tmp/team_ratings_v5_main.py")
    path.write_bytes(src)
    name = "team_ratings_v5_main"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if "mkt_d" not in module.MARGIN_COEFS:
        raise RuntimeError("main team_ratings_v5 has no mkt_d; the reference model changed")
    return module


def v5_feature_frame(v5, starters: dict) -> pd.DataFrame:
    sched = pd.read_csv(DATA / "games.csv", low_memory=False)
    sched = sched[sched["season"] >= 2014].copy()
    for col in ("home_team", "away_team"):
        sched[col] = sched[col].map(canonical_team)
    frames = []
    for season in range(2014, 2026):
        path = DATA / "pbp" / f"play_by_play_{season}.parquet"
        if not path.exists():
            continue
        pbp = pd.read_parquet(path, columns=PBP_COLS)
        rows = v5.team_game_rows(pbp, season, team_map=canonical_team)
        frames.append(rows)
        print(f"rows {season}: {len(rows)}", flush=True)
    rows = v5.attach_points(pd.concat(frames, ignore_index=True), sched)
    teams = sorted(set(rows["posteam"].dropna()))
    feats = []
    both_chart = 0
    both_match_actual = 0
    n_seen = 0
    for season in range(2015, 2026):
        weeks = sorted(int(w) for w in sched.loc[sched["season"] == season, "week"].unique())
        for week in weeks:
            wm = v5.build_week_model(rows, sched, season, int(week), teams)
            slate = sched[(sched["season"] == season) & (sched["week"] == week)]
            for g in slate.itertuples(index=False):
                if not (wm.has_team(g.home_team) and wm.has_team(g.away_team)):
                    continue
                hq = g.home_qb_id if isinstance(getattr(g, "home_qb_id", None), str) else None
                aq = g.away_qb_id if isinstance(getattr(g, "away_qb_id", None), str) else None
                # The actual starter is the PR #32 reference. It is not used in v5-blind.
                f = wm.features(g.home_team, g.away_team, hq, aq)
                f["qb_d_actual"] = f["qb_d"]
                hq24 = starters.get((int(season), int(week), g.home_team))
                aq24 = starters.get((int(season), int(week), g.away_team))
                hq24 = hq24 if isinstance(hq24, str) and hq24 else None
                aq24 = aq24 if isinstance(aq24, str) and aq24 else None
                # Missing chart: 0, not last week's starter and not the QB who played.
                home_d = float(wm.qb.delta(g.home_team, hq24)) if hq24 else 0.0
                away_d = float(wm.qb.delta(g.away_team, aq24)) if aq24 else 0.0
                f["qb_d"] = home_d - away_d
                # Sensitivity only. Passing None makes QBBook use last week's starter.
                f["qb_d_fallback"] = float(wm.qb.delta(g.home_team, hq24)) - float(wm.qb.delta(g.away_team, aq24))
                f["game_id"] = g.game_id
                f["t24_both"] = int(hq24 is not None and aq24 is not None)
                f["t24_equals_actual"] = int(hq24 == hq and aq24 == aq)
                feats.append(f)
                n_seen += 1
                both_chart += f["t24_both"]
                both_match_actual += f["t24_equals_actual"]
        print(f"features {season}", flush=True)
    features = pd.DataFrame(feats)
    features.attrs["starter_audit"] = {
        "games_featurized": n_seen,
        "both_teams_have_t24_starter": both_chart,
        "t24_starter_equals_actual_starter": both_match_actual,
    }
    keep = [
        "game_id", "season", "week", "game_type", "result", "spread_line", "location",
    ]
    out = sched[keep].merge(features, on="game_id", how="inner")
    out["hfa"] = (out["location"] != "Neutral").astype(float)
    out["result"] = pd.to_numeric(out["result"], errors="coerce")
    out["spread_line"] = pd.to_numeric(out["spread_line"], errors="coerce")
    out["season"] = out["season"].astype(int)
    out.attrs["starter_audit"] = features.attrs["starter_audit"]
    return out


def oos_predictions(
    frame: pd.DataFrame,
    cols: list[str],
    *,
    allow: tuple[str, ...] | None,
) -> pd.Series:
    if allow is not None:
        require_allowlist(cols, allow)
        require_allowlist(list(frame[cols].columns), allow)
    pieces = []
    coefs = {}
    for season in range(2016, 2026):
        train = frame[(frame["season"] < season) & (frame["season"] >= 2015)]
        train = train.dropna(subset=[*cols, "result"])
        x = train[cols].to_numpy(dtype=float)
        y = train["result"].to_numpy(dtype=float)
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        coefs[str(season)] = {c: float(b) for c, b in zip(cols, beta)}
        test = frame[frame["season"] == season].dropna(subset=cols)
        pred = test[cols].to_numpy(dtype=float) @ beta
        pieces.append(pd.Series(pred, index=test["game_id"].to_numpy()))
        print(f"  season {season} n_train={len(train)}", flush=True)
    out = pd.concat(pieces)
    out.attrs["coefs"] = coefs
    return out


def shipped_v6() -> pd.DataFrame:
    feat_text = git_show(f"{V6_REF}:backtest/harness_model/priors/week_features.csv")
    coef_text = git_show(f"{V6_REF}:backtest/harness_model/coefs.json")
    features = pd.read_csv(pd.io.common.BytesIO(feat_text))
    require_allowlist(V6_FULL, V6_ALLOW)
    for col in V6_FULL:
        if col not in features.columns:
            raise RuntimeError(f"shipped v6 design is missing {col}")
    coefs = json.loads(coef_text)
    frozen = coefs["margin"]
    by_season = coefs["by_season"]
    parts = []
    for season, sl in features.groupby("season"):
        spec = by_season.get(str(int(season)), {"margin": frozen})["margin"]
        out = sl.copy()
        out["pred_v6"] = (
            spec["pass"] * out["pass_comp"]
            + spec["rush"] * out["rush_comp"]
            + spec["success"] * out["sr_comp"]
            + spec["hfa"] * out["hfa"]
        )
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def metric_bundle(pred: np.ndarray, result: np.ndarray, close: np.ndarray) -> dict:
    crps = crps_normal(result, pred)
    mae = np.abs(pred - result)
    ats = covers(pred, result, close)
    decided = ats[np.isfinite(ats)]
    wins = int(decided.sum())
    n_ats = int(decided.size)
    return {
        "crps": float(crps.mean()),
        "mae": float(mae.mean()),
        "ats": float(decided.mean()) if n_ats else None,
        "ats_wins": wins,
        "ats_n": n_ats,
        "_crps": crps,
        "_mae": mae,
        "_ats": ats,
    }


def paired(a: dict, b: dict, label: str) -> dict:
    """Differences are a minus b. A positive CRPS difference means a is worse."""
    return {
        "contrast": label,
        "crps": bootstrap_mean(a["_crps"] - b["_crps"], fresh_rng()),
        "mae": bootstrap_mean(a["_mae"] - b["_mae"], fresh_rng()),
        "ats": bootstrap_mean(a["_ats"] - b["_ats"], fresh_rng()),
    }


def public_metrics(block: dict) -> dict:
    return {k: v for k, v in block.items() if not k.startswith("_")}


def ablation_table(
    frame: pd.DataFrame,
    full_cols: list[str],
    drops: list[tuple],
    sample_ids: set[str],
    allow: tuple[str, ...],
) -> list[dict]:
    """Each drop refits the remaining columns. Delta is ablated minus full."""
    print(f"full refit {full_cols}", flush=True)
    full = oos_predictions(frame, full_cols, allow=allow)
    rows = []
    base = _on_sample(frame, full, sample_ids)
    for name, cols, note in drops:
        print(f"drop {name}: {cols}", flush=True)
        pred = oos_predictions(frame, cols, allow=allow)
        alt = _on_sample(frame, pred, sample_ids)
        # Align to the full-sample order.
        delta_crps = bootstrap_mean(alt["_crps"] - base["_crps"], fresh_rng())
        delta_mae = bootstrap_mean(alt["_mae"] - base["_mae"], fresh_rng())
        rows.append({
            "component": name,
            "note": note,
            "columns_kept": cols,
            "crps": alt["crps"],
            "mae": alt["mae"],
            "delta_crps": delta_crps,
            "delta_mae": delta_mae,
        })
    rows.insert(0, {
        "component": "(full)",
        "note": "walk-forward refit of every non-market column",
        "columns_kept": full_cols,
        "crps": base["crps"],
        "mae": base["mae"],
        "delta_crps": {"n": base_n(base), "diff": 0.0, "ci95": [0.0, 0.0]},
        "delta_mae": {"n": base_n(base), "diff": 0.0, "ci95": [0.0, 0.0]},
    })
    return rows


def base_n(block: dict) -> int:
    return int(np.isfinite(block["_crps"]).sum())


def _on_sample(frame: pd.DataFrame, pred: pd.Series, sample_ids: set[str]) -> dict:
    sl = frame[frame["game_id"].isin(sample_ids)].drop_duplicates("game_id")
    sl = sl.set_index("game_id").loc[sorted(sample_ids)]
    mu = pred.reindex(sl.index).to_numpy(dtype=float)
    if not np.isfinite(mu).all():
        missing = int((~np.isfinite(mu)).sum())
        raise RuntimeError(f"{missing} sample games have no prediction")
    return metric_bundle(mu, sl["result"].to_numpy(dtype=float), sl["spread_line"].to_numpy(dtype=float))


def _materialize_v6() -> object:
    dest = Path("/tmp/v6diag")
    (dest / "backend" / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "backend" / "app" / "services").mkdir(parents=True, exist_ok=True)
    (dest / "backend" / "app" / "models").mkdir(parents=True, exist_ok=True)
    (dest / "backend" / "app" / "__init__.py").write_text("")
    (dest / "backend" / "app" / "services" / "__init__.py").write_text("")
    (dest / "backend" / "app" / "models" / "__init__.py").write_text("")
    for spec, rel in (
        (f"{V6_REF}:backend/app/services/v6_blind.py", "backend/app/services/v6_blind.py"),
        (f"{V6_REF}:backend/scripts/backtest_v6_blind.py", "backend/scripts/backtest_v6_blind.py"),
        (f"{V6_REF}:backend/app/models/seed.py", "backend/app/models/seed.py"),
    ):
        (dest / rel).write_bytes(git_show(spec))
    os.environ["NFLVERSE_DIR"] = str(DATA)
    path = dest / "backend" / "scripts" / "backtest_v6_blind.py"
    name = "v6_backtest_diag"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def v6_component_frame(mod) -> pd.DataFrame:
    """One frozen-hyperparameter rebuild that keeps the QB delta separate.

    The shipped prior file adds the starter delta into ``pass_comp`` before
    the regression, so the published column cannot drop the QB alone. This
    rebuild uses the same frozen hyperparameters (half-life 14, k_pass 8,
    k_rush 10, k_success 12, def weight 0.55, QB k 400). It is used only if
    it reproduces the shipped ``pass_comp``.
    """
    teams = sorted(mod.nfl_team_ids())
    print(f"v6 rebuild: schedule and pbp, {len(teams)} teams", flush=True)
    sched = mod.load_schedule()
    team_games, qb_games = mod.aggregate_pbp()
    team_games = mod.apply_neutral(team_games, sched)
    team_games, qb_games, ti = mod.code_teams(team_games, qb_games, teams)
    roster = mod.roster_continuity(teams)
    starters, _audit = mod.build_starters(sched)
    qb_share = mod.qb_continuity(qb_games, starters)
    players = pd.read_parquet(
        DATA / "aux" / "players.parquet",
        columns=["gsis_id", "draft_round", "draft_year"],
    )
    draft = mod.draft_table(players)
    weeks_by_season: dict[int, list[int]] = {}
    sub = sched[sched["season"].between(2014, 2026) & sched["home_team"].isin(teams)]
    for season, g in sub.groupby("season"):
        weeks_by_season[int(season)] = sorted(int(w) for w in g["week"].unique())
    hl, kp, kr, ks, dw, qbk = 14.0, 8.0, 10.0, 12.0, 0.55, 400.0
    print("v6 rebuild: pass cache", flush=True)
    pass_asof, pass_end = mod.metric_cache(
        team_games, "pass_epa", "pass_n", hl, kp, teams, roster, qb_share, weeks_by_season,
    )
    print("v6 rebuild: rush cache", flush=True)
    rush_asof, _rush_end = mod.metric_cache(
        team_games, "rush_epa", "rush_n", hl, kr, teams, roster, qb_share, weeks_by_season,
    )
    print("v6 rebuild: success cache", flush=True)
    sr_asof, _sr_end = mod.metric_cache(
        team_games, "suc", "suc_n", hl, ks, teams, roster, qb_share, weeks_by_season,
    )
    print("v6 rebuild: QB deltas", flush=True)
    deltas = mod.qb_deltas(
        qb_games, pass_asof, pass_end, hl, qbk, starters, teams, ti, draft, weeks_by_season,
    )
    games = mod.build_game_frame(sched, teams)
    rows = []
    for rec in games.itertuples(index=False):
        key = (int(rec.season), int(rec.week))
        if key not in pass_asof or key not in rush_asof or key not in sr_asof or key not in deltas:
            continue
        h, a = ti[rec.home_team], ti[rec.away_team]
        po, pd_, *_ = pass_asof[key]
        ro, rd, *_ = rush_asof[key]
        so, sd, *_ = sr_asof[key]
        dq = deltas[key]
        pass_edge = (po[h] - po[a]) + dw * (pd_[a] - pd_[h])
        rush_edge = (ro[h] - ro[a]) + dw * (rd[a] - rd[h])
        sr_edge = (so[h] - so[a]) + dw * (sd[a] - sd[h])
        qb = float(dq[h] - dq[a])
        rows.append({
            "game_id": rec.game_id,
            "season": int(rec.season),
            "pass_edge": float(pass_edge),
            "qb": qb,
            "pass_comp": float(pass_edge + qb),
            "rush_comp": float(rush_edge),
            "sr_comp": float(sr_edge),
            "hfa": 0.0 if int(rec.neutral) == 1 else 1.0,
            "result": (
                float(rec.home_score) - float(rec.away_score)
                if pd.notna(rec.home_score) and pd.notna(rec.away_score) else np.nan
            ),
        })
    return pd.DataFrame(rows)


def v6_drops(split_ok: bool) -> list[tuple]:
    drops = [
        ("pass_comp", ["rush_comp", "sr_comp", "hfa"],
         "Drop the pass column. In the shipped model that column is the pass ridge plus the starter delta, priced by one coefficient."),
        ("rush_comp", ["pass_comp", "sr_comp", "hfa"],
         "Drop the rush ridge."),
        ("sr_comp", ["pass_comp", "rush_comp", "hfa"],
         "Drop the success-rate ridge."),
        ("hfa", ["pass_comp", "rush_comp", "sr_comp"],
         "Drop home field. There is no other intercept."),
        ("team_units", ["hfa"],
         "Drop pass, rush, and success together. Home field remains. Diagnostic group, not a candidate model."),
    ]
    if split_ok:
        drops[1:1] = [
            ("qb_delta", ["pass_edge", "rush_comp", "sr_comp", "hfa"],
             "Drop only the starter delta. The pass ridge stays, and its coefficient is refit."),
            ("pass_team", ["qb", "rush_comp", "sr_comp", "hfa"],
             "Drop only the pass ridge. The starter delta stays as its own column and is refit."),
        ]
    return drops


V5_FALLBACK = ["epa_d", "pts_d", "st_d", "qb_d_fallback", "hfa"]
V5_FALLBACK_ALLOW = tuple(V5_FALLBACK)
V6_SPLIT_ALLOW = ("pass_comp", "pass_edge", "qb", "rush_comp", "sr_comp", "hfa")
QA_CLAIM = {
    "contrast": "v6-blind minus v5-blind",
    "metric": "crps",
    "diff": 0.088,
    "ci95": [0.031, 0.145],
    "source": "QA review of PR #32, fully blind v5 with the depth-chart starter 24h before kickoff",
}


def main() -> None:
    _self_check()
    if not (DATA / "games.csv").exists():
        raise SystemExit(f"nflverse mirror not found at {DATA}")
    print("loading main qb-ridge-v5", flush=True)
    v5 = load_main_v5()
    print("loading the T-24h depth chart", flush=True)
    starter_mod = _materialize_v6()
    starters, chart_audit = starter_mod.build_starters(starter_mod.load_schedule())
    print("building v5 features (market column kept for the reference fit only)", flush=True)
    frame = v5_feature_frame(v5, starters)
    feature_audit = dict(frame.attrs.get("starter_audit", {}))
    print("v5 full walk-forward", flush=True)
    pred_full = oos_predictions(frame, V5_FULL, allow=None)
    print("v5-blind walk-forward", flush=True)
    pred_blind = oos_predictions(frame, V5_BLIND, allow=V5_BLIND_ALLOW)
    print("v5 sensitivity: last starter when the chart is missing", flush=True)
    pred_fallback = oos_predictions(frame, V5_FALLBACK, allow=V5_FALLBACK_ALLOW)
    print("shipped v6", flush=True)
    v6 = shipped_v6()
    both = frame.merge(v6[["game_id", "pred_v6", "pass_comp", "rush_comp", "sr_comp"]], on="game_id", how="inner", suffixes=("", "_v6"))
    # hfa is already on the v5 frame. The shipped v6 hfa matches location.
    both = both.rename(columns={"hfa": "hfa"})
    sample = both[
        (both["game_type"] == "REG")
        & both["season"].between(2016, 2025)
        & np.isfinite(both["result"])
        & np.isfinite(both["spread_line"])
    ].drop_duplicates("game_id")
    sample = sample.copy()
    sample["pred_v5"] = sample["game_id"].map(pred_full)
    sample["pred_v5_blind"] = sample["game_id"].map(pred_blind)
    sample["pred_v5_fallback"] = sample["game_id"].map(pred_fallback)
    sample = sample[
        np.isfinite(sample["pred_v5"])
        & np.isfinite(sample["pred_v5_blind"])
        & np.isfinite(sample["pred_v5_fallback"])
        & np.isfinite(sample["pred_v6"])
    ]
    sample = sample.sort_values("game_id").reset_index(drop=True)
    print(f"common REG games {len(sample)}", flush=True)
    if len(sample) != 2639:
        print(f"warning: expected 2639 games from PR #32, found {len(sample)}", flush=True)

    y = sample["result"].to_numpy(dtype=float)
    close = sample["spread_line"].to_numpy(dtype=float)
    blocks = {
        "v5": metric_bundle(sample["pred_v5"].to_numpy(dtype=float), y, close),
        "v5_blind": metric_bundle(sample["pred_v5_blind"].to_numpy(dtype=float), y, close),
        "v6": metric_bundle(sample["pred_v6"].to_numpy(dtype=float), y, close),
    }
    fallback = metric_bundle(sample["pred_v5_fallback"].to_numpy(dtype=float), y, close)
    contrasts = [
        paired(blocks["v6"], blocks["v5_blind"], "v6-blind minus v5-blind"),
        paired(blocks["v5_blind"], blocks["v6"], "v5-blind minus v6-blind"),
        paired(blocks["v5"], blocks["v6"], "v5 minus v6-blind"),
        paired(blocks["v5_blind"], blocks["v5"], "v5-blind minus v5"),
        paired(blocks["v6"], fallback, "v6-blind minus v5 last-start-if-missing"),
    ]
    print(json.dumps({name: public_metrics(block) for name, block in blocks.items()}, indent=2), flush=True)
    ids = set(sample["game_id"])
    print("v5-blind ablations", flush=True)
    v5_drops = [
        ("epa_d", ["pts_d", "st_d", "qb_d", "hfa"], "Drop opponent-adjusted EPA/play."),
        ("pts_d", ["epa_d", "st_d", "qb_d", "hfa"], "Drop opponent-adjusted points."),
        ("st_d", ["epa_d", "pts_d", "qb_d", "hfa"], "Drop special teams."),
        ("qb_d", ["epa_d", "pts_d", "st_d", "hfa"],
         "Drop the T-24h depth-chart starter delta. A missing chart is 0 on that side."),
        ("hfa", ["epa_d", "pts_d", "st_d", "qb_d"], "Drop home field. There is no other intercept."),
        ("team_ridges", ["qb_d", "hfa"],
         "Drop EPA, points, and special-teams ridges together. QB and home field remain. Diagnostic group, not a candidate model."),
    ]
    v5_ablation = ablation_table(frame, V5_BLIND, v5_drops, ids, V5_BLIND_ALLOW)

    split_note = "not run"
    split_ok = False
    match = None
    try:
        print("rebuilding v6 components to separate the QB delta", flush=True)
        rebuilt = v6_component_frame(starter_mod)
        chk = sample[["game_id", "pass_comp", "rush_comp", "sr_comp"]].merge(
            rebuilt, on="game_id", suffixes=("_shipped", "_rebuilt"),
        )
        match = {}
        for col in ("pass_comp", "rush_comp", "sr_comp"):
            a = chk[f"{col}_shipped"].to_numpy(dtype=float)
            b = chk[f"{col}_rebuilt"].to_numpy(dtype=float)
            match[col] = {
                "n": int(len(chk)),
                "corr": float(np.corrcoef(a, b)[0, 1]),
                "mae": float(np.mean(np.abs(a - b))),
            }
        split_ok = all(m["corr"] >= 0.999 and m["mae"] <= 0.01 for m in match.values())
        split_note = "used" if split_ok else "rebuild did not reproduce the shipped features; QB was not split"
        print(f"v6 rebuild match {match} split_ok={split_ok}", flush=True)
    except Exception as exc:  # noqa: BLE001 — a failed split must not invent a QB ablation
        split_note = f"rebuild failed: {type(exc).__name__}: {exc}"
        print(split_note, flush=True)
        match = None
        rebuilt = None
    print("v6 ablations", flush=True)
    # Prefer the shipped feature columns for the ablations that do not need a split,
    # so the full refit is the same design as PR #32.
    shipped_design = frame[["game_id", "season", "result", "spread_line"]].merge(
        v6[["game_id", "pass_comp", "rush_comp", "sr_comp", "hfa"]].drop_duplicates("game_id"),
        on="game_id",
        how="inner",
    )
    v6_ablation = ablation_table(shipped_design, V6_FULL, v6_drops(False), ids, V6_ALLOW)
    qb_split_ablation = None
    if split_ok:
        split_frame = frame[["game_id", "season", "result", "spread_line"]].merge(rebuilt, on="game_id", how="inner")
        split_frame = split_frame.loc[:, ~split_frame.columns.duplicated()]
        qb_split_ablation = ablation_table(
            split_frame, ["pass_comp", "rush_comp", "sr_comp", "hfa"], v6_drops(True), ids, V6_SPLIT_ALLOW,
        )

    qa_observed = contrasts[0]["crps"]
    mkt_coefs = pred_full.attrs.get("coefs", {})
    payload = {
        "definition": {
            "v5_blind": (
                "Allowlist epa_d, pts_d, st_d, qb_d, hfa. qb_d is the home minus away "
                "QBBook delta for the depth-chart starter at kickoff minus 24 hours. "
                "A missing chart is 0 on that side, not last week's starter and not "
                "home_qb_id or away_qb_id. No mkt_d and no other market column. "
                "Coefficients are refit by ordinary least squares on seasons >= 2015 "
                "and < the test season, every game type, features from games before that week."
            ),
            "v5_reference": (
                "The PR #32 qb-ridge-v5: mkt_d plus epa_d, pts_d, st_d, the actual "
                "starter delta qb_d_actual, and hfa, refit on the same window."
            ),
            "v6": (
                "Shipped prod/v6-blind-main week_features and by_season margin coefficients, "
                "rounded as published. Columns pass_comp, rush_comp, sr_comp, hfa. "
                "pass_comp already contains the starter delta."
            ),
            "guard": "allowlist: a design column outside the permitted list raises ValueError",
            "sensitivity_not_the_model": (
                "qb_d_fallback uses the same T-24h starter, and QBBook's last-starter "
                "fallback only when the chart is missing. It is not v5-blind."
            ),
        },
        "allowlist": {
            "v5_blind": list(V5_BLIND_ALLOW),
            "v6_blind": list(V6_ALLOW),
        },
        "starter_audit": {
            "depth_chart": chart_audit,
            "features_all_game_types": feature_audit,
            "scored_reg_sample": {
                "n": int(len(sample)),
                "both_teams_have_t24_starter": int(sample["t24_both"].sum()),
                "t24_starter_equals_actual_starter": int(sample["t24_equals_actual"].sum()),
            },
        },
        "qa_claim": {
            **QA_CLAIM,
            "observed_diff": qa_observed["diff"],
            "observed_ci95": qa_observed["ci95"],
            "protocol": (
                "This run uses the PR #32 games, seed 20261026, and 10000 paired resamples. "
                "The QA review did not leave a script in this repo, so the seed behind 0.088 was not replayed from source."
            ),
        },
        "sample": "2016-2025 REG games with a result, a closing spread_line, and v5, v5-blind, and v6 predictions",
        "n_games": len(sample),
        "sigma_margin": SIGMA,
        "seed": SEED,
        "n_bootstrap": N_BOOT,
        "difference": "first minus second; a positive CRPS difference means the first model is worse. Ablation delta is the reduced model minus the full model of that family.",
        "not_in_the_walk_forward_design": [
            "rest",
            "injuries",
            "weather",
            "context layer",
            "live market blend",
            "prop and ADP anchors",
        ],
        "levels": {name: public_metrics(block) for name, block in blocks.items()},
        "sensitivity_last_start_if_chart_missing": public_metrics(fallback),
        "contrasts": contrasts,
        "v5_market_coefficients": mkt_coefs,
        "v5_blind_coefficients": pred_blind.attrs.get("coefs", {}),
        "ablations": {
            "v5_blind": v5_ablation,
            "v6_blind": v6_ablation,
        },
        "v6_qb_split": {
            "status": split_note,
            "match_to_shipped_features": match,
            "ablations": qb_split_ablation,
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    sample[[
        "game_id", "season", "week", "result", "spread_line",
        "pred_v5", "pred_v5_blind", "pred_v6",
    ]].to_csv(OUT_CSV, index=False)
    print(json.dumps({"n": len(sample), "levels": payload["levels"], "contrasts": contrasts}, indent=2), flush=True)


if __name__ == "__main__":
    main()
