"""Reproduce the frozen v6-blind baseline and compare it to the stored files.

Market columns on the schedule are not design columns. This script does not
fit the feature bundle and does not grade the bundle.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.environ["NFLVERSE_DIR"] = str(ROOT / "data" / "nflverse")
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import backtest_v6_blind as bt
from app.services import v6_blind as v6

STORE_FEAT = ROOT / "backtest" / "harness_model" / "priors" / "week_features.csv"
STORE_PRED = ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv"
COEFS = ROOT / "backtest" / "harness_model" / "coefs.json"
OUT = ROOT / "data" / "nflverse" / "v6_rebuild.parquet"

FEATURE_COLS = [
    "pass_comp", "rush_comp", "sr_comp", "hfa",
    "pass_sum", "rush_sum", "sr_sum",
]


def main() -> None:
    teams = sorted(bt.nfl_team_ids())
    sched = bt.load_schedule()
    market = [
        c for c in sched.columns
        if c in v6.BANNED_INPUTS
        or "moneyline" in c
        or str(c).endswith("_odds")
        or c in ("spread_line", "total_line")
    ]
    print("schedule market columns kept for grading only", market, flush=True)
    # Ratings read play-by-play, not the schedule prices. The original assemble
    # copies the grading lines onto the game frame; they are dropped below.
    sched_feat = sched
    team_games, qb_games = bt.aggregate_pbp()
    team_games = bt.apply_neutral(team_games, sched_feat)
    team_games, qb_games, ti = bt.code_teams(team_games, qb_games, teams)
    roster = bt.roster_continuity(teams)
    starters, audit = bt.build_starters(sched_feat)
    qb_share = bt.qb_continuity(qb_games, starters)
    players = pd.read_parquet(
        bt.DATA / "aux" / "players.parquet",
        columns=["gsis_id", "draft_round", "draft_year"],
    )
    draft = bt.draft_table(players)
    weeks_by_season: dict[int, list[int]] = {}
    sub = sched_feat[sched_feat["season"].between(2014, 2025) & sched_feat["home_team"].isin(teams)]
    for season, g in sub.groupby("season"):
        weeks_by_season[int(season)] = sorted(int(w) for w in g["week"].unique())

    hl, kp, kr, ks, dw, qbk = 14.0, 8.0, 10.0, 12.0, 0.55, 400.0
    print("building frozen v6 caches", flush=True)
    pass_asof, _pass_end = bt.metric_cache(
        team_games, "pass_epa", "pass_n", hl, kp, teams, roster, qb_share, weeks_by_season,
    )
    rush_asof, _rush_end = bt.metric_cache(
        team_games, "rush_epa", "rush_n", hl, kr, teams, roster, qb_share, weeks_by_season,
    )
    sr_asof, _sr_end = bt.metric_cache(
        team_games, "suc", "suc_n", hl, ks, teams, roster, qb_share, weeks_by_season,
    )
    deltas = bt.qb_deltas(
        qb_games, pass_asof, _pass_end, hl, qbk, starters, teams, ti, draft, weeks_by_season,
    )
    games = bt.build_game_frame(sched_feat, teams)
    design = bt.assemble(games, pass_asof, rush_asof, sr_asof, deltas, ti, dw)
    grading = [c for c in ("spread_line", "total_line") if c in design.columns]
    design = design.drop(columns=grading)
    bad = [c for c in design.columns if any(tok in c.lower() for tok in ("spread", "moneyline", "odds", "total_line"))]
    if bad:
        raise SystemExit(f"rebuilt design still has market columns: {bad}")
    design.to_parquet(OUT, index=False)
    print("wrote", OUT, "rows", len(design), flush=True)

    stored_f = pd.read_csv(STORE_FEAT)
    stored_p = pd.read_csv(STORE_PRED)
    coefs = json.loads(COEFS.read_text())
    window = design[design["season"].between(2015, 2023)].copy()
    feat = stored_f[stored_f["season"].between(2015, 2023)]
    merged = window.merge(feat, on="game_id", suffixes=("_new", "_old"))
    print("feature join", len(merged), "of stored", len(feat), "rebuilt", len(window), flush=True)
    for col in FEATURE_COLS:
        a = merged[f"{col}_new"].to_numpy(dtype=float)
        b = merged[f"{col}_old"].to_numpy(dtype=float)
        both = np.isfinite(a) & np.isfinite(b)
        diff = a[both] - b[both]
        corr = float(np.corrcoef(a[both], b[both])[0, 1]) if both.sum() > 2 else float("nan")
        print(
            f"  {col}: n={int(both.sum())} mae={float(np.mean(np.abs(diff))):.6f} "
            f"max={float(np.max(np.abs(diff))):.6f} corr={corr:.6f}",
            flush=True,
        )

    # Apply the stored coefficients to the rebuilt features. 2015 uses the frozen fit.
    rows = []
    for season, sl in window.groupby("season"):
        spec = coefs["by_season"].get(str(int(season)), {"margin": coefs["margin"], "total": coefs["total"]})
        sl = sl.copy()
        sl["margin_hat"] = v6.apply_margin(sl, spec["margin"])
        sl["total_hat"] = v6.apply_total(sl, spec["total"])
        rows.append(sl)
    pred = pd.concat(rows, ignore_index=True)
    cmp = pred.merge(
        stored_p[stored_p["season"].between(2015, 2023)][["game_id", "margin", "total"]],
        on="game_id",
    )
    md = cmp["margin_hat"].to_numpy(dtype=float) - cmp["margin"].to_numpy(dtype=float)
    td = cmp["total_hat"].to_numpy(dtype=float) - cmp["total"].to_numpy(dtype=float)
    print(
        f"pred join {len(cmp)} margin mae {float(np.mean(np.abs(md))):.6f} "
        f"max {float(np.max(np.abs(md))):.6f} corr {float(np.corrcoef(cmp['margin_hat'], cmp['margin'])[0,1]):.6f}",
        flush=True,
    )
    print(
        f"total mae {float(np.mean(np.abs(td))):.6f} max {float(np.max(np.abs(td))):.6f} "
        f"corr {float(np.corrcoef(cmp['total_hat'], cmp['total'])[0,1]):.6f}",
        flush=True,
    )
    # CRPS of stored predictions vs rebuilt predictions, 2016-23 regular season, vs actual margin.
    reg = sched_feat[sched_feat["game_type"].eq("REG")][["game_id", "home_score", "away_score", "season"]]
    graded = cmp.merge(reg, on="game_id")
    graded = graded[graded["season"].between(2016, 2023) & graded["home_score"].notna()]
    y = graded["home_score"].to_numpy(dtype=float) - graded["away_score"].to_numpy(dtype=float)
    crps_new = float(v6.crps_normal(y, graded["margin_hat"].to_numpy(dtype=float), v6.MARGIN_SIGMA).mean())
    crps_old = float(v6.crps_normal(y, graded["margin"].to_numpy(dtype=float), v6.MARGIN_SIGMA).mean())
    print(
        f"2016-23 REG n={len(graded)} crps rebuilt {crps_new:.6f} stored {crps_old:.6f} "
        f"delta {crps_new - crps_old:.6f}",
        flush=True,
    )
    print("starter audit", {k: audit[k] for k in ("starter_assigned", "team_games", "missing_depth_chart")}, flush=True)


if __name__ == "__main__":
    main()
