"""Head-to-head: market-blind v6 vs main's qb-ridge-v5, REG 2016–2025.

v5 is the code on ``main`` (market prior ``mkt_d`` fit to past closing
spreads, stage-2 coefficients refit on seasons strictly before the test
season). v6 is the frozen walk-forward design: week features and the
per-season coefficients shipped with this branch. Closing lines grade ATS
and are not inputs. This script does not retune either model and does not
rewrite the v6 report.

    NFLVERSE_DIR=/path/to/nflverse python -m scripts.compare_v6_vs_v5
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.v6_blind import MARGIN_SIGMA, crps_normal

DATA = Path(os.environ.get("NFLVERSE_DIR", str(ROOT / "data" / "nflverse")))
OUT = ROOT / "backtest" / "v6_vs_v5_h2h.json"
SEED = 20261026
N_BOOT = 10000
M1 = ["mkt_d", "epa_d", "pts_d", "st_d", "qb_d"]
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


def load_main_v5():
    """The market-prior model currently on main, not the blind file in this tree."""
    src = subprocess.check_output(
        ["git", "show", "main:backend/app/services/team_ratings_v5.py"],
        cwd=ROOT,
    )
    path = Path("/tmp/team_ratings_v5_main.py")
    path.write_bytes(src)
    spec = importlib.util.spec_from_file_location("team_ratings_v5_main", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["team_ratings_v5_main"] = module
    spec.loader.exec_module(module)
    return module


def v6_predictions() -> pd.DataFrame:
    features = pd.read_csv(ROOT / "backtest" / "harness_model" / "priors" / "week_features.csv")
    coefs = json.loads((ROOT / "backtest" / "harness_model" / "coefs.json").read_text())
    frozen = coefs["margin"]
    by_season = coefs["by_season"]
    parts = []
    for season, sl in features.groupby("season"):
        spec = by_season.get(str(int(season)), {"margin": frozen})
        c = spec["margin"]
        out = sl.copy()
        out["pred_v6"] = (
            c["pass"] * out["pass_comp"]
            + c["rush"] * out["rush_comp"]
            + c["success"] * out["sr_comp"]
            + c["hfa"] * out["hfa"]
        )
        parts.append(out[["game_id", "pred_v6"]])
    return pd.concat(parts, ignore_index=True)


def v5_features(v5) -> pd.DataFrame:
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
    for season in range(2015, 2026):
        weeks = sorted(int(w) for w in sched.loc[sched["season"] == season, "week"].unique())
        for week in weeks:
            wm = v5.build_week_model(rows, sched, season, week, teams)
            slate = sched[(sched["season"] == season) & (sched["week"] == week)]
            for g in slate.itertuples(index=False):
                if not (wm.has_team(g.home_team) and wm.has_team(g.away_team)):
                    continue
                hq = g.home_qb_id if isinstance(g.home_qb_id, str) else None
                aq = g.away_qb_id if isinstance(g.away_qb_id, str) else None
                f = wm.features(g.home_team, g.away_team, hq, aq)
                f["game_id"] = g.game_id
                feats.append(f)
        print(f"features {season}: {sum(1 for f in feats if str(f['game_id']).startswith(str(season)))}", flush=True)
    return pd.DataFrame(feats)


def walk_forward_v5(features: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    d = sched.merge(features, on="game_id", how="inner")
    d["hfa"] = (d["location"] != "Neutral").astype(float)
    d["result"] = pd.to_numeric(d["result"], errors="coerce")
    d["spread_line"] = pd.to_numeric(d["spread_line"], errors="coerce")
    out = []
    for season in range(2016, 2026):
        train = d[(d["season"] < season) & (d["season"] >= 2015) & d["result"].notna()]
        train = train.dropna(subset=M1)
        x = np.column_stack([train[c].to_numpy(dtype=float) for c in M1] + [train["hfa"].to_numpy(dtype=float)])
        y = train["result"].to_numpy(dtype=float)
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        test = d[(d["season"] == season) & (d["game_type"] == "REG")].copy()
        test = test.dropna(subset=M1)
        xt = np.column_stack([test[c].to_numpy(dtype=float) for c in M1] + [test["hfa"].to_numpy(dtype=float)])
        test["pred_v5"] = xt @ beta
        test["v5_coef_mkt_d"] = float(beta[0])
        out.append(test[["game_id", "season", "week", "game_type", "result", "spread_line", "pred_v5", "v5_coef_mkt_d"]])
        print(f"v5 {season}: n={len(test)} mkt_d coef={beta[0]:.3f}", flush=True)
    return pd.concat(out, ignore_index=True)


def covers(pred: np.ndarray, result: np.ndarray, close: np.ndarray) -> np.ndarray:
    """1 if the model's side beat the close, 0 otherwise. NaN on a push or a tie with the line."""
    push = np.isclose(result, close)
    decided = ~push & ~np.isclose(pred, close)
    home = pred > close
    home_covers = result > close
    won = (home & home_covers) | (~home & ~home_covers)
    out = np.where(decided, won.astype(float), np.nan)
    return out


