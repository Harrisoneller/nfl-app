"""QB-transition ablation on top of frozen v6-blind.

`select` fits the shrinkage k and the margin/total weights on knowable
2016–23 games only and writes the frozen file. It does not score 2024 or later.

`evaluate` reads that frozen file and scores the holdout once. Run it only
after PREREG.md is committed.

The feature is the expected starter's shrunk EPA/dropback minus the QB value
already inside the team pass rating. It is applied only on transition games.
On those games the QB piece already inside the v6 margin is removed and this
weight replaces it, so the starter is not counted twice. Other games keep the
v6 number.

    pred_margin = v6_margin
        - pass_coef * (old_dh if home transitions else 0
                       - old_da if away transitions else 0)
        + w_margin * f_margin
    pred_total = v6_total
        - total_pass_coef * (old_dh if home transitions else 0
                             + old_da if away transitions else 0)
        + w_total * f_total

`pass_coef` is the coefficient already inside that season's v6 prediction
(`by_season` for 2016–2023, the frozen full-sample coefficient for 2024–2026).
2015 rows are not used to fit w: their stored margin used the full-sample
coefficient. The 2016 walk-forward weight is 0 because no earlier season is
in the training window. The frozen weight is one through-origin slope on all
knowable 2016–23 games.

Pre-2025 expected starter: the previous game's starter, unless that QB's
injury row with date_modified at or before kickoff minus 24 hours says Out
or Doubtful. Then the next depth-chart QB who is not themselves Out or
Doubtful. A starter change is knowable only when that report shows it.
2025–26 keep the timestamped depth chart cut at kickoff minus 24 hours.

home_qb_id is the actual starter. It labels UNKNOWABLE games and, from games
already played, the incumbent. It is not the expected starter of the current game.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import v6_blind as v6  # noqa: E402

OUT_DIR = ROOT / "research" / "qb_transition"
FEATURES_PATH = OUT_DIR / "features.csv"
FROZEN_PATH = OUT_DIR / "frozen.json"
RESULTS_PATH = ROOT / "QB_TRANSITION_RESULTS.md"
PREDS = ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv"

# Locked before any fit is looked at. Higher k wins a tie.
QB_K_GRID = (300.0, 400.0)
TIE_BREAK_HIGHER_K = True
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20241023
HOLDOUT_FIRST_SEASON = 2024
ATS_BAR = 0.525
MIN_PLAYS = 400
P_BAR = 0.01


def _load_backtest():
    path = ROOT / "backend" / "scripts" / "backtest_v6_blind.py"
    spec = importlib.util.spec_from_file_location("v6_backtest_impl", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def report_triggers(status) -> bool:
    if status is None or (isinstance(status, float) and np.isnan(status)):
        return False
    text = str(status).strip().casefold()
    if text in {"", "nan", "none", "nat"}:
        return False
    return text in {"out", "doubtful"}


def unknowable_pre2025(actual, incumbent, report_triggered: bool) -> bool:
    """A change that already happened and was not on the Friday report."""
    if not isinstance(actual, str) or not isinstance(incumbent, str):
        return False
    if actual == incumbent:
        return False
    return not report_triggered


def unknowable_dated(actual, incumbent, expected) -> bool:
    """2025–26: the actual starter was not the T-24h depth-chart starter."""
    if not isinstance(actual, str):
        return False
    if isinstance(expected, str):
        return actual != expected
    if isinstance(incumbent, str):
        return actual != incumbent
    return False


def ols_through_origin(y: np.ndarray, x: np.ndarray) -> float:
    """One slope, no intercept. An intercept would shift games the feature does not touch."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    den = float(np.dot(x, x))
    if den < 1e-8:
        return 0.0
    return float(np.dot(x, y) / den)


def replacement_margin(
    margin, f_margin, old_dh, old_da, home_transition, away_transition, pass_coef, w_margin,
) -> np.ndarray:
    """Strip the v6 QB term on transitioning sides and put the new weight in its place."""
    old_edge = (
        np.asarray(old_dh, dtype=float) * np.asarray(home_transition, dtype=float)
        - np.asarray(old_da, dtype=float) * np.asarray(away_transition, dtype=float)
    )
    return (
        np.asarray(margin, dtype=float)
        - np.asarray(pass_coef, dtype=float) * old_edge
        + float(w_margin) * np.asarray(f_margin, dtype=float)
    )


