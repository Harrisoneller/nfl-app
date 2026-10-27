"""One locked nested walk-forward of the feature bundle, 2016–25 regular season.

PREREG.md is not read for tuning and is not modified. Market columns are read
only inside grade().
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
os.environ["NFLVERSE_DIR"] = str(ROOT / "data" / "nflverse")
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import backtest_v6_blind as bt
from app.services import v6_blind as v6

OUT_CSV = ROOT / "backtest" / "feature_bundle" / "per_game.csv"
OUT_JSON = ROOT / "backtest" / "feature_bundle" / "bars.json"

PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team",
    "epa", "pass", "rush", "qb_kneel", "qb_spike", "wp", "success",
    "qb_dropback", "passer_player_id", "special_teams_play",
    "down", "ydstogo", "yards_gained", "interception", "fumble_lost",
]
MARKET_TOKENS = ("spread", "moneyline", "odds", "total_line", "price", "opener")
CELLS = [(6.0, 2), (6.0, 3), (7.0, 2), (7.0, 3), (8.0, 2), (8.0, 3)]
QB_HALF_LIFE = 14.0  # v6 week-index half-life; not in the searched grid
QB_K = 400.0
DEF_W = 0.55
K_PASS, K_RUSH, K_SUCCESS, K_TO = 8.0, 10.0, 12.0, 12.0
MARGIN_X = ["pass_comp", "rush_comp", "sr_comp", "xpl_comp", "to_comp", "hfa"]
TOTAL_X = ["pass_sum", "rush_sum", "sr_sum", "xpl_sum", "to_sum"]
SEED = 20261026
GATE = 3.316


def _banned(columns) -> None:
    bad = []
    for col in columns:
        key = str(col).lower()
        if key in v6.BANNED_INPUTS or any(tok in key for tok in MARKET_TOKENS):
            bad.append(str(col))
    if bad:
        raise RuntimeError(f"market columns reached the feature path: {bad}")


def load_plays(teams: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Family scrimmage plays for the ridges, and every dropback for the QB delta.

    A 3rd/4th-and-short is in neither family. It does not enter a metric ridge.
    On a dropback it faces the mean of the two defensive ratings (early weight 0.5).
    """
    family_frames = []
    qb_frames = []
    for year in range(2014, 2026):
        path = bt.DATA / "pbp" / f"play_by_play_{year}.parquet"
        raw = pd.read_parquet(path, columns=PBP_COLS)
        _banned(raw.columns)
        raw = raw[raw["posteam"].notna() & raw["epa"].notna() & raw["defteam"].notna()]
        raw = raw[raw["season_type"].isin(["REG", "POST"])]
        kneel = raw["qb_kneel"].fillna(0).astype(float) == 1
        spike = raw["qb_spike"].fillna(0).astype(float) == 1
        st = raw["special_teams_play"].fillna(0).astype(float) == 1
        band = raw["wp"].between(0.10, 0.90)
        base = raw.loc[~kneel & ~spike & ~st & band].copy()
        base["posteam"] = base["posteam"].map(bt._canon)
        base["defteam"] = base["defteam"].map(bt._canon)
        base["home_team"] = base["home_team"].map(bt._canon)
        base = base[base["posteam"].isin(teams) & base["defteam"].isin(teams)]
        base["season"] = year
        base["week"] = base["week"].astype(int)
        base["is_home"] = (base["posteam"] == base["home_team"]).astype(float)
        down = pd.to_numeric(base["down"], errors="coerce")
        ytg = pd.to_numeric(base["ydstogo"], errors="coerce")
        early = (down == 1) | ((down == 2) & (ytg <= 7))
        passing = ((down == 2) & (ytg >= 8)) | (down.isin([3, 4]) & (ytg >= 5))
        base["early_flag"] = np.where(early, 1.0, np.where(passing, 0.0, np.nan))
        is_pass = base["pass"].fillna(0).astype(float) == 1
        is_rush = base["rush"].fillna(0).astype(float) == 1
        fam = base.loc[(is_pass | is_rush) & base["early_flag"].notna()].copy()
        fam["early"] = fam["early_flag"].astype(float)
        fam["is_pass"] = is_pass.loc[fam.index]
        fam["is_rush"] = is_rush.loc[fam.index]
        fam["suc"] = fam["success"].fillna(0).astype(float)
        yards = pd.to_numeric(fam["yards_gained"], errors="coerce").fillna(0)
        fam["explosive"] = (yards >= 20).astype(float)
        turn = (
            pd.to_numeric(fam["interception"], errors="coerce").fillna(0)
            + pd.to_numeric(fam["fumble_lost"], errors="coerce").fillna(0)
        )
        fam["turnover"] = turn.clip(lower=0)
        family_frames.append(fam)
        db = base.loc[base["qb_dropback"].fillna(0).astype(float) == 1].copy()
        db = db[db["passer_player_id"].notna()]
        db["early"] = db["early_flag"].fillna(0.5)
        qb_frames.append(db)
        print(f"plays {year} family {len(fam)} dropbacks {len(db)}", flush=True)
    return pd.concat(family_frames, ignore_index=True), pd.concat(qb_frames, ignore_index=True)


