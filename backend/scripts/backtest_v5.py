"""Walk-forward backtest + coefficient refit for the v5 game model.

Reproduces docs/MODEL_V5.md end to end from public nflverse data:

    python -m scripts.backtest_v5                 # report only
    python -m scripts.backtest_v5 --since 2015    # first season used for fitting

For every week from ``--start`` on, ratings are built from games strictly
before that week (``team_ratings_v5.build_week_model``); stage-2 coefficients
for season S are fit only on seasons < S. Prints out-of-sample error against
results and against the closing line, plus the coefficients fit on every
season — paste those into ``team_ratings_v5.MARGIN_COEFS`` / ``TOTAL_COEFS``
(or set the ``v5.*`` registry params) when refreshing the model.

Needs network access to github.com (nflverse releases). ~2-3 minutes.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from app.adapters.data.nflverse_stats import RELEASE
from app.services import team_ratings_v5 as v5
from app.utils.teams import canonical_team

PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team", "epa",
    "pass", "rush", "qb_kneel", "qb_spike", "wp", "qtr", "score_differential",
    "special_teams_play", "qb_dropback", "passer_player_id",
]
M1 = ["mkt_d", "epa_d", "pts_d", "st_d", "qb_d"]
M0 = ["epa_d", "pts_d", "st_d", "qb_d"]
TC = ["pts_s", "epa_s", "env2", "plays_s", "qb_s"]


def _ols(df: pd.DataFrame, cols: list[str], y: str, intercept_col: str) -> np.ndarray:
    d = df.dropna(subset=cols + [y])
    x = np.column_stack([d[c] for c in cols] + [d[intercept_col]])
    return np.linalg.lstsq(x, d[y].to_numpy(dtype=float), rcond=None)[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2013, help="first PBP season to load")
    ap.add_argument("--start", type=int, default=2014, help="first season to build features for")
    ap.add_argument("--since", type=int, default=2015, help="first season used in stage-2 fits")
    ap.add_argument("--test-from", type=int, default=2017)
    args = ap.parse_args()

    sched = pd.read_csv(f"{RELEASE}/schedules/games.csv")
    sched = sched[sched["season"] >= args.first].copy()
    for c in ("home_team", "away_team"):
        sched[c] = sched[c].map(canonical_team)
    last = int(sched.dropna(subset=["home_score"])["season"].max())

    frames = []
    for s in range(args.first, last + 1):
        pbp = pd.read_parquet(f"{RELEASE}/pbp/play_by_play_{s}.parquet", columns=PBP_COLS)
        frames.append(v5.team_game_rows(pbp, s, team_map=canonical_team))
        print(f"rows {s}: {len(frames[-1])}")
    rows = v5.attach_points(pd.concat(frames, ignore_index=True), sched)
    teams = sorted(set(rows["posteam"]))

    feats = []
    for s in range(args.start, last + 1):
        for w in sorted(sched[sched["season"] == s]["week"].unique()):
            wm = v5.build_week_model(rows, sched, s, int(w), teams)
            for g in sched[(sched["season"] == s) & (sched["week"] == w)].itertuples():
                if not (wm.has_team(g.home_team) and wm.has_team(g.away_team)):
                    continue
                hq = g.home_qb_id if isinstance(g.home_qb_id, str) else None
                aq = g.away_qb_id if isinstance(g.away_qb_id, str) else None
                f = wm.features(g.home_team, g.away_team, hq, aq)
                f["game_id"] = g.game_id
                feats.append(f)
    d = sched.merge(pd.DataFrame(feats), on="game_id")
    d["hfa"] = (d["location"] != "Neutral").astype(float)
    d["one"] = 1.0

    out = []
    for s in range(args.test_from, last + 1):
        tr = d[(d["season"] < s) & (d["season"] >= args.since)]
        te = d[d["season"] == s].copy()
        b1, bt = _ols(tr, M1, "result", "hfa"), _ols(tr, TC, "total", "one")
        te["pred_margin"] = np.column_stack([te[c].fillna(0) for c in M1] + [te["hfa"]]) @ b1
        te["pred_total"] = np.column_stack([te[c] for c in TC] + [te["one"]]) @ bt
        out.append(te)
    o = pd.concat(out)
    o = o[o["result"].notna() & o["spread_line"].notna()]
    for lo, hi in ((args.test_from, 2020), (2021, last)):
        x = o[o["season"].between(lo, hi)]
        if not len(x):
            continue
        em, el = x["pred_margin"] - x["result"], x["spread_line"] - x["result"]
        diff = x["pred_margin"] - x["spread_line"]
        cover = np.sign(x["result"] - x["spread_line"])
        m = cover != 0
        beta = np.polyfit(diff, x["result"] - x["spread_line"], 1)[0]
        print(f"\n{lo}-{hi}  n={len(x)}")
        print(f"  margin RMSE model {np.sqrt((em**2).mean()):.3f}  line {np.sqrt((el**2).mean()):.3f}")
        print(f"  corr w/ line {np.corrcoef(x['pred_margin'], x['spread_line'])[0, 1]:.3f}"
              f"  slope {np.polyfit(x['spread_line'], x['pred_margin'], 1)[0]:.2f}")
        print(f"  ATS (all) {(np.sign(diff[m]) == cover[m]).mean():.3f}  "
              f"realization beta {beta:.3f}")
        y = x.dropna(subset=["total_line"])
        print(f"  total RMSE model {np.sqrt(((y['pred_total'] - y['total'])**2).mean()):.3f}"
              f"  line {np.sqrt(((y['total_line'] - y['total'])**2).mean()):.3f}")

    a = d[d["season"] >= args.since]
    fit = {
        "MARGIN_COEFS": dict(zip(M1 + ["hfa"], np.round(_ols(a, M1, "result", "hfa"), 3).tolist())),
        "MARGIN_COEFS_NO_MKT": dict(zip(M0 + ["hfa"], np.round(_ols(a, M0, "result", "hfa"), 3).tolist())),
        "TOTAL_COEFS": dict(zip(TC + ["const"], np.round(_ols(a, TC, "total", "one"), 3).tolist())),
    }
    print("\nCoefficients fit on all seasons:\n" + json.dumps(fit, indent=1))


if __name__ == "__main__":
    main()