def replacement_total(
    total, f_total, old_dh, old_da, home_transition, away_transition, total_pass_coef, w_total,
) -> np.ndarray:
    old_sum = (
        np.asarray(old_dh, dtype=float) * np.asarray(home_transition, dtype=float)
        + np.asarray(old_da, dtype=float) * np.asarray(away_transition, dtype=float)
    )
    return (
        np.asarray(total, dtype=float)
        - np.asarray(total_pass_coef, dtype=float) * old_sum
        + float(w_total) * np.asarray(f_total, dtype=float)
    )


def load_pass_coefs() -> dict[int, tuple[float, float]]:
    """Margin and total pass coefficients that are already inside predictions.csv."""
    raw = json.loads((ROOT / "backtest" / "harness_model" / "coefs.json").read_text())
    table = {
        int(season): (float(block["margin"]["pass"]), float(block["total"]["pass"]))
        for season, block in raw["by_season"].items()
    }
    full = (float(raw["margin"]["pass"]), float(raw["total"]["pass"]))
    for season in (2015, 2024, 2025, 2026):
        table.setdefault(season, full)
    return table


def attach_pass_coefs(df: pd.DataFrame) -> pd.DataFrame:
    table = load_pass_coefs()
    out = df.copy()
    seasons = out["season"].astype(int)
    missing = sorted(set(seasons) - set(table))
    if missing:
        raise RuntimeError(f"no pass coefficient for seasons {missing}")
    out["pass_coef"] = seasons.map(lambda season: table[int(season)][0]).astype(float)
    out["total_pass_coef"] = seasons.map(lambda season: table[int(season)][1]).astype(float)
    return out