def _neutral(frame: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    loc = sched[["game_id", "neutral"]].drop_duplicates("game_id")
    out = frame.merge(loc, on="game_id", how="left")
    out["neutral"] = out["neutral"].fillna(0)
    out.loc[out["neutral"] == 1, "is_home"] = 0.0
    return out


def aggregate(plays: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    p = _neutral(plays, sched)
    p["pass_epa"] = p["epa"] * p["is_pass"].astype(float)
    p["rush_epa"] = p["epa"] * p["is_rush"].astype(float)
    p["is_pass_f"] = p["is_pass"].astype(float)
    p["is_rush_f"] = p["is_rush"].astype(float)
    return p.groupby(["season", "week", "game_id", "posteam", "defteam", "early"], as_index=False).agg(
        is_home=("is_home", "first"),
        pass_n=("is_pass_f", "sum"),
        pass_epa=("pass_epa", "sum"),
        rush_n=("is_rush_f", "sum"),
        rush_epa=("rush_epa", "sum"),
        suc_n=("epa", "size"),
        suc=("suc", "sum"),
        xpl=("explosive", "sum"),
        to=("turnover", "sum"),
    )


def aggregate_qb(dropbacks: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    p = _neutral(dropbacks, sched)
    qb = p.groupby(
        ["season", "week", "game_id", "posteam", "defteam", "passer_player_id"],
        as_index=False,
    ).agg(is_home=("is_home", "first"), n=("epa", "size"), epa=("epa", "sum"), early=("early", "mean"))
    return qb.rename(columns={"posteam": "team", "passer_player_id": "qb"})


def team_weeks(sched: pd.DataFrame) -> dict[tuple[int, str], np.ndarray]:
    out: dict[tuple[int, str], np.ndarray] = {}
    rows = []
    for side in ("home_team", "away_team"):
        part = sched[["season", "week", side]].dropna().rename(columns={side: "team"})
        rows.append(part)
    cal = pd.concat(rows, ignore_index=True)
    cal = cal[cal["team"].notna()]
    for (season, team), g in cal.groupby(["season", "team"]):
        out[(int(season), team)] = np.array(sorted(set(int(w) for w in g["week"])), dtype=int)
    return out


def ages(season: int, teams_s: np.ndarray, hist_week: np.ndarray, asof: int, calendar) -> np.ndarray:
    """Games strictly between the historical week and the week being predicted."""
    out = np.zeros(len(teams_s), dtype=float)
    index: dict[str, np.ndarray] = {}
    for i, team in enumerate(teams_s):
        ws = index.get(team)
        if ws is None:
            ws = calendar.get((season, team), np.array([], dtype=int))
            index[team] = ws
        if len(ws) == 0:
            continue
        hw = int(hist_week[i])
        right = int(np.searchsorted(ws, asof, side="left"))
        left = int(np.searchsorted(ws, hw, side="right"))
        out[i] = float(right - left)
    return out


def games_before(season: int, asof: int, teams: list[str], calendar) -> float:
    counts = []
    for team in teams:
        ws = calendar.get((season, team), np.array([], dtype=int))
        counts.append(float(np.sum(ws < asof)))
    return float(np.median(counts)) if counts else 0.0


def ridge_split(off_i, def_i, early, is_home, y, weight, n_teams, prior_off, prior_de, prior_dp, k):
    n = len(y)
    p = 3 * n_teams + 2
    if n == 0:
        return prior_off.copy(), prior_de.copy(), prior_dp.copy(), 0.0, 0.0
    x = np.zeros((n, p), dtype=float)
    rows = np.arange(n)
    x[rows, off_i] = 1.0
    early = early.astype(float)
    x[rows, n_teams + def_i] = early
    x[rows, 2 * n_teams + def_i] = 1.0 - early
    x[:, 3 * n_teams] = 1.0
    x[:, 3 * n_teams + 1] = is_home
    pen = np.full(p, v6.RIDGE_EPS)
    pen[:3 * n_teams] = k
    xtw = x.T * weight
    a = xtw @ x
    a.flat[:: p + 1] += pen
    rhs = xtw @ y
    rhs[:n_teams] += k * prior_off
    rhs[n_teams:2 * n_teams] += k * prior_de
    rhs[2 * n_teams:3 * n_teams] += k * prior_dp
    try:
        coef = np.linalg.solve(a, rhs)
    except np.linalg.LinAlgError:
        coef = np.linalg.lstsq(a, rhs, rcond=None)[0]
    return (
        coef[:n_teams].copy(),
        coef[n_teams:2 * n_teams].copy(),
        coef[2 * n_teams:3 * n_teams].copy(),
        float(coef[3 * n_teams]),
        float(coef[3 * n_teams + 1]),
    )


def metric_cache(frame, num, den, half_life, k, teams, roster, qb_share, weeks_by_season, calendar):
    _banned(frame.columns)
    n = len(teams)
    ti = {t: i for i, t in enumerate(teams)}
    end_off = np.zeros(n)
    end_de = np.zeros(n)
    end_dp = np.zeros(n)
    asof = {}
    ended = {}
    use = frame[frame[den] > 0].copy()
    use["off_i"] = use["posteam"].map(ti)
    use["def_i"] = use["defteam"].map(ti)
    use = use[use["off_i"].notna() & use["def_i"].notna()]
    for season in range(2014, 2026):
        cont = bt.continuity_vector(season, teams, roster, qb_share)
        prior_off = v6.PRESEASON_KEEP * end_off * cont
        prior_de = v6.PRESEASON_KEEP * end_de * cont
        prior_dp = v6.PRESEASON_KEEP * end_dp * cont
        sg = use[use["season"] == season]
        for week in weeks_by_season.get(season, []):
            hist = sg[sg["week"] < week]
            if len(hist) == 0:
                asof[(season, week)] = (prior_off.copy(), prior_de.copy(), prior_dp.copy(), 0.0, 0.0)
                continue
            age = ages(season, hist["posteam"].to_numpy(), hist["week"].to_numpy(dtype=int), week, calendar)
            decay = np.power(0.5, age / half_life)
            den_v = hist[den].to_numpy(dtype=float)
            # One game's early and passing rows share its recency weight.
            key = hist["game_id"].astype(str) + "|" + hist["posteam"].astype(str)
            tmp = pd.DataFrame({"key": key, "den": den_v, "decay": decay})
            tot = tmp.groupby("key")["den"].transform("sum").to_numpy(dtype=float)
            weight = decay * np.divide(den_v, tot, out=np.zeros_like(den_v), where=tot > 0)
            y = (hist[num] / hist[den]).to_numpy(dtype=float)
            g_before = games_before(season, week, teams, calendar)
            k_eff = k * float(np.power(0.5, g_before / half_life))
            off, de, dp, ic, hfa = ridge_split(
                hist["off_i"].to_numpy(dtype=int),
                hist["def_i"].to_numpy(dtype=int),
                hist["early"].to_numpy(dtype=float),
                hist["is_home"].to_numpy(dtype=float),
                y, weight, n, prior_off, prior_de, prior_dp, max(k_eff, 1e-6),
            )
            seen = np.zeros(n)
            np.add.at(seen, hist["off_i"].to_numpy(dtype=int), weight)
            off = np.where(seen > 0, off, prior_off)
            de = np.where(seen > 0, de, prior_de)
            dp = np.where(seen > 0, dp, prior_dp)
            asof[(season, week)] = (off, de, dp, ic, hfa)
        if len(sg) == 0:
            ended[season] = (prior_off.copy(), prior_de.copy(), prior_dp.copy(), 0.0, 0.0)
            end_off, end_de, end_dp = prior_off, prior_de, prior_dp
            continue
        asof_week = int(sg["week"].max()) + 1
        age = ages(season, sg["posteam"].to_numpy(), sg["week"].to_numpy(dtype=int), asof_week, calendar)
        decay = np.power(0.5, age / half_life)
        den_v = sg[den].to_numpy(dtype=float)
        key = sg["game_id"].astype(str) + "|" + sg["posteam"].astype(str)
        tmp = pd.DataFrame({"key": key, "den": den_v})
        tot = tmp.groupby("key")["den"].transform("sum").to_numpy(dtype=float)
        weight = decay * np.divide(den_v, tot, out=np.zeros_like(den_v), where=tot > 0)
        y = (sg[num] / sg[den]).to_numpy(dtype=float)
        g_before = games_before(season, asof_week, teams, calendar)
        k_eff = k * float(np.power(0.5, g_before / half_life))
        off, de, dp, ic, hfa = ridge_split(
            sg["off_i"].to_numpy(dtype=int),
            sg["def_i"].to_numpy(dtype=int),
            sg["early"].to_numpy(dtype=float),
            sg["is_home"].to_numpy(dtype=float),
            y, weight, n, prior_off, prior_de, prior_dp, max(k_eff, 0.5),
        )
        ended[season] = (off, de, dp, ic, hfa)
        end_off, end_de, end_dp = off, de, dp
    return asof, ended


def league_shares(plays: pd.DataFrame) -> dict[int, float]:
    """Early-down share among family plays from seasons strictly before `season`."""
    out = {}
    running_e = 0.0
    running_n = 0.0
    for season in range(2014, 2026):
        if season >= 2015:
            out[season] = (running_e / running_n) if running_n > 0 else 0.5
        part = plays[plays["season"] == season]
        running_e += float(part["early"].sum())
        running_n += float(len(part))
    return out


def share_cache(plays, half_life, teams, weeks_by_season, calendar, league):
    ti = {t: i for i, t in enumerate(teams)}
    counts = plays.groupby(["season", "week", "posteam"], as_index=False).agg(
        early=("early", "sum"), n=("early", "size"),
    )
    out = {}
    for season, weeks in weeks_by_season.items():
        if season < 2015:
            continue
        sg = counts[counts["season"] == season]
        base = float(league.get(season, 0.5))
        for week in weeks:
            share = np.full(len(teams), base)
            hist = sg[sg["week"] < week]
            if len(hist):
                age = ages(season, hist["posteam"].to_numpy(), hist["week"].to_numpy(dtype=int), week, calendar)
                decay = np.power(0.5, age / half_life)
                early_n = hist["early"].to_numpy(dtype=float)
                play_n = hist["n"].to_numpy(dtype=float)
                hist = hist.assign(_we=early_n * decay, _wn=play_n * decay)
                grp = hist.groupby("posteam")[["_we", "_wn"]].sum()
                for team, row in grp.iterrows():
                    i = ti.get(team)
                    if i is None or row["_wn"] <= 0:
                        continue
                    share[i] = float(row["_we"] / row["_wn"])
            if np.any((share < -1e-9) | (share > 1.0 + 1e-9)):
                raise RuntimeError(f"early share left [0, 1] at {season} week {week}")
            out[(season, week)] = share
    return out


def qb_deltas(qb, pass_asof, pass_end, starters, teams, ti, draft, weeks_by_season):
    """v6 QB delta against the split pass surface. Decay half-life stays 14."""
    qg = qb.sort_values(["season", "week"]).reset_index(drop=True)
    qg["idx"] = qg["season"].to_numpy(dtype=int) * 22 + qg["week"].to_numpy(dtype=int)
    qg["off_i"] = qg["team"].map(ti)
    qg["def_i"] = qg["defteam"].map(ti)
    qg = qg[qg["off_i"].notna() & qg["def_i"].notna()].copy()
    priors: dict[int, dict] = {}
    global_prior: dict[int, float] = {}
    for season in range(2015, 2026):
        num: dict = {}
        den: dict = {}
        for yr, (off, de, dp, ic, hfa) in pass_end.items():
            if yr >= season:
                continue
            part = qg[qg["season"] == yr]
            if len(part) == 0:
                continue
            early = part["early"].to_numpy(dtype=float)
            de_y = de[part["def_i"].to_numpy(dtype=int)] * early + dp[part["def_i"].to_numpy(dtype=int)] * (1.0 - early)
            adj = (
                part["epa"].to_numpy(dtype=float) / part["n"].to_numpy(dtype=float)
                - ic
                - de_y
                - hfa * part["is_home"].to_numpy(dtype=float)
            )
            for qb_id, n, a in zip(part["qb"], part["n"].to_numpy(dtype=float), adj):
                bucket, drafted = draft.get(qb_id, ("udfa", None))
                key = (bucket, bt.exp_bucket(yr, drafted))
                num[key] = num.get(key, 0.0) + n * a
                den[key] = den.get(key, 0.0) + n
        priors[season] = {k: num[k] / den[k] for k in num if den[k] > 0}
        global_prior[season] = (sum(num.values()) / sum(den.values())) if den else 0.0

    def prior_for(season: int, qb_id: str) -> float:
        bucket, drafted = draft.get(qb_id, ("udfa", None))
        table = priors.get(season, {})
        return float(table.get((bucket, bt.exp_bucket(season, drafted)), global_prior.get(season, 0.0)))

    out = {}
    for season, weeks in weeks_by_season.items():
        if season < 2015:
            continue
        for week in weeks:
            asof_idx = season * 22 + week
            hist = qg[qg["idx"] < asof_idx]
            surface = pass_asof.get((season, week))
            delta = np.zeros(len(teams))
            if surface is None or len(hist) == 0:
                out[(season, week)] = delta
                continue
            age = (asof_idx - 1) - hist["idx"].to_numpy(dtype=float)
            decay = np.power(0.5, np.maximum(age, 0.0) / QB_HALF_LIFE)
            n = hist["n"].to_numpy(dtype=float)
            early = hist["early"].to_numpy(dtype=float)
            def_i = hist["def_i"].to_numpy(dtype=int)
            home = hist["is_home"].to_numpy(dtype=float)
            seasons_h = hist["season"].to_numpy(dtype=int)
            _off, de_now, dp_now, ic_now, hfa_now = surface
            adj = np.empty(len(hist))
            epa_db = hist["epa"].to_numpy(dtype=float) / n
            for yr in np.unique(seasons_h):
                mask = seasons_h == yr
                if yr < season and yr in pass_end:
                    _o, de_y, dp_y, ic_y, hfa_y = pass_end[yr]
                else:
                    de_y, dp_y, ic_y, hfa_y = de_now, dp_now, ic_now, hfa_now
                faced = de_y[def_i[mask]] * early[mask] + dp_y[def_i[mask]] * (1.0 - early[mask])
                adj[mask] = epa_db[mask] - ic_y - faced - hfa_y * home[mask]
            w = n * decay
            tmp = hist.assign(_w=w, _wa=w * adj)
            grouped = tmp.groupby("qb", sort=False)[["_w", "_wa"]].sum()
            ratings = {}
            for qb_id, grow in grouped.iterrows():
                p0 = prior_for(season, str(qb_id))
                ratings[qb_id] = (float(grow["_wa"]) + QB_K * p0) / (float(grow["_w"]) + QB_K)
            team_w = np.zeros(len(teams))
            team_r = np.zeros(len(teams))
            off_i = tmp["off_i"].to_numpy(dtype=int)
            qb_ids = tmp["qb"].to_numpy()
            ww = tmp["_w"].to_numpy(dtype=float)
            rated = np.array([ratings.get(qb_id, 0.0) for qb_id in qb_ids])
            np.add.at(team_w, off_i, ww)
            np.add.at(team_r, off_i, ww * rated)
            for team in teams:
                i = ti[team]
                starter = starters.get((season, week, team))
                if not starter or team_w[i] <= 0:
                    continue
                starter_r = ratings[starter] if starter in ratings else prior_for(season, starter)
                delta[i] = starter_r - (team_r[i] / team_w[i])
            out[(season, week)] = delta
    return out


def edge(off_h, off_a, de_h, dp_h, de_a, dp_a, share_h, share_a):
    faced_h = share_h * de_a + (1.0 - share_h) * dp_a
    faced_a = share_a * de_h + (1.0 - share_a) * dp_h
    return (off_h - off_a) + DEF_W * (faced_h - faced_a)


def assemble(games, surfaces, shares, deltas, ti):
    _banned(games.columns)
    rows = []
    for rec in games.itertuples(index=False):
        key = (int(rec.season), int(rec.week))
        if any(key not in s for s in surfaces.values()) or key not in deltas or key not in shares["pass"]:
            continue
        h, a = ti[rec.home_team], ti[rec.away_team]
        sh = {name: float(shares[name][key][h]) for name in shares}
        sa = {name: float(shares[name][key][a]) for name in shares}
        comps = {}
        sums = {}
        for name, surface in surfaces.items():
            off, de, dp, _ic, _hfa = surface[key]
            comps[name] = edge(off[h], off[a], de[h], dp[h], de[a], dp[a], sh[name], sa[name])
            sums[name] = float(off[h] + off[a])
        qb = deltas[key]
        comps["pass"] = comps["pass"] + float(qb[h] - qb[a])
        sums["pass"] = sums["pass"] + float(qb[h] + qb[a])
        scored = pd.notna(rec.home_score) and pd.notna(rec.away_score)
        rows.append({
            "game_id": rec.game_id,
            "season": int(rec.season),
            "week": int(rec.week),
            "home_team": rec.home_team,
            "away_team": rec.away_team,
            "hfa": 0.0 if int(rec.neutral) == 1 else 1.0,
            "result": (float(rec.home_score) - float(rec.away_score)) if scored else np.nan,
            "total_pts": (float(rec.home_score) + float(rec.away_score)) if scored else np.nan,
            "pass_comp": comps["pass"],
            "rush_comp": comps["rush"],
            "sr_comp": comps["sr"],
            "xpl_comp": comps["xpl"],
            "to_comp": comps["to"],
            "pass_sum": sums["pass"],
            "rush_sum": sums["rush"],
            "sr_sum": sums["sr"],
            "xpl_sum": sums["xpl"],
            "to_sum": sums["to"],
        })
    out = pd.DataFrame(rows)
    _banned(out.columns)
    return out


def fit_margin(frame: pd.DataFrame) -> dict[str, float]:
    _banned(frame.columns)
    x = frame.loc[:, MARGIN_X].to_numpy(dtype=float)
    y = frame["result"].to_numpy(dtype=float)
    ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
    coef, *_ = np.linalg.lstsq(x[ok], y[ok], rcond=None)
    keys = ["pass", "rush", "success", "explosive", "turnover", "hfa"]
    return {k: float(v) for k, v in zip(keys, coef)}


def fit_total(frame: pd.DataFrame) -> dict[str, float]:
    _banned(frame.columns)
    s = frame.loc[:, TOTAL_X].to_numpy(dtype=float)
    y = frame["total_pts"].to_numpy(dtype=float)
    ok = np.isfinite(s).all(axis=1) & np.isfinite(y)
    x = np.column_stack([np.ones(int(ok.sum())), s[ok]])
    coef, *_ = np.linalg.lstsq(x, y[ok], rcond=None)
    keys = ["const", "pass", "rush", "success", "explosive", "turnover"]
    return {k: float(v) for k, v in zip(keys, coef)}


def apply_margin(frame, coef) -> np.ndarray:
    return (
        coef["pass"] * frame["pass_comp"].to_numpy(dtype=float)
        + coef["rush"] * frame["rush_comp"].to_numpy(dtype=float)
        + coef["success"] * frame["sr_comp"].to_numpy(dtype=float)
        + coef["explosive"] * frame["xpl_comp"].to_numpy(dtype=float)
        + coef["turnover"] * frame["to_comp"].to_numpy(dtype=float)
        + coef["hfa"] * frame["hfa"].to_numpy(dtype=float)
    )


def apply_total(frame, coef) -> np.ndarray:
    return (
        coef["const"]
        + coef["pass"] * frame["pass_sum"].to_numpy(dtype=float)
        + coef["rush"] * frame["rush_sum"].to_numpy(dtype=float)
        + coef["success"] * frame["sr_sum"].to_numpy(dtype=float)
        + coef["explosive"] * frame["xpl_sum"].to_numpy(dtype=float)
        + coef["turnover"] * frame["to_sum"].to_numpy(dtype=float)
    )


def ready(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[frame["result"].notna() & frame["total_pts"].notna()].copy()
    return out


def cell_crps(feat: pd.DataFrame, holdout_season: int) -> float:
    """CRPS used to pick the cell for holdout_season. Seasons >= holdout_season are absent."""
    use = ready(feat)
    use = use[use["season"] < holdout_season]
    if holdout_season == 2016:
        sl = use[use["season"] == 2015]
        if len(sl) < 50:
            return float("inf")
        coef = fit_margin(sl)
        mu = apply_margin(sl, coef)
        return float(v6.crps_normal(sl["result"].to_numpy(dtype=float), mu, v6.MARGIN_SIGMA).mean())
    pieces = []
    for season in range(2016, holdout_season):
        train = use[use["season"] < season]
        test = use[use["season"] == season]
        if int(train["season"].max()) >= season:
            raise RuntimeError("inner fit saw the test season")
        if len(train) < 50 or len(test) == 0:
            continue
        coef = fit_margin(train)
        mu = apply_margin(test, coef)
        pieces.append(v6.crps_normal(test["result"].to_numpy(dtype=float), mu, v6.MARGIN_SIGMA))
    if not pieces:
        return float("inf")
    return float(np.concatenate(pieces).mean())


def predict_season(feat: pd.DataFrame, season: int) -> pd.DataFrame:
    use = ready(feat)
    train = use[use["season"] < season]
    test = use[use["season"] == season].copy()
    if len(train) and int(train["season"].max()) >= season:
        raise RuntimeError("holdout fit saw the holdout season")
    if len(test) and int(test["season"].min()) != season:
        raise RuntimeError("test frame is not a single season")
    mcoef = fit_margin(train)
    tcoef = fit_total(train)
    test["pred_margin"] = apply_margin(test, mcoef)
    test["pred_total"] = apply_total(test, tcoef)
    return test


def bootstrap_mean(values: np.ndarray, pct: float, n_boot: int = 10000) -> float:
    rng = np.random.default_rng(SEED)
    n = len(values)
    draw = rng.integers(0, n, size=(n_boot, n))
    means = values[draw].mean(axis=1)
    return float(np.percentile(means, pct))


def bootstrap_ci(values: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    n = len(values)
    draw = rng.integers(0, n, size=(10000, n))
    means = values[draw].mean(axis=1)
    lo, hi = np.percentile(means, [5, 95])
    return float(lo), float(hi)


def covers(pred, y, line) -> np.ndarray:
    bet_home = pred > line
    home_covers = y > line
    return (bet_home & home_covers) | (~bet_home & ~home_covers)


def grade(detail: pd.DataFrame) -> dict:
    """detail has bundle and v6 predictions plus grading columns. REG only."""
    if not detail["game_type"].eq("REG").all():
        raise RuntimeError("playoff row reached grading")
    if detail["season"].max() > 2025 or detail["season"].min() < 2016:
        raise RuntimeError("grade season outside 2016-2025")
    y = detail["actual_margin"].to_numpy(dtype=float)
    b = detail["pred_margin_bundle"].to_numpy(dtype=float)
    o = detail["pred_margin_v6"].to_numpy(dtype=float)
    d = detail["crps_v6"].to_numpy(dtype=float) - detail["crps_bundle"].to_numpy(dtype=float)
    ttest = stats.ttest_1samp(d, popmean=0.0, alternative="greater")
    bound = bootstrap_mean(d, 2.5)
    close = detail["close_spread"].to_numpy(dtype=float)
    finite = np.isfinite(y) & np.isfinite(b) & np.isfinite(o) & np.isfinite(close)
    push = np.isclose(y, close)
    either_eq = np.isclose(b, close) | np.isclose(o, close)
    plays = finite & ~push & ~either_eq & (np.abs(b - close) > GATE)
    y_p, b_p, o_p, c_p = y[plays], b[plays], o[plays], close[plays]
    b_cov = covers(b_p, y_p, c_p)
    o_cov = covers(o_p, y_p, c_p)
    only_b = int((b_cov & ~o_cov).sum())
    only_o = int((~b_cov & o_cov).sum())
    n_disc = only_b + only_o
    p_ats = float(stats.binomtest(only_b, n_disc, 0.5, alternative="greater").pvalue) if n_disc else None
    ats_b = float(b_cov.mean()) if len(b_cov) else None
    ats_o = float(o_cov.mean()) if len(o_cov) else None
    diff = (ats_b - ats_o) if ats_b is not None else None
    c = b_cov.astype(float) - o_cov.astype(float)
    ni_lo, ni_hi = bootstrap_ci(c) if len(c) else (None, None)
    return {
        "n_crps": int(len(detail)),
        "mean_crps_bundle": float(detail["crps_bundle"].mean()),
        "mean_crps_v6": float(detail["crps_v6"].mean()),
        "mean_d": float(d.mean()),
        "t_stat": float(ttest.statistic),
        "t_p": float(ttest.pvalue),
        "boot_2_5": bound,
        "crps_pass": bool(ttest.pvalue < 0.025 and bound > 0),
        "n_plays": int(plays.sum()),
        "ats_bundle": ats_b,
        "ats_v6": ats_o,
        "ats_diff": diff,
        "ats_p": p_ats,
        "n_discordant": n_disc,
        "only_bundle": only_b,
        "ats_pass": bool(diff is not None and diff >= 0.01 and p_ats is not None and p_ats < 0.025),
        "ni_lo": ni_lo,
        "ni_hi": ni_hi,
        "ni_pass": bool(ni_lo is not None and ni_lo >= -0.01),
        "cells": detail.groupby("season")["cell"].first().to_dict(),
    }


def main() -> None:
    teams = sorted(bt.nfl_team_ids())
    ti = {t: i for i, t in enumerate(teams)}
    sched = bt.load_schedule()
    market = [c for c in ("spread_line", "total_line", "home_moneyline", "away_moneyline",
                          "away_spread_odds", "home_spread_odds", "under_odds", "over_odds") if c in sched.columns]
    grading = sched[["game_id", "season", "week", "game_type", "spread_line", "total_line",
                     "home_score", "away_score"]].copy()
    sched_f = sched.drop(columns=market)
    _banned(sched_f.columns)
    print("market columns withheld from features", market, flush=True)
    plays, dropbacks = load_plays(set(teams))
    team_rows = aggregate(plays, sched_f)
    qb_rows = aggregate_qb(dropbacks, sched_f)
    print(f"family rows {len(team_rows)} qb rows {len(qb_rows)}", flush=True)
    calendar = team_weeks(sched_f[sched_f["game_type"].isin(bt.GAME_TYPES)])
    roster = bt.roster_continuity(teams)
    starters, _audit = bt.build_starters(sched_f)
    qb_share = bt.qb_continuity(qb_rows.rename(columns={"team": "team"}), starters)
    # qb_continuity groups qb_games by team; our frame uses team. Good.
    players = pd.read_parquet(bt.DATA / "aux" / "players.parquet", columns=["gsis_id", "draft_round", "draft_year"])
    draft = bt.draft_table(players)
    weeks_by_season = {}
    sub = sched_f[sched_f["season"].between(2014, 2025) & sched_f["home_team"].isin(teams)]
    for season, g in sub.groupby("season"):
        weeks_by_season[int(season)] = sorted(int(w) for w in g["week"].unique())
    league = league_shares(plays)
    reg_games = sched_f[
        sched_f["game_type"].eq("REG")
        & sched_f["season"].between(2015, 2025)
        & sched_f["home_team"].isin(teams)
        & sched_f["away_team"].isin(teams)
    ].copy()

    features = {}
    for hl in (6.0, 7.0, 8.0):
        print(f"cache hl={hl}", flush=True)
        pass_asof, pass_end = metric_cache(team_rows, "pass_epa", "pass_n", hl, K_PASS, teams, roster, qb_share, weeks_by_season, calendar)
        rush_asof, _ = metric_cache(team_rows, "rush_epa", "rush_n", hl, K_RUSH, teams, roster, qb_share, weeks_by_season, calendar)
        sr_asof, _ = metric_cache(team_rows, "suc", "suc_n", hl, K_SUCCESS, teams, roster, qb_share, weeks_by_season, calendar)
        to_asof, _ = metric_cache(team_rows, "to", "suc_n", hl, K_TO, teams, roster, qb_share, weeks_by_season, calendar)
        print(f"  qb hl={hl}", flush=True)
        deltas = qb_deltas(qb_rows, pass_asof, pass_end, starters, teams, ti, draft, weeks_by_season)
        one_share = share_cache(plays, hl, teams, weeks_by_season, calendar, league)
        shares = {name: one_share for name in ("pass", "rush", "sr", "xpl", "to")}
        for mult in (2, 3):
            print(f"  explosive x{mult}", flush=True)
            xpl_asof, _ = metric_cache(team_rows, "xpl", "suc_n", hl, K_SUCCESS * mult, teams, roster, qb_share, weeks_by_season, calendar)
            surfaces = {"pass": pass_asof, "rush": rush_asof, "sr": sr_asof, "to": to_asof, "xpl": xpl_asof}
            features[(hl, mult)] = assemble(reg_games, surfaces, shares, deltas, ti)
            print(f"  features {hl}x{mult} {len(features[(hl, mult)])}", flush=True)

    chosen = {}
    pred_rows = []
    for season in range(2016, 2026):
        scores = []
        for hl, mult in CELLS:
            score = cell_crps(features[(hl, mult)], season)
            scores.append((score, hl, mult))
        scores.sort(key=lambda r: (r[0], -r[2], -r[1]))
        # tie within 1e-6: higher multiplier, then longer half-life.
        best = scores[0]
        near = [r for r in scores if abs(r[0] - best[0]) <= 1e-6]
        near.sort(key=lambda r: (-r[2], -r[1]))
        hl, mult = near[0][1], near[0][2]
        chosen[season] = {"half_life": hl, "multiplier": mult, "inner_crps": near[0][0], "grid": [
            {"half_life": h, "multiplier": m, "crps": s} for s, h, m in scores
        ]}
        print(f"season {season} cell {hl:g}x{mult} inner {near[0][0]:.6f}", flush=True)
        part = predict_season(features[(hl, mult)], season)
        part["cell"] = f"{hl:g}x{mult}"
        pred_rows.append(part)
    bundle = pd.concat(pred_rows, ignore_index=True)
    if bundle["season"].max() > 2025:
        raise RuntimeError("scored past 2025")
    if len(bundle) != 2639:
        print(f"WARNING graded rows {len(bundle)} expected 2639", flush=True)

    stored = pd.read_csv(ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv")
    merged = bundle.merge(
        stored[["game_id", "margin", "total"]],
        on="game_id",
        how="inner",
        validate="one_to_one",
    )
    merged = merged.merge(
        grading[["game_id", "game_type", "spread_line", "total_line"]],
        on="game_id",
        how="left",
        validate="one_to_one",
    )
    merged = merged[merged["game_type"].eq("REG") & merged["season"].between(2016, 2025)]
    y = merged["result"].to_numpy(dtype=float)
    merged["actual_margin"] = y
    merged["actual_total"] = merged["total_pts"]
    merged["pred_margin_bundle"] = merged["pred_margin"]
    merged["pred_total_bundle"] = merged["pred_total"]
    merged["pred_margin_v6"] = merged["margin"]
    merged["pred_total_v6"] = merged["total"]
    merged["close_spread"] = merged["spread_line"]
    merged["close_total"] = merged["total_line"]
    merged["espn_bet_open"] = ""
    merged["sigma"] = v6.MARGIN_SIGMA
    merged["crps_v6"] = v6.crps_normal(y, merged["pred_margin_v6"].to_numpy(dtype=float), v6.MARGIN_SIGMA)
    merged["crps_bundle"] = v6.crps_normal(y, merged["pred_margin_bundle"].to_numpy(dtype=float), v6.MARGIN_SIGMA)
    bars = grade(merged)
    bars["chosen"] = {str(k): v for k, v in chosen.items()}
    bars["opener"] = {
        "source": "ESPN BET",
        "n_rows": 0,
        "n_plays": 0,
        "note": "games.csv has no ESPN BET open. The column is blank. Directional ATS and CLV are not estimated.",
    }
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "game_id", "season", "week", "game_type", "cell",
        "pred_margin_v6", "pred_total_v6", "pred_margin_bundle", "pred_total_bundle",
        "sigma", "close_spread", "close_total", "espn_bet_open",
        "actual_margin", "actual_total", "crps_v6", "crps_bundle",
    ]
    out = merged[cols].sort_values(["season", "week", "game_id"])
    out.to_csv(OUT_CSV, index=False)
    OUT_JSON.write_text(json.dumps(bars, indent=2))
    print(json.dumps({k: bars[k] for k in bars if k != "chosen"}, indent=2), flush=True)
    print("wrote", OUT_CSV, "rows", len(out), flush=True)


if __name__ == "__main__":
    main()
