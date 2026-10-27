"""2016–23 selection for success, explosiveness, and havoc. Does not score 2024 or 2025.

    NFLVERSE_DIR=/path/to/nflverse python research/nfl_sr_xpl_havoc/select.py
"""
from __future__ import annotations

import hashlib
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
DEFS_REV = "b68470fa18119e7b443c13cdf2278bcadebfd686"
PREREG = HERE / "PREREG.md"
OUT_JSON = HERE / "selection.json"
OUT_MD = HERE / "SELECTION.md"
SIGMA = 13.5
Z_CRIT = 1.959963984540054
Z_80 = 0.8416212335729143
V5_BLIND_ALLOW = ("epa_d", "pts_d", "st_d", "qb_d", "hfa")
AUG_ALLOW = V5_BLIND_ALLOW + ("sr_d", "xpl_d", "havoc_d")
V5_FULL = ["mkt_d", "epa_d", "pts_d", "st_d", "qb_d_actual", "hfa"]
PBP_COLS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam", "home_team", "away_team",
    "epa", "success", "pass", "rush", "down", "ydstogo", "yards_gained", "wp",
    "qb_kneel", "qb_spike", "special_teams_play",
    "sack", "tackled_for_loss", "fumble_forced", "interception",
    "pass_defense_1_player_id", "pass_defense_2_player_id",
]
V5_PBP_COLS = [
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
    allowed = set(allow)
    extra = [str(c) for c in columns if str(c) not in allowed]
    if extra or len(list(columns)) != len(set(columns)):
        raise ValueError(
            f"design columns {list(columns)} are not a subset of the allowlist {list(allow)}"
        )


def _self_check() -> None:
    try:
        require_allowlist(["epa_d", "mkt_d", "spread_line", "qb_hit"], AUG_ALLOW)
    except ValueError:
        pass
    else:
        raise RuntimeError("the allowlist did not reject a column outside the contract")
    require_allowlist(list(AUG_ALLOW), AUG_ALLOW)


def crps_normal(y: np.ndarray, mu: np.ndarray, sigma: float = SIGMA) -> np.ndarray:
    z = (np.asarray(y, dtype=float) - np.asarray(mu, dtype=float)) / sigma
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    cdf = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    return sigma * (z * (2.0 * cdf - 1.0) + 2.0 * pdf - 1.0 / math.sqrt(math.pi))


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def git_show(spec: str) -> bytes:
    return subprocess.check_output(["git", "show", spec], cwd=ROOT)


def holdout_count() -> int:
    """Sample size only. Scores are a non-null filter and are not returned."""
    g = pd.read_csv(
        DATA / "games.csv",
        usecols=["season", "game_type", "home_score", "away_score", "spread_line"],
        low_memory=False,
    )
    mask = (
        g["season"].isin([2024, 2025])
        & g["game_type"].eq("REG")
        & g["home_score"].notna()
        & g["away_score"].notna()
        & g["spread_line"].notna()
    )
    return int(mask.sum())


def load_defs():
    src = git_show(f"{DEFS_REV}:features/nfl/defs.py")
    path = Path("/tmp/nfl_defs_sr_xpl.py")
    path.write_bytes(src)
    name = "nfl_defs_sr_xpl"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if module.K_EXPLOSIVE != 12 or module.K_SUCCESS != 12 or module.DEF_WEIGHT != 0.55:
        raise RuntimeError("defs.py constants are not the pinned contract")
    if module.EXPLOSIVE_YARDS != 20 or module.WP_LO != 0.10 or module.WP_HI != 0.90:
        raise RuntimeError("defs.py play filter is not the pinned contract")
    return module


def load_plays() -> pd.DataFrame:
    frames = []
    for season in range(2014, 2024):
        path = DATA / "pbp" / f"play_by_play_{season}.parquet"
        if not path.exists():
            raise RuntimeError(f"missing {path}")
        frames.append(pd.read_parquet(path, columns=PBP_COLS))
        print(f"pbp {season}: {len(frames[-1])}", flush=True)
    plays = pd.concat(frames, ignore_index=True)
    if int(plays["season"].max()) >= 2024:
        raise RuntimeError("play-by-play includes a holdout season")
    return plays


def havoc_flag(defs, scr: pd.DataFrame) -> pd.Series:
    present = defs._player_present
    return (
        scr["sack"].fillna(0).astype(float).eq(1)
        | scr["tackled_for_loss"].fillna(0).astype(float).eq(1)
        | scr["fumble_forced"].fillna(0).astype(float).eq(1)
        | scr["interception"].fillna(0).astype(float).eq(1)
        | scr["pass_defense_1_player_id"].map(present)
        | scr["pass_defense_2_player_id"].map(present)
    )


def havoc_table(defs, plays: pd.DataFrame) -> pd.DataFrame:
    raw = defs._prepare(plays)
    scr = defs._scrimmage(raw)
    if len(scr) == 0:
        raise RuntimeError("no scrimmage plays for havoc")
    scr = scr.copy()
    scr["havoc_num"] = havoc_flag(defs, scr).astype(float)
    scr["havoc_den"] = 1.0
    agg = scr.groupby(["season", "week", "game_id", "posteam"], as_index=False)[["havoc_num", "havoc_den"]].sum()
    spine = defs._spine(raw)
    obs = spine.merge(
        agg,
        left_on=["season", "week", "game_id", "team"],
        right_on=["season", "week", "game_id", "posteam"],
        how="left",
    )
    obs["havoc_num"] = obs["havoc_num"].fillna(0.0)
    obs["havoc_den"] = obs["havoc_den"].fillna(0.0)
    teams = sorted(set(obs["team"]).union(obs["opponent"]))
    panel = defs._ridge_panel(obs, teams, "havoc_num", "havoc_den", defs.K_SUCCESS)
    ti = {team: i for i, team in enumerate(teams)}
    obs["havoc_off"] = [
        defs._lookup_rating(panel, int(s), int(w), ti[t], 0)
        for s, w, t in zip(obs["season"], obs["week"], obs["team"])
    ]
    obs["havoc_def"] = [
        defs._lookup_rating(panel, int(s), int(w), ti[t], 1)
        for s, w, t in zip(obs["season"], obs["week"], obs["team"])
    ]
    return obs[["game_id", "team", "havoc_off", "havoc_def"]]


def matchup_frame(defs, plays: pd.DataFrame) -> pd.DataFrame:
    print("build_features", flush=True)
    team_game, _season = defs.build_features(plays)
    print("havoc ridge", flush=True)
    havoc = havoc_table(defs, plays)
    merged = team_game.merge(havoc, on=["game_id", "team"], how="inner")
    home = merged[merged["is_home"] == 1].set_index("game_id")
    away = merged[merged["is_home"] == 0].set_index("game_id")
    both = home.index.intersection(away.index)
    weight = float(defs.DEF_WEIGHT)

    def edge(off: str, de: str) -> np.ndarray:
        return (
            home.loc[both, off].to_numpy(dtype=float) - away.loc[both, off].to_numpy(dtype=float)
            + weight * (away.loc[both, de].to_numpy(dtype=float) - home.loc[both, de].to_numpy(dtype=float))
        )

    return pd.DataFrame({
        "game_id": both,
        "sr_d": edge("sr_off_adj", "sr_def_adj"),
        "xpl_d": edge("xpl_off_adj", "xpl_def_adj"),
        "havoc_d": edge("havoc_off", "havoc_def"),
    })


def load_main_v5():
    src = git_show("main:backend/app/services/team_ratings_v5.py")
    path = Path("/tmp/team_ratings_v5_sr.py")
    path.write_bytes(src)
    name = "team_ratings_v5_sr"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _materialize_v6():
    dest = Path("/tmp/v6sr")
    (dest / "backend" / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "backend" / "app" / "services").mkdir(parents=True, exist_ok=True)
    (dest / "backend" / "app" / "models").mkdir(parents=True, exist_ok=True)
    for folder in ("backend/app", "backend/app/services", "backend/app/models"):
        (dest / folder / "__init__.py").write_text("")
    for spec, rel in (
        (f"{V6_REF}:backend/app/services/v6_blind.py", "backend/app/services/v6_blind.py"),
        (f"{V6_REF}:backend/scripts/backtest_v6_blind.py", "backend/scripts/backtest_v6_blind.py"),
        (f"{V6_REF}:backend/app/models/seed.py", "backend/app/models/seed.py"),
    ):
        (dest / rel).write_bytes(git_show(spec))
    os.environ["NFLVERSE_DIR"] = str(DATA)
    path = dest / "backend" / "scripts" / "backtest_v6_blind.py"
    name = "v6_backtest_sr"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def v5_feature_frame(v5, sched: pd.DataFrame, starters: dict) -> pd.DataFrame:
    frames = []
    for season in range(2014, 2024):
        path = DATA / "pbp" / f"play_by_play_{season}.parquet"
        pbp = pd.read_parquet(path, columns=V5_PBP_COLS)
        rows = v5.team_game_rows(pbp, season, team_map=canonical_team)
        frames.append(rows)
        print(f"rows {season}: {len(rows)}", flush=True)
    rows = v5.attach_points(pd.concat(frames, ignore_index=True), sched)
    teams = sorted(set(rows["posteam"].dropna()))
    feats = []
    for season in range(2015, 2024):
        weeks = sorted(int(w) for w in sched.loc[sched["season"] == season, "week"].unique())
        for week in weeks:
            wm = v5.build_week_model(rows, sched, season, int(week), teams)
            slate = sched[(sched["season"] == season) & (sched["week"] == week)]
            for g in slate.itertuples(index=False):
                if not (wm.has_team(g.home_team) and wm.has_team(g.away_team)):
                    continue
                hq = g.home_qb_id if isinstance(getattr(g, "home_qb_id", None), str) else None
                aq = g.away_qb_id if isinstance(getattr(g, "away_qb_id", None), str) else None
                f = wm.features(g.home_team, g.away_team, hq, aq)
                f["qb_d_actual"] = f["qb_d"]
                hq24 = starters.get((int(season), int(week), g.home_team))
                aq24 = starters.get((int(season), int(week), g.away_team))
                hq24 = hq24 if isinstance(hq24, str) and hq24 else None
                aq24 = aq24 if isinstance(aq24, str) and aq24 else None
                home_d = float(wm.qb.delta(g.home_team, hq24)) if hq24 else 0.0
                away_d = float(wm.qb.delta(g.away_team, aq24)) if aq24 else 0.0
                f["qb_d"] = home_d - away_d
                f["game_id"] = g.game_id
                feats.append(f)
        print(f"features {season}", flush=True)
    features = pd.DataFrame(feats)
    keep = ["game_id", "season", "week", "game_type", "result", "spread_line", "location"]
    out = sched[keep].merge(features, on="game_id", how="inner")
    out["hfa"] = (out["location"] != "Neutral").astype(float)
    out["result"] = pd.to_numeric(out["result"], errors="coerce")
    out["spread_line"] = pd.to_numeric(out["spread_line"], errors="coerce")
    out["season"] = out["season"].astype(int)
    if int(out["season"].max()) >= 2024:
        raise RuntimeError("v5 features include a holdout season")
    return out


def oos_predictions(frame: pd.DataFrame, cols: list[str], *, allow: tuple[str, ...] | None) -> pd.Series:
    if int(frame["season"].max()) >= 2024:
        raise RuntimeError("refusing to fit a frame that contains 2024 or later")
    if allow is not None:
        require_allowlist(cols, allow)
        require_allowlist(list(frame[cols].columns), allow)
    pieces = []
    for season in range(2016, 2024):
        train = frame[(frame["season"] < season) & (frame["season"] >= 2015)].dropna(subset=[*cols, "result"])
        beta, *_ = np.linalg.lstsq(
            train[cols].to_numpy(dtype=float),
            train["result"].to_numpy(dtype=float),
            rcond=None,
        )
        test = frame[frame["season"] == season].dropna(subset=cols)
        pred = test[cols].to_numpy(dtype=float) @ beta
        pieces.append(pd.Series(pred, index=test["game_id"].to_numpy(), name="pred"))
        print(f"  season {season} n_train={len(train)}", flush=True)
    return pd.concat(pieces)


def selection_sample(frame: pd.DataFrame) -> pd.DataFrame:
    sample = frame[
        (frame["game_type"] == "REG")
        & frame["season"].between(2016, 2023)
        & np.isfinite(frame["result"])
        & np.isfinite(frame["spread_line"])
    ].drop_duplicates("game_id")
    if int(sample["season"].max()) >= 2024:
        raise RuntimeError("selection sample entered the holdout")
    return sample.sort_values("game_id").reset_index(drop=True)


def paired_stats(crps_base: np.ndarray, crps_alt: np.ndarray) -> dict:
    d = np.asarray(crps_base, dtype=float) - np.asarray(crps_alt, dtype=float)
    n = int(d.size)
    mean = float(d.mean())
    sd = float(d.std(ddof=1))
    z = mean / (sd / math.sqrt(n))
    return {"n": n, "mean": mean, "sd": sd, "z": z}


def power_and_mde(mean: float, sd: float, n_h: int) -> dict:
    if n_h <= 0:
        raise RuntimeError("holdout sample size is zero")
    if sd == 0.0:
        power = 1.0 if mean > 0 else 0.0
        mde = 0.0
    else:
        se_h = sd / math.sqrt(n_h)
        power = 1.0 - normal_cdf(Z_CRIT - mean / se_h)
        mde = (Z_CRIT + Z_80) * sd / math.sqrt(n_h)
    return {"n_h": n_h, "power": power, "mde": mde}


def write_markdown(payload: dict) -> None:
    s = payload["selection"]
    v = payload["verdict"]
    levels = payload["levels"]
    audit = payload["feature_audit"]
    lines = [
        "# Selection, 2016–23",
        "",
        f"Verdict: **{v}**.",
        "",
        "The 2024–25 holdout was not fit and was not scored. "
        f"`n_h` = {s['n_h']} is the count of played regular-season games in those seasons with a closing line. "
        "It is the power sample size only.",
        "",
        f"Paired difference is CRPS(v5-blind) minus CRPS(augmented), n = {s['n']}.",
        "",
        f"- mean {s['mean']:.6f}",
        f"- SD {s['sd']:.6f}",
        f"- paired z {s['z']:.4f}",
        f"- MDE at 80% holdout power {s['mde']:.6f} CRPS",
        f"- projected holdout power {s['power']:.4f}",
        "",
        "Go required paired z >= 2 and projected power >= 0.50. "
        + (
            "Both cleared. QA still has to lock PREREG.md before the holdout is run."
            if v == "GO"
            else "The bar was not cleared, so the verdict is NO-SHIP and the holdout stays unspent."
        ),
        "",
        "| Model | 2016–23 REG CRPS |",
        "| --- | ---: |",
        f"| v5-blind | {levels['v5_blind_crps']:.6f} |",
        f"| augmented | {levels['augmented_crps']:.6f} |",
        f"| live v5 | {levels['live_v5_crps']:.6f} |",
        "",
        f"Descriptive paired z versus live v5, CRPS(live v5) minus CRPS(augmented): {payload['versus_live_v5']['z']:.4f}. "
        "That number is not the go/no-go.",
        "",
        f"Feature audit on the selection games: success SD {audit['sr_d_sd']:.6f}, "
        f"explosive SD {audit['xpl_d_sd']:.6f}, havoc SD {audit['havoc_d_sd']:.6f}.",
        "",
        f"Preregistration file SHA-256 `{payload['prereg_sha256']}`.",
        f"defs.py commit `{payload['defs_rev']}`.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines))


def main() -> None:
    _self_check()
    if not (DATA / "games.csv").exists():
        raise SystemExit(f"nflverse mirror not found at {DATA}")
    n_h = holdout_count()
    print(f"holdout sample size n_h={n_h}", flush=True)
    defs = load_defs()
    plays = load_plays()
    context = matchup_frame(defs, plays)
    print("loading schedule through 2023", flush=True)
    mod = _materialize_v6()
    sched = mod.load_schedule()
    sched = sched[sched["season"] <= 2023].copy()
    if int(sched["season"].max()) >= 2024:
        raise RuntimeError("schedule filter failed")
    print("T-24h starters", flush=True)
    starters, _audit = mod.build_starters(sched)
    print("v5 ridges", flush=True)
    v5 = load_main_v5()
    frame = v5_feature_frame(v5, sched, starters)
    frame = frame.merge(context, on="game_id", how="inner")
    sample = selection_sample(frame)
    missing = [c for c in AUG_ALLOW if not np.isfinite(sample[c]).all()]
    if missing:
        raise RuntimeError(f"non-finite selection features {missing}")
    audit = {
        "n": int(len(sample)),
        "sr_d_sd": float(sample["sr_d"].std(ddof=1)),
        "xpl_d_sd": float(sample["xpl_d"].std(ddof=1)),
        "havoc_d_sd": float(sample["havoc_d"].std(ddof=1)),
    }
    print(f"feature audit {audit}", flush=True)
    if min(audit["sr_d_sd"], audit["xpl_d_sd"], audit["havoc_d_sd"]) <= 0:
        raise RuntimeError("a pre-specified differential does not vary")

    print("v5-blind walk-forward", flush=True)
    pred_blind = oos_predictions(frame, list(V5_BLIND_ALLOW), allow=V5_BLIND_ALLOW)
    print("augmented walk-forward", flush=True)
    pred_aug = oos_predictions(frame, list(AUG_ALLOW), allow=AUG_ALLOW)
    print("live v5 walk-forward", flush=True)
    pred_v5 = oos_predictions(frame, V5_FULL, allow=None)
    sample = sample.copy()
    sample["pred_blind"] = sample["game_id"].map(pred_blind)
    sample["pred_aug"] = sample["game_id"].map(pred_aug)
    sample["pred_v5"] = sample["game_id"].map(pred_v5)
    if not np.isfinite(sample[["pred_blind", "pred_aug", "pred_v5"]]).all().all():
        raise RuntimeError("a selection game has no prediction")
    y = sample["result"].to_numpy(dtype=float)
    crps_blind = crps_normal(y, sample["pred_blind"].to_numpy(dtype=float))
    crps_aug = crps_normal(y, sample["pred_aug"].to_numpy(dtype=float))
    crps_v5 = crps_normal(y, sample["pred_v5"].to_numpy(dtype=float))
    stats = paired_stats(crps_blind, crps_aug)
    versus_v5 = paired_stats(crps_v5, crps_aug)
    tail = power_and_mde(stats["mean"], stats["sd"], n_h)
    verdict = "GO" if stats["z"] >= 2.0 and tail["power"] >= 0.50 else "NO-SHIP"
    payload = {
        "window": "2016-2023 REG",
        "holdout": "not run",
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "defs_rev": DEFS_REV,
        "sigma_margin": SIGMA,
        "selection": {**stats, **tail},
        "verdict": verdict,
        "levels": {
            "v5_blind_crps": float(crps_blind.mean()),
            "augmented_crps": float(crps_aug.mean()),
            "live_v5_crps": float(crps_v5.mean()),
        },
        "versus_live_v5": versus_v5,
        "feature_audit": audit,
        "allowlist": list(AUG_ALLOW),
        "pressure_rate": "not in the nflverse play-by-play schema; qb_hit was not substituted",
        "go_rule": "paired z >= 2 and projected holdout power >= 0.50",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    write_markdown(payload)
    print(json.dumps({"verdict": verdict, "selection": payload["selection"], "levels": payload["levels"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