def _as_str(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _weekly_chart(bt) -> dict[tuple[int, int, str], list[str]]:
    chart: dict[tuple[int, int, str], list[str]] = {}
    for year in range(2014, 2025):
        path = bt.DATA / "aux" / f"depth_{year}.parquet"
        if not path.exists():
            continue
        import pyarrow.parquet as pq

        names = set(pq.read_schema(path).names)
        if "club_code" not in names:
            continue
        depth = bt._parquet_columns(path, [
            "season", "week", "club_code", "depth_team", "position", "gsis_id", "depth_position",
        ])
        qb = depth["position"].astype(str).str.upper().eq("QB")
        dpos = depth["depth_position"].astype(str).str.upper().eq("QB")
        depth = depth[qb | dpos].copy()
        depth["team"] = depth["club_code"].map(bt._canon)
        depth["week"] = pd.to_numeric(depth["week"], errors="coerce")
        depth = depth[depth["week"].notna() & depth["gsis_id"].notna() & depth["team"].notna()]
        depth["week"] = depth["week"].astype(int)
        depth["season"] = depth["season"].astype(int)
        depth["depth_team"] = pd.to_numeric(depth["depth_team"], errors="coerce").fillna(99)
        depth = depth.sort_values(["season", "week", "team", "depth_team"])
        for (season, week, team), g in depth.groupby(["season", "week", "team"], sort=False):
            ids: list[str] = []
            for qb_id in g["gsis_id"].tolist():
                if qb_id not in ids:
                    ids.append(qb_id)
            chart[(int(season), int(week), team)] = ids
    return chart


def _injury_groups(bt) -> dict[tuple[int, int, str], pd.DataFrame]:
    frames = []
    for year in range(2014, 2025):
        path = bt.DATA / "aux" / f"injuries_{year}.parquet"
        if not path.exists():
            continue
        import pyarrow.parquet as pq

        if "date_modified" not in set(pq.read_schema(path).names):
            continue
        inj = bt._parquet_columns(path, [
            "season", "week", "team", "gsis_id", "report_status", "date_modified",
        ])
        frames.append(inj)
    if not frames:
        return {}
    inj = pd.concat(frames, ignore_index=True)
    inj["team"] = inj["team"].map(bt._canon)
    inj["date_modified"] = pd.to_datetime(inj["date_modified"], utc=True, errors="coerce")
    inj["week"] = pd.to_numeric(inj["week"], errors="coerce")
    inj = inj[inj["week"].notna() & inj["date_modified"].notna() & inj["gsis_id"].notna() & inj["team"].notna()]
    inj["week"] = inj["week"].astype(int)
    inj["season"] = inj["season"].astype(int)
    return {
        (int(s), int(w), t): g.sort_values("date_modified")
        for (s, w, t), g in inj.groupby(["season", "week", "team"], sort=False)
    }


def _status_at(group: pd.DataFrame | None, gsis: str | None, ko: pd.Timestamp | None):
    if group is None or ko is None or not gsis:
        return None
    sub = group[group["gsis_id"] == gsis]
    sub = sub[sub["date_modified"] <= ko - pd.Timedelta(hours=24)]
    if len(sub) == 0:
        return None
    return sub.iloc[-1]["report_status"]


def build_team_labels(bt, sched: pd.DataFrame, dated_starters: dict) -> pd.DataFrame:
    chart = _weekly_chart(bt)
    injuries = _injury_groups(bt)
    ordered = sched.sort_values(["gameday", "gametime", "game_id"], kind="mergesort")
    last: dict[str, str] = {}
    rows = []
    for rec in ordered.itertuples(index=False):
        season, week = int(rec.season), int(rec.week)
        ko = bt._kickoff(rec.gameday, getattr(rec, "gametime", None))
        for side, team, actual_raw in (
            ("home", rec.home_team, getattr(rec, "home_qb_id", None)),
            ("away", rec.away_team, getattr(rec, "away_qb_id", None)),
        ):
            if team is None:
                continue
            actual = _as_str(actual_raw)
            incumbent = last.get(team)
            group = injuries.get((season, week, team))
            if season >= 2025:
                expected = _as_str(dated_starters.get((season, week, team)))
                triggered = False
                transition = bool(expected and incumbent and expected != incumbent)
                unknowable = unknowable_dated(actual, incumbent, expected)
            else:
                triggered = report_triggers(_status_at(group, incumbent, ko))
                if triggered:
                    order = chart.get((season, week, team), [])

                    def _blocked(qb_id, _group=group, _ko=ko):
                        return report_triggers(_status_at(_group, qb_id, _ko))

                    expected = next(
                        (qb_id for qb_id in order if qb_id != incumbent and not _blocked(qb_id)),
                        None,
                    )
                    transition = bool(expected and incumbent and expected != incumbent)
                else:
                    expected = incumbent
                    transition = False
                unknowable = unknowable_pre2025(actual, incumbent, triggered)
            rows.append({
                "game_id": rec.game_id,
                "season": season,
                "week": week,
                "team": team,
                "side": side,
                "incumbent": incumbent,
                "expected": expected,
                "transition": transition,
                "unknowable": unknowable,
                "report_triggered": triggered,
            })
            if actual:
                last[team] = actual
    return pd.DataFrame(rows)


def _side_delta(vec, team: str, ti: dict[str, int]) -> float:
    if vec is None or team not in ti:
        return 0.0
    return float(vec[ti[team]])


def _feature_frame(
    labels: pd.DataFrame, new_deltas: dict, old_deltas: dict, ti: dict[str, int],
) -> pd.DataFrame:
    """One row per game. New deltas are zero on teams that are not transitioning."""
    home = labels[labels["side"] == "home"].set_index("game_id")
    away = labels[labels["side"] == "away"].set_index("game_id")
    rows = []
    for game_id in home.index.intersection(away.index):
        h, a = home.loc[game_id], away.loc[game_id]
        key = (int(h["season"]), int(h["week"]))
        new_vec, old_vec = new_deltas.get(key), old_deltas.get(key)
        dh = _side_delta(new_vec, h["team"], ti)
        da = _side_delta(new_vec, a["team"], ti)
        rows.append({
            "game_id": game_id,
            "season": int(h["season"]),
            "week": int(h["week"]),
            "home_team": h["team"],
            "away_team": a["team"],
            "f_margin": dh - da,
            "f_total": dh + da,
            "old_dh": _side_delta(old_vec, h["team"], ti),
            "old_da": _side_delta(old_vec, a["team"], ti),
            "transition": int(bool(h["transition"]) or bool(a["transition"])),
            "unknowable": int(bool(h["unknowable"]) or bool(a["unknowable"])),
            "home_transition": int(bool(h["transition"])),
            "away_transition": int(bool(a["transition"])),
        })
    return pd.DataFrame(rows)


def build_features(bt) -> pd.DataFrame:
    teams = sorted(bt.nfl_team_ids())
    ti = {team: i for i, team in enumerate(teams)}
    print("loading schedule, pbp, v6 starter proxy", flush=True)
    sched = bt.load_schedule()
    team_games, qb_games = bt.aggregate_pbp()
    team_games = bt.apply_neutral(team_games, sched)
    team_games, qb_games, ti = bt.code_teams(team_games, qb_games, teams)
    dated_starters, _audit = bt.build_starters(sched)
    print("pass surface at the frozen v6 half-life and k", flush=True)
    roster = bt.roster_continuity(teams)
    qb_share = bt.qb_continuity(qb_games, dated_starters)
    weeks_by_season: dict[int, list[int]] = {}
    for season, g in sched.groupby("season"):
        weeks_by_season[int(season)] = sorted(int(w) for w in g["week"].unique())
    hyper = json.loads((ROOT / "backtest" / "harness_model" / "coefs.json").read_text())["hyperparams"]
    hl = float(hyper["half_life"])
    k_pass = float(hyper["k_pass"])
    pass_asof, pass_end = bt.metric_cache(
        team_games, "pass_epa", "pass_n", hl, k_pass, teams, roster, qb_share, weeks_by_season,
    )
    players = pd.read_parquet(
        bt.DATA / "aux" / "players.parquet", columns=["gsis_id", "draft_round", "draft_year"],
    )
    draft = bt.draft_table(players)
    labels = build_team_labels(bt, sched, dated_starters)
    print(f"team labels {len(labels)}", flush=True)
    print("v6 starter deltas, removed on transition games so they are not counted twice", flush=True)
    old_deltas = bt.qb_deltas(
        qb_games, pass_asof, pass_end, hl, float(hyper["qb_k"]), dated_starters,
        teams, ti, draft, weeks_by_season,
    )
    pieces = []
    for qb_k in QB_K_GRID:
        print(f"qb deltas k={qb_k}", flush=True)
        starters = {}
        for rec in labels.itertuples(index=False):
            if rec.transition and isinstance(rec.expected, str):
                starters[(int(rec.season), int(rec.week), rec.team)] = rec.expected
        deltas = bt.qb_deltas(
            qb_games, pass_asof, pass_end, hl, float(qb_k), starters, teams, ti, draft, weeks_by_season,
        )
        frame = _feature_frame(labels, deltas, old_deltas, ti)
        frame["qb_k"] = float(qb_k)
        pieces.append(frame)
    out = pd.concat(pieces, ignore_index=True)
    out = out[out["season"].between(2015, 2026)].copy()
    return out


def _attach_v6(features: pd.DataFrame, max_season: int) -> pd.DataFrame:
    """Join outcomes only through max_season. Later seasons are not scored here."""
    if int(features["season"].max()) > max_season and (features["season"] > max_season).any():
        features = features[features["season"] <= max_season].copy()
    preds = pd.read_csv(PREDS)
    preds = preds[preds["season"] <= max_season]
    games = pd.read_csv(
        _load_backtest().DATA / "games.csv",
        usecols=["game_id", "result", "total", "spread_line", "total_line"],
        low_memory=False,
    )
    games = games.rename(columns={"total": "actual_total"})
    df = features.merge(preds[["game_id", "margin", "total"]], on="game_id", how="inner")
    df = df.merge(games, on="game_id", how="left")
    df["result"] = pd.to_numeric(df["result"], errors="coerce")
    df["actual_total"] = pd.to_numeric(df["actual_total"], errors="coerce")
    df["spread_line"] = pd.to_numeric(df["spread_line"], errors="coerce")
    df["total_line"] = pd.to_numeric(df["total_line"], errors="coerce")
    if (df["season"] >= HOLDOUT_FIRST_SEASON).any():
        raise RuntimeError("selection attached a holdout season")
    return df


def _knowable_scored(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["unknowable"] == 0)
        & df["result"].notna()
        & df["margin"].notna()
        & df["spread_line"].notna()
    ].copy()