def bootstrap_mean(diff: np.ndarray, rng: np.random.Generator) -> dict:
    finite = diff[np.isfinite(diff)]
    n = int(finite.size)
    point = float(finite.mean()) if n else None
    if n == 0:
        return {"n": 0, "diff": None, "ci95": [None, None]}
    draws = rng.integers(0, n, size=(N_BOOT, n))
    means = finite[draws].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return {"n": n, "diff": point, "ci95": [float(lo), float(hi)]}


def main() -> None:
    print("loading main qb-ridge-v5", flush=True)
    v5 = load_main_v5()
    assert "mkt_d" in v5.MARGIN_COEFS
    print("building v5 features from local pbp", flush=True)
    features = v5_features(v5)
    sched = pd.read_csv(DATA / "games.csv", low_memory=False)
    sched = sched[sched["season"].between(2015, 2025)].copy()
    for col in ("home_team", "away_team"):
        sched[col] = sched[col].map(canonical_team)
    v5_pred = walk_forward_v5(features, sched)
    v6_pred = v6_predictions()
    both = v5_pred.merge(v6_pred, on="game_id", how="inner")
    both = both[both["game_type"] == "REG"]
    both = both[both["season"].between(2016, 2025)]
    both = both[np.isfinite(both["result"]) & np.isfinite(both["spread_line"]) & np.isfinite(both["pred_v5"]) & np.isfinite(both["pred_v6"])]
    both = both.drop_duplicates("game_id")
    y = both["result"].to_numpy(dtype=float)
    close = both["spread_line"].to_numpy(dtype=float)
    p5 = both["pred_v5"].to_numpy(dtype=float)
    p6 = both["pred_v6"].to_numpy(dtype=float)
    crps5 = crps_normal(y, p5, MARGIN_SIGMA)
    crps6 = crps_normal(y, p6, MARGIN_SIGMA)
    mae5 = np.abs(p5 - y)
    mae6 = np.abs(p6 - y)
    ats5 = covers(p5, y, close)
    ats6 = covers(p6, y, close)
    # Independent streams, each seeded 20261026, so one metric's draws do not
    # shift the next. Difference is v6 minus v5. Lower CRPS and MAE favor v6
    # when the difference is negative. Higher ATS favors v6 when it is positive.
    crps_ci = bootstrap_mean(crps6 - crps5, np.random.default_rng(SEED))
    mae_ci = bootstrap_mean(mae6 - mae5, np.random.default_rng(SEED))
    ats_ci = bootstrap_mean(ats6 - ats5, np.random.default_rng(SEED))
    payload = {
        "sample": "2016-2025 REG games with a result, a closing spread_line, and both predictions",
        "n_games": len(both),
        "sigma_margin": MARGIN_SIGMA,
        "seed": SEED,
        "n_bootstrap": N_BOOT,
        "difference": "v6 minus v5",
        "v5": {
            "source": "main:backend/app/services/team_ratings_v5.py",
            "model": "qb-ridge-v5",
            "stage2": "OLS of home margin on mkt_d, epa_d, pts_d, st_d, qb_d, hfa, fit on seasons >= 2015 and < test season",
            "features": "build_week_model from games strictly before the week, including the market prior",
            "crps": float(crps5.mean()),
            "mae": float(mae5.mean()),
            "ats": float(np.nanmean(ats5)),
            "ats_n": int(np.isfinite(ats5).sum()),
        },
        "v6": {
            "source": "backtest/harness_model priors and by_season coefficients",
            "model": "qb-ridge-v6-blind",
            "crps": float(crps6.mean()),
            "mae": float(mae6.mean()),
            "ats": float(np.nanmean(ats6)),
            "ats_n": int(np.isfinite(ats6).sum()),
        },
        "diff_v6_minus_v5": {
            "crps": crps_ci,
            "mae": mae_ci,
            "ats": ats_ci,
        },
        "gate_v6_crps_no_worse_than_v5": bool(crps6.mean() <= crps5.mean()),
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2), flush=True)


if __name__ == "__main__":
    main()