def _margin_target(df: pd.DataFrame) -> np.ndarray:
    """Result minus the v6 margin after the old QB term is removed. The weight is not in this target."""
    stripped = replacement_margin(
        df["margin"], np.zeros(len(df)), df["old_dh"], df["old_da"],
        df["home_transition"], df["away_transition"], df["pass_coef"], 0.0,
    )
    return df["result"].to_numpy(dtype=float) - stripped


def _total_target(df: pd.DataFrame) -> np.ndarray:
    stripped = replacement_total(
        df["total"], np.zeros(len(df)), df["old_dh"], df["old_da"],
        df["home_transition"], df["away_transition"], df["total_pass_coef"], 0.0,
    )
    return df["actual_total"].to_numpy(dtype=float) - stripped


def walk_forward_crps(df: pd.DataFrame) -> tuple[float, list[dict]]:
    """Margin CRPS on knowable 2016–23 games. 2015 is not a training row. 2016 weight is 0."""
    pieces = []
    rows = []
    knowable = _knowable_scored(df)
    knowable = knowable[knowable["season"].between(2016, 2023)]
    for season in range(2016, 2024):
        train = knowable[knowable["season"] < season]
        test = knowable[knowable["season"] == season]
        if len(test) == 0:
            continue
        if len(train) < 30:
            w = 0.0
        else:
            w = ols_through_origin(
                _margin_target(train),
                train["f_margin"].to_numpy(dtype=float),
            )
        mu = replacement_margin(
            test["margin"], test["f_margin"], test["old_dh"], test["old_da"],
            test["home_transition"], test["away_transition"], test["pass_coef"], w,
        )
        y = test["result"].to_numpy(dtype=float)
        crps = v6.crps_normal(y, mu, v6.MARGIN_SIGMA)
        pieces.append(crps)
        base = v6.crps_normal(y, test["margin"].to_numpy(dtype=float), v6.MARGIN_SIGMA)
        rows.append({
            "season": season,
            "n": int(len(test)),
            "w_margin": round(float(w), 6),
            "crps": float(crps.mean()),
            "v6_crps": float(base.mean()),
        })
    if not pieces:
        return float("inf"), rows
    return float(np.concatenate(pieces).mean()), rows


def select() -> None:
    bt = _load_backtest()
    features = build_features(bt)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Persist every season's feature. Scores are not in this file.
    feature_cols = [
        "game_id", "season", "week", "home_team", "away_team", "qb_k",
        "f_margin", "f_total", "old_dh", "old_da", "transition", "unknowable",
        "home_transition", "away_transition",
    ]
    features[feature_cols].to_csv(FEATURES_PATH, index=False)
    feat_sha = hashlib.sha256(FEATURES_PATH.read_bytes()).hexdigest()
    window = attach_pass_coefs(_attach_v6(features, max_season=2023))
    candidates = []
    for qb_k in QB_K_GRID:
        part = window[window["qb_k"] == qb_k]
        crps, by_season = walk_forward_crps(part)
        knowable = _knowable_scored(part[part["season"].between(2016, 2023)])
        w_margin = ols_through_origin(
            _margin_target(knowable),
            knowable["f_margin"].to_numpy(dtype=float),
        )
        tot = knowable[knowable["actual_total"].notna() & knowable["total"].notna()]
        w_total = ols_through_origin(
            _total_target(tot),
            tot["f_total"].to_numpy(dtype=float),
        )
        sel = part[part["season"].between(2016, 2023) & part["result"].notna() & part["spread_line"].notna()]
        candidates.append({
            "qb_k": qb_k,
            "walk_forward_margin_crps": crps,
            "by_season": by_season,
            "frozen_w_margin": round(w_margin, 6),
            "frozen_w_total": round(w_total, 6),
            "knowable_games_2016_23": int((sel["unknowable"] == 0).sum()),
            "unknowable_games_2016_23": int((sel["unknowable"] == 1).sum()),
            "transition_knowable_games_2016_23": int(((sel["unknowable"] == 0) & (sel["transition"] == 1)).sum()),
        })
        print(
            f"k={qb_k} walk-forward CRPS {crps:.4f} "
            f"w_margin {w_margin:.4f} w_total {w_total:.4f}",
            flush=True,
        )
    candidates.sort(key=lambda row: (row["walk_forward_margin_crps"], -row["qb_k"] if TIE_BREAK_HIGHER_K else row["qb_k"]))
    chosen = candidates[0]
    for row in candidates:
        row["selected"] = row["qb_k"] == chosen["qb_k"]
    payload = {
        "model": "v6-blind plus qb-transition",
        "holdout_scored": False,
        "scored_seasons_max": 2023,
        "qb_k_grid": list(QB_K_GRID),
        "tie_break": "higher k",
        "selected_qb_k": chosen["qb_k"],
        "frozen_w_margin": chosen["frozen_w_margin"],
        "frozen_w_total": chosen["frozen_w_total"],
        "walk_forward_margin_crps": chosen["walk_forward_margin_crps"],
        "candidates": candidates,
        "features_sha256": feat_sha,
        "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED, "quantiles": [0.05, 0.95]},
        "formula": (
            "pred_margin = v6_margin - pass_coef * (old_dh * home_transition "
            "- old_da * away_transition) + w_margin * f_margin; "
            "pred_total replaces the same sides on the sum"
        ),
        "training_seasons": "knowable 2016-2023; 2015 excluded",
        "walk_forward_2016": "weight 0, no prior season in the training window",
        "pass_coef_source": "coefs.json by_season; 2024-2026 use the frozen full-sample pass coefficient",
        "note": "2024 and later are not scored in this file.",
    }
    text = json.dumps(payload, indent=2)
    if "2024-25" in text or "holdout_ats" in text:
        raise RuntimeError("selection file contains a holdout score")
    FROZEN_PATH.write_text(text + "\n")
    print(f"wrote {FROZEN_PATH}", flush=True)
    print("HOLDOUT NOT SCORED", flush=True)


def _covers(y, pred, close) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    close = np.asarray(close, dtype=float)
    push = np.isclose(y, close)
    decided = ~push & (pred != close)
    bet_home = pred > close
    covers = (y > close)
    win = (bet_home & covers) | (~bet_home & ~covers)
    return decided, win


def grade_block(df: pd.DataFrame, w_margin: float, w_total: float) -> dict:
    y = df["result"].to_numpy(dtype=float)
    base = df["margin"].to_numpy(dtype=float)
    pred = replacement_margin(
        df["margin"], df["f_margin"], df["old_dh"], df["old_da"],
        df["home_transition"], df["away_transition"], df["pass_coef"], w_margin,
    )
    close = df["spread_line"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(pred) & np.isfinite(close) & np.isfinite(base)
    y, pred, close, base = y[ok], pred[ok], close[ok], base[ok]
    decided, win = _covers(y, pred, close)
    wins = int(win[decided].sum())
    n_ats = int(decided.sum())
    ats = (wins / n_ats) if n_ats else None
    model_crps = float(v6.crps_normal(y, pred, v6.MARGIN_SIGMA).mean()) if len(y) else None
    v6_crps = float(v6.crps_normal(y, base, v6.MARGIN_SIGMA).mean()) if len(y) else None
    close_crps = float(v6.crps_normal(y, close, v6.MARGIN_SIGMA).mean()) if len(y) else None
    model_rmse = float(np.sqrt(np.mean((pred - y) ** 2))) if len(y) else None
    v6_rmse = float(np.sqrt(np.mean((base - y) ** 2))) if len(y) else None
    close_rmse = float(np.sqrt(np.mean((close - y) ** 2))) if len(y) else None
    lo = hi = None
    p_value = None
    if n_ats:
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        draws = rng.integers(0, n_ats, size=(BOOTSTRAP_DRAWS, n_ats))
        rates = win[decided].astype(float)[draws].mean(axis=1)
        lo = float(np.quantile(rates, 0.05))
        hi = float(np.quantile(rates, 0.95))
        from scipy.stats import binomtest

        p_value = float(binomtest(wins, n_ats, 0.5, alternative="greater").pvalue)
    ty = pd.to_numeric(df["actual_total"], errors="coerce").to_numpy(dtype=float)[ok]
    tt = pd.to_numeric(df["total"], errors="coerce").to_numpy(dtype=float)[ok]
    tl = pd.to_numeric(df["total_line"], errors="coerce").to_numpy(dtype=float)[ok]
    tok = np.isfinite(ty) & np.isfinite(tt) & np.isfinite(tl)
    if tok.any():
        t_pred = replacement_total(
            df["total"], df["f_total"], df["old_dh"], df["old_da"],
            df["home_transition"], df["away_transition"], df["total_pass_coef"], w_total,
        )[ok][tok]
        totals_crps = float(v6.crps_normal(ty[tok], t_pred, v6.TOTAL_SIGMA).mean())
        v6_totals = float(v6.crps_normal(ty[tok], tt[tok], v6.TOTAL_SIGMA).mean())
        close_totals = float(v6.crps_normal(ty[tok], tl[tok], v6.TOTAL_SIGMA).mean())
    else:
        totals_crps = v6_totals = close_totals = None
    return {
        "n": int(ok.sum()),
        "n_ats": n_ats,
        "wins": wins,
        "ats": ats,
        "bootstrap_90_lo": lo,
        "bootstrap_90_hi": hi,
        "p_value": p_value,
        "margin_crps": model_crps,
        "v6_margin_crps": v6_crps,
        "close_margin_crps": close_crps,
        "margin_rmse": model_rmse,
        "v6_margin_rmse": v6_rmse,
        "close_margin_rmse": close_rmse,
        "totals_crps": totals_crps,
        "v6_totals_crps": v6_totals,
        "close_totals_crps": close_totals,
        "clv": None,
        "opener_n": 0,
    }


def bar_verdict(summary: dict) -> dict:
    ats = summary["ats"] if summary["ats"] is not None else float("nan")
    n_ok = summary["n_ats"] >= MIN_PLAYS
    ats_ok = bool(ats >= ATS_BAR) and n_ok
    boot_ok = summary["bootstrap_90_lo"] is not None and summary["bootstrap_90_lo"] > 0.50
    crps_ok = (
        summary["margin_crps"] is not None
        and summary["v6_margin_crps"] is not None
        and summary["margin_crps"] <= summary["v6_margin_crps"]
    )
    p_ok = summary["p_value"] is not None and summary["p_value"] < P_BAR
    return {
        "ats_52_5_on_400": {"pass": ats_ok, "ats": summary["ats"], "n_ats": summary["n_ats"], "need_ats": ATS_BAR, "need_n": MIN_PLAYS},
        "bootstrap_90_lo_above_50": {"pass": boot_ok, "lo": summary["bootstrap_90_lo"], "hi": summary["bootstrap_90_hi"]},
        "clv": {"pass": None, "status": "unavailable", "opener_n": 0},
        "no_crps_regression_vs_v6": {
            "pass": crps_ok,
            "ablation": summary["margin_crps"],
            "v6": summary["v6_margin_crps"],
        },
        "p_below_0_01": {"pass": p_ok, "p_value": summary["p_value"]},
        "overall_pass": bool(ats_ok and boot_ok and crps_ok and p_ok),
    }


def _fmt_num(value, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _fmt_grade(name: str, summary: dict) -> list[str]:
    ats = summary["ats"]
    ats_s = f"{ats:.3f}" if ats is not None else ""
    lo, hi = summary["bootstrap_90_lo"], summary["bootstrap_90_hi"]
    boot = f"{lo:.3f}–{hi:.3f}" if lo is not None else ""
    p = summary["p_value"]
    p_s = f"{p:.4g}" if p is not None else ""
    return [
        f"### {name}",
        "",
        "| n | decided | ATS | bootstrap 90% | p (ATS > .50) | Margin CRPS | v6 CRPS | Close CRPS | RMSE | v6 RMSE | Close RMSE |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        (
            f"| {summary['n']} | {summary['n_ats']} | {ats_s} | {boot} | {p_s} | "
            f"{_fmt_num(summary['margin_crps'])} | {_fmt_num(summary['v6_margin_crps'])} | {_fmt_num(summary['close_margin_crps'])} | "
            f"{_fmt_num(summary['margin_rmse'])} | {_fmt_num(summary['v6_margin_rmse'])} | {_fmt_num(summary['close_margin_rmse'])} |"
        ),
        "",
        (
            f"Totals CRPS {summary['totals_crps']:.3f} versus v6 {summary['v6_totals_crps']:.3f} "
            f"and the close {summary['close_totals_crps']:.3f}."
            if summary["totals_crps"] is not None else "Totals CRPS unavailable."
        ),
        "",
        "CLV unavailable. Opener n = 0.",
        "",
    ]


def evaluate() -> None:
    if not FROZEN_PATH.exists() or not FEATURES_PATH.exists():
        raise SystemExit("frozen file missing; run select and commit PREREG.md first")
    frozen = json.loads(FROZEN_PATH.read_text())
    if frozen.get("holdout_scored") is not False:
        raise SystemExit("frozen file was not produced by select")
    digest = hashlib.sha256(FEATURES_PATH.read_bytes()).hexdigest()
    if digest != frozen["features_sha256"]:
        raise SystemExit("features.csv does not match the frozen sha")
    w_m = float(frozen["frozen_w_margin"])
    w_t = float(frozen["frozen_w_total"])
    qb_k = float(frozen["selected_qb_k"])
    features = pd.read_csv(FEATURES_PATH)
    features = features[features["qb_k"] == qb_k].copy()
    preds = pd.read_csv(PREDS)
    games = pd.read_csv(
        _load_backtest().DATA / "games.csv",
        usecols=["game_id", "result", "total", "spread_line", "total_line"],
        low_memory=False,
    ).rename(columns={"total": "actual_total"})
    for col in ("old_dh", "old_da", "home_transition", "away_transition", "f_margin", "f_total"):
        if col not in features.columns:
            raise SystemExit(f"features.csv is missing {col}; re-run select")
    df = features.merge(preds[["game_id", "margin", "total"]], on="game_id", how="inner")
    df = df.merge(games, on="game_id", how="left")
    for col in ("result", "actual_total", "spread_line", "total_line", "margin", "total"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = attach_pass_coefs(df)
    hold = df[df["season"].between(2024, 2025) & (df["unknowable"] == 0) & df["result"].notna()]
    trans = hold[hold["transition"] == 1]
    live = df[(df["season"] == 2026) & (df["unknowable"] == 0) & df["result"].notna()]
    live_trans = live[live["transition"] == 1]
    blocks = {
        "2024–25 holdout, knowable games": grade_block(hold, w_m, w_t),
        "2024–25 transition games only": grade_block(trans, w_m, w_t),
        "2026 live check, knowable games": grade_block(live, w_m, w_t),
        "2026 transition games only": grade_block(live_trans, w_m, w_t),
    }
    verdict = bar_verdict(blocks["2024–25 holdout, knowable games"])
    unknowable_hold = int(((df["season"].between(2024, 2025)) & (df["unknowable"] == 1) & df["result"].notna()).sum())
    unknowable_2026 = int(((df["season"] == 2026) & (df["unknowable"] == 1) & df["result"].notna()).sum())
    lines = [
        "# QB-transition ablation results",
        "",
        "Scored once from the frozen file. Weights were not refit on these seasons.",
        "",
        f"Selected on 2016–23: qb k {qb_k:g}, margin weight {w_m:.6f}, total weight {w_t:.6f}.",
        f"Walk-forward selection CRPS was {frozen['walk_forward_margin_crps']:.4f}.",
        "",
        f"UNKNOWABLE scored games excluded: {unknowable_hold} in 2024–25, {unknowable_2026} in 2026.",
        "",
        "## Bar, 2024–25 knowable games versus the close",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    labels = {
        "ats_52_5_on_400": "ATS ≥ 52.5% on ≥ 400 plays",
        "bootstrap_90_lo_above_50": "bootstrap 90% CI lower bound > 50%",
        "clv": "CLV > 0 where openers exist",
        "no_crps_regression_vs_v6": "no margin-CRPS regression versus v6-blind",
        "p_below_0_01": "one-sided binomial p < 0.01",
    }
    for key, label in labels.items():
        item = verdict[key]
        if key == "clv":
            status = "UNAVAILABLE"
        else:
            status = "PASS" if item["pass"] else "FAIL"
        lines.append(f"| {label} | {status} |")
    lines.append("")
    lines.append(f"Overall: **{'PASS' if verdict['overall_pass'] else 'FAIL'}**.")
    lines.append("")
    lines.append("CLV is unavailable because this snapshot has no opener file. That item is not counted as a pass or a fail.")
    lines.append("")
    for name, summary in blocks.items():
        lines.extend(_fmt_grade(name, summary))
    lines.append("The holdout was not used to change k or either weight.")
    lines.append("")
    RESULTS_PATH.write_text("\n".join(lines) + "\n")
    (OUT_DIR / "verdict.json").write_text(json.dumps({"verdict": verdict, "blocks": blocks, "unknowable_2024_25": unknowable_hold, "unknowable_2026": unknowable_2026}, indent=2) + "\n")
    print(json.dumps(verdict, indent=2), flush=True)
    print(f"wrote {RESULTS_PATH}", flush=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "select"
    if mode == "select":
        select()
    elif mode == "evaluate":
        evaluate()
    else:
        raise SystemExit("mode must be select or evaluate")


if __name__ == "__main__":
    main()
