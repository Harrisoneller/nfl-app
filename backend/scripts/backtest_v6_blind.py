"""Walk-forward fit and close-graded backtest for the v6-blind core.

Hyperparameters are chosen by margin CRPS on 2016–2023 only (2015 is the
first training season). 2024–25 is scored once after the choice is frozen.
2026 is a live check. Closing lines are grading columns, never design columns.

Run from the repo with NFLVERSE_DIR pointing at a local nflverse mirror
(games.csv, pbp/, aux/). Publishing stays off; this script only writes
artifacts.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import v6_blind as v6  # noqa: E402

_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR",
    "OAK": "LV", "SD": "LAC", "STL": "LAR", "HST": "HOU", "BLT": "BAL", "CLV": "CLE",
}


def _seed_teams() -> list[dict]:
    path = ROOT / "backend" / "app" / "models" / "seed.py"
    spec = importlib.util.spec_from_file_location("nfl_seed_table", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.NFL_TEAMS


def nfl_team_ids() -> set[str]:
    return {t["id"] for t in _seed_teams()}


def canonical_team(team: str | None) -> str | None:
    if not isinstance(team, str) or not team.strip():
        return None
    token = team.strip().upper()
    return _ALIASES.get(token, token)

DATA = Path(__import__("os").environ.get("NFLVERSE_DIR", "/tmp/nflverse"))
GAME_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team",
    "epa", "pass", "rush", "qb_kneel", "qb_spike", "wp", "success",
    "qb_dropback", "passer_player_id", "special_teams_play",
]


def _canon(value):
    if not isinstance(value, str) or not value:
        return None
    return canonical_team(value)


def _kickoff(gameday, gametime) -> pd.Timestamp | None:
    """Treat the listed clock as UTC. See the report for why that is conservative."""
    if not isinstance(gameday, str) or len(gameday) < 8:
        return None
    clock = gametime if isinstance(gametime, str) and ":" in gametime else "13:00"
    clock = clock.strip()[:5]
    try:
        return pd.Timestamp(f"{gameday[:10]} {clock}", tz="UTC")
    except (ValueError, TypeError):
        return None


def load_schedule() -> pd.DataFrame:
    g = pd.read_csv(DATA / "games.csv", low_memory=False)
    g["home_team"] = g["home_team"].map(_canon)
    g["away_team"] = g["away_team"].map(_canon)
    g = g[g["game_type"].isin(GAME_TYPES)].copy()
    g["week"] = g["week"].astype(int)
    g["season"] = g["season"].astype(int)
    g["neutral"] = (g["location"] == "Neutral").astype(int) if "location" in g.columns else 0
    g["gameday"] = g["gameday"].astype(str).str.slice(0, 10)
    return g


def aggregate_pbp() -> tuple[pd.DataFrame, pd.DataFrame]:
    team_rows: list[pd.DataFrame] = []
    qb_rows: list[pd.DataFrame] = []
    for year in range(2014, 2027):
        path = DATA / "pbp" / f"play_by_play_{year}.parquet"
        if not path.exists():
            continue
        raw = pd.read_parquet(path, columns=PBP_COLS)
        raw = raw[raw["posteam"].notna() & raw["epa"].notna() & raw["defteam"].notna()]
        if "season_type" in raw.columns:
            raw = raw[raw["season_type"].isin(["REG", "POST"])]
        kneel = raw["qb_kneel"].fillna(0).astype(float) == 1
        spike = raw["qb_spike"].fillna(0).astype(float) == 1
        band = raw["wp"].between(0.10, 0.90)
        keep = ~kneel & ~spike & band
        if "special_teams_play" in raw.columns:
            keep = keep & (raw["special_teams_play"].fillna(0).astype(float) != 1)
        base = raw.loc[keep].copy()
        base["posteam"] = base["posteam"].map(_canon)
        base["defteam"] = base["defteam"].map(_canon)
        base["home_team"] = base["home_team"].map(_canon)
        base = base[base["posteam"].notna() & base["defteam"].notna()]
        base["week"] = base["week"].astype(int)
        base["is_home"] = (
            (base["posteam"] == base["home_team"]) & base["home_team"].notna()
        ).astype(float)
        is_pass = base["pass"].fillna(0).astype(float) == 1
        is_rush = base["rush"].fillna(0).astype(float) == 1
        scr = base.loc[is_pass | is_rush].copy()
        scr["is_pass"] = is_pass.loc[scr.index].astype(float)
        scr["is_rush"] = is_rush.loc[scr.index].astype(float)
        scr["suc"] = scr["success"].fillna(0).astype(float)
        scr["pass_epa"] = scr["epa"] * scr["is_pass"]
        scr["rush_epa"] = scr["epa"] * scr["is_rush"]
        grouped = scr.groupby(["game_id", "posteam", "defteam"], as_index=False).agg(
            week=("week", "first"),
            is_home=("is_home", "first"),
            pass_n=("is_pass", "sum"),
            pass_epa=("pass_epa", "sum"),
            rush_n=("is_rush", "sum"),
            rush_epa=("rush_epa", "sum"),
            suc_n=("epa", "size"),
            suc=("suc", "sum"),
        )
        grouped["season"] = year
        team_rows.append(grouped)

        db = base.loc[base["qb_dropback"].fillna(0).astype(float) == 1].copy()
        db = db[db["passer_player_id"].notna()]
        qb = db.groupby(["game_id", "posteam", "defteam", "passer_player_id"], as_index=False).agg(
            week=("week", "first"),
            is_home=("is_home", "first"),
            n=("epa", "size"),
            epa=("epa", "sum"),
        )
        qb["season"] = year
        qb = qb.rename(columns={"posteam": "team", "passer_player_id": "qb"})
        qb_rows.append(qb)
        print(f"aggregated {year}: team-games {len(grouped)} qb-games {len(qb)}", flush=True)
    return pd.concat(team_rows, ignore_index=True), pd.concat(qb_rows, ignore_index=True)


def apply_neutral(team_games: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    loc = sched[["game_id", "neutral"]].drop_duplicates("game_id")
    out = team_games.merge(loc, on="game_id", how="left")
    out["neutral"] = out["neutral"].fillna(0)
    out.loc[out["neutral"] == 1, "is_home"] = 0.0
    return out


def _ids(frame: pd.DataFrame, teams: list[str]) -> pd.DataFrame:
    ti = {t: i for i, t in enumerate(teams)}
    out = frame[frame["posteam"].isin(ti) & frame["defteam"].isin(ti)].copy() if "posteam" in frame.columns else frame
    return out


def code_teams(team_games: pd.DataFrame, qb_games: pd.DataFrame, teams: list[str]):
    ti = {t: i for i, t in enumerate(teams)}
    tg = team_games[team_games["posteam"].isin(ti) & team_games["defteam"].isin(ti)].copy()
    tg["off_i"] = tg["posteam"].map(ti).astype(int)
    tg["def_i"] = tg["defteam"].map(ti).astype(int)
    qg = qb_games[qb_games["team"].isin(ti) & qb_games["defteam"].isin(ti)].copy()
    qg["off_i"] = qg["team"].map(ti).astype(int)
    qg["def_i"] = qg["defteam"].map(ti).astype(int)
    return tg, qg, ti


def roster_continuity(teams: list[str]) -> dict[tuple[int, str], float]:
    """Share of last season's offensive snaps returning on the week-1 roster."""
    players = pd.read_parquet(DATA / "aux" / "players.parquet", columns=["gsis_id", "pfr_id"])
    pfr_to_gsis = (
        players.dropna(subset=["pfr_id", "gsis_id"])
        .drop_duplicates("pfr_id")
        .set_index("pfr_id")["gsis_id"]
        .to_dict()
    )
    out: dict[tuple[int, str], float] = {}
    snaps_by_year: dict[int, pd.DataFrame] = {}
    rosters_by_year: dict[int, pd.DataFrame] = {}
    for year in range(2014, 2027):
        sp = DATA / "aux" / f"snaps_{year}.parquet"
        ro = DATA / "aux" / f"roster_{year}.parquet"
        if sp.exists():
            s = pd.read_parquet(sp, columns=["season", "game_type", "team", "pfr_player_id", "offense_snaps"])
            s["team"] = s["team"].map(_canon)
            s["gsis_id"] = s["pfr_player_id"].map(pfr_to_gsis)
            s = s[s["game_type"].isin(["REG", "POST", "WC", "DIV", "CON", "SB"])]
            snaps_by_year[year] = s
        if ro.exists():
            r = pd.read_parquet(ro, columns=["season", "week", "team", "gsis_id", "game_type"])
            r["team"] = r["team"].map(_canon)
            rosters_by_year[year] = r
    for year in range(2015, 2027):
        prev, cur = snaps_by_year.get(year - 1), rosters_by_year.get(year)
        if prev is None or cur is None:
            continue
        week1 = cur[(cur["week"] == 1) & (cur["gsis_id"].notna())]
        roster = week1.groupby("team")["gsis_id"].agg(lambda s: set(s.tolist())).to_dict()
        snapped = prev[prev["gsis_id"].notna() & (prev["offense_snaps"].fillna(0) > 0)]
        for team, g in snapped.groupby("team"):
            total = float(g["offense_snaps"].sum())
            if total <= 0 or team not in roster:
                continue
            back = float(g.loc[g["gsis_id"].isin(roster[team]), "offense_snaps"].sum())
            out[(year, team)] = back / total
    print(f"roster continuity pairs {len(out)}", flush=True)
    return out


def _parquet_columns(path: Path, wanted: list[str]) -> pd.DataFrame:
    import pyarrow.parquet as pq

    have = set(pq.read_schema(path).names)
    use = [c for c in wanted if c in have]
    return pd.read_parquet(path, columns=use)


def build_starters(sched: pd.DataFrame) -> tuple[dict[tuple[int, int, str], str | None], dict]:
    """Expected starter at T-24h. Never the final schedule QB id, never last week's starter.

    2014–2024 depth charts are weekly files with no captured_at. The proxy is
    that week's chart, overridden only by an injury row whose date_modified is
    at or before kickoff minus 24 hours and whose report_status is Out.

    2025–2026 depth charts carry a ``dt`` timestamp. The snapshot is the latest
    chart with dt <= kickoff−24h. Those injury files have no date_modified, so
    the injury row is not used. A missing chart leaves the QB delta at 0.
    """
    weekly_frames = []
    dated_frames = []
    inj_frames = []
    injury_seasons_without_timestamp: list[int] = []
    for year in range(2014, 2027):
        dp = DATA / "aux" / f"depth_{year}.parquet"
        ip = DATA / "aux" / f"injuries_{year}.parquet"
        if dp.exists():
            import pyarrow.parquet as pq

            names = set(pq.read_schema(dp).names)
            if "dt" in names and "club_code" not in names:
                d = _parquet_columns(dp, ["dt", "team", "gsis_id", "pos_abb", "pos_name", "pos_rank"])
                d["team"] = d["team"].map(_canon)
                d["dt"] = pd.to_datetime(d["dt"], utc=True, errors="coerce")
                pos = d["pos_abb"].astype(str).str.upper() if "pos_abb" in d.columns else ""
                name = d["pos_name"].astype(str).str.upper() if "pos_name" in d.columns else ""
                d = d[(pos.eq("QB") | name.str.contains("QUARTERBACK")) & d["dt"].notna() & d["gsis_id"].notna()]
                d["pos_rank"] = pd.to_numeric(d["pos_rank"], errors="coerce").fillna(99)
                dated_frames.append(d[["dt", "team", "gsis_id", "pos_rank"]])
            else:
                d = _parquet_columns(dp, [
                    "season", "week", "club_code", "depth_team", "position", "gsis_id", "depth_position",
                ])
                weekly_frames.append(d)
        if ip.exists():
            inj = _parquet_columns(ip, [
                "season", "week", "team", "gsis_id", "report_status", "date_modified",
            ])
            if "date_modified" not in inj.columns:
                injury_seasons_without_timestamp.append(year)
            else:
                inj_frames.append(inj)
    depth = pd.concat(weekly_frames, ignore_index=True) if weekly_frames else pd.DataFrame()
    dated = pd.concat(dated_frames, ignore_index=True) if dated_frames else pd.DataFrame()
    inj = pd.concat(inj_frames, ignore_index=True) if inj_frames else pd.DataFrame()
    audit = {
        "depth_rows": int(len(depth) + len(dated)),
        "injury_rows": int(len(inj)),
        "injury_date_modified_nonnull": float(inj["date_modified"].notna().mean()) if len(inj) else 0.0,
        "depth_has_timestamp_column": bool(len(dated)),
        "dated_depth_seasons": [2025, 2026] if len(dated) else [],
        "injury_seasons_without_date_modified": injury_seasons_without_timestamp,
        "proxy": (
            "2014–2024: weekly depth-chart QB order, overridden only when an injury "
            "row with date_modified at or before kickoff minus 24h has report_status "
            "Out. Those depth charts have season and week and no captured_at, so they "
            "are not a proven T-24h snapshot. 2025–2026: latest depth chart whose dt "
            "is at or before kickoff minus 24h (those files are timestamped). Injury "
            "files for 2025–2026 have no date_modified, so they are not applied. "
            "gametime is compared as UTC. No home_qb_id. No last-start fallback. "
            "Missing chart → QB delta 0."
        ),
    }
    chart: dict[tuple[int, int, str], list[str]] = {}
    if len(depth):
        qb = depth["position"].astype(str).str.upper().eq("QB") if "position" in depth.columns else False
        dpos = depth["depth_position"].astype(str).str.upper().eq("QB") if "depth_position" in depth.columns else False
        depth = depth[qb | dpos].copy()
        depth["team"] = depth["club_code"].map(_canon)
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

    dated_by_team = {}
    if len(dated):
        dated = dated[dated["team"].notna()].sort_values("dt")
        dated_by_team = {team: g.reset_index(drop=True) for team, g in dated.groupby("team", sort=False)}

    if len(inj):
        inj = inj.copy()
        inj["team"] = inj["team"].map(_canon)
        inj["date_modified"] = pd.to_datetime(inj["date_modified"], utc=True, errors="coerce")
        inj = inj[inj["date_modified"].notna() & inj["gsis_id"].notna() & inj["team"].notna()]
        inj["week"] = pd.to_numeric(inj["week"], errors="coerce")
        inj = inj[inj["week"].notna()]
        inj["week"] = inj["week"].astype(int)
        inj["season"] = inj["season"].astype(int)
        inj["out"] = inj["report_status"].astype(str).str.lower().eq("out")
        inj_groups = {
            (int(s), int(w), t): g.sort_values("date_modified")
            for (s, w, t), g in inj.groupby(["season", "week", "team"], sort=False)
        }
    else:
        inj_groups = {}

    def ruled_out(season: int, week: int, team: str, ko: pd.Timestamp | None) -> set[str]:
        group = inj_groups.get((season, week, team))
        if group is None or ko is None:
            return set()
        eligible = group[group["date_modified"] <= ko - pd.Timedelta(hours=24)]
        if len(eligible) == 0:
            return set()
        latest = eligible.groupby("gsis_id", sort=False).tail(1)
        return set(latest.loc[latest["out"], "gsis_id"].tolist())

    def dated_order(team: str, ko: pd.Timestamp | None) -> list[str]:
        g = dated_by_team.get(team)
        if g is None or ko is None or len(g) == 0:
            return []
        cutoff = ko - pd.Timedelta(hours=24)
        sub = g[g["dt"] <= cutoff]
        if len(sub) == 0:
            return []
        latest = sub["dt"].iloc[-1]
        snap = sub[sub["dt"] == latest].sort_values("pos_rank")
        ids: list[str] = []
        for qb_id in snap["gsis_id"].tolist():
            if qb_id not in ids:
                ids.append(qb_id)
        return ids

    starters: dict[tuple[int, int, str], str | None] = {}
    n_games = 0
    n_with = 0
    n_override = 0
    n_missing_chart = 0
    n_dated = 0
    for row in sched.itertuples(index=False):
        season, week = int(row.season), int(row.week)
        if season < 2015:
            continue
        ko = _kickoff(getattr(row, "gameday", None), getattr(row, "gametime", None))
        for team in (row.home_team, row.away_team):
            if team is None:
                continue
            n_games += 1
            weekly = chart.get((season, week, team), [])
            from_dated = dated_order(team, ko)
            order = from_dated or weekly
            if from_dated:
                n_dated += 1
            if not order:
                n_missing_chart += 1
                starters[(season, week, team)] = None
                continue
            outs = ruled_out(season, week, team, ko)
            chosen = next((qb for qb in order if qb not in outs), None)
            if chosen is not None and chosen != order[0]:
                n_override += 1
            if chosen:
                n_with += 1
            else:
                n_missing_chart += 1
            starters[(season, week, team)] = chosen
    audit.update({
        "team_games": n_games,
        "starter_assigned": n_with,
        "starter_rate": (n_with / n_games) if n_games else 0.0,
        "missing_depth_chart": n_missing_chart,
        "injury_overrides_of_qb1": n_override,
        "dated_depth_assignments": n_dated,
    })
    print(
        f"starters assigned {n_with}/{n_games} missing_chart {n_missing_chart} "
        f"overrides {n_override} dated {n_dated}",
        flush=True,
    )
    return starters, audit


def qb_continuity(
    qb_games: pd.DataFrame, starters: dict[tuple[int, int, str], str | None],
) -> dict[tuple[int, str], float]:
    """Week-1 expected starter's share of last season's dropbacks. Unknown starter → 1."""
    counts = qb_games.groupby(["season", "team", "qb"], as_index=False)["n"].sum()
    out: dict[tuple[int, str], float] = {}
    by_season = {int(s): g for s, g in counts.groupby("season")}
    for (season, week, team), qb in starters.items():
        if week != 1 or season < 2015:
            continue
        prev = by_season.get(season - 1)
        if prev is None:
            out[(season, team)] = 1.0
            continue
        team_rows = prev[prev["team"] == team]
        total = float(team_rows["n"].sum())
        if total <= 0 or not qb:
            out[(season, team)] = 1.0
            continue
        share = float(team_rows.loc[team_rows["qb"] == qb, "n"].sum()) / total
        out[(season, team)] = share
    return out


def continuity_vector(
    season: int,
    teams: list[str],
    roster: dict[tuple[int, str], float],
    qb_share: dict[tuple[int, str], float],
) -> np.ndarray:
    vec = np.ones(len(teams), dtype=float)
    for i, team in enumerate(teams):
        r = roster.get((season, team), 1.0)
        q = qb_share.get((season, team), 1.0)
        vec[i] = 0.5 * r + 0.5 * q
    return vec


def metric_cache(
    tg: pd.DataFrame,
    num: str,
    den: str,
    half_life: float,
    k: float,
    teams: list[str],
    roster: dict,
    qb_share: dict,
    weeks_by_season: dict[int, list[int]],
) -> tuple[dict, dict]:
    """As-of ratings and end-of-season pass/rush/success parameters. Market-free."""
    n = len(teams)
    end_off = np.zeros(n)
    end_def = np.zeros(n)
    asof: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, float, float]] = {}
    ended: dict[int, tuple[np.ndarray, np.ndarray, float, float]] = {}
    for season in range(2014, 2027):
        cont = continuity_vector(season, teams, roster, qb_share)
        prior_off = v6.PRESEASON_KEEP * end_off * cont
        prior_def = v6.PRESEASON_KEEP * end_def * cont
        sg = tg[tg["season"] == season]
        weeks = weeks_by_season.get(season, [])
        for week in weeks:
            hist = sg[sg["week"] < week]
            usable = hist[hist[den] > 0]
            if len(usable) == 0:
                asof[(season, week)] = (prior_off.copy(), prior_def.copy(), 0.0, 0.0)
                continue
            age = (week - 1) - usable["week"].to_numpy(dtype=float)
            weight = np.power(0.5, age / half_life)
            y = (usable[num] / usable[den]).to_numpy(dtype=float)
            k_eff = k * float(np.power(0.5, (week - 1) / half_life))
            off, de, ic, hfa = v6.ridge_team(
                usable["off_i"].to_numpy(dtype=int),
                usable["def_i"].to_numpy(dtype=int),
                usable["is_home"].to_numpy(dtype=float),
                y, weight, n, prior_off, prior_def, k_eff,
            )
            g = np.zeros(n)
            np.add.at(g, usable["off_i"].to_numpy(dtype=int), weight)
            off = np.where(g > 0, off, prior_off)
            de = np.where(g > 0, de, prior_def)
            asof[(season, week)] = (off, de, ic, hfa)
        usable = sg[sg[den] > 0]
        if len(usable) == 0:
            ended[season] = (prior_off.copy(), prior_def.copy(), 0.0, 0.0)
            end_off, end_def = prior_off, prior_def
            continue
        asof_week = int(usable["week"].max()) + 1
        age = (asof_week - 1) - usable["week"].to_numpy(dtype=float)
        weight = np.power(0.5, age / half_life)
        y = (usable[num] / usable[den]).to_numpy(dtype=float)
        k_eff = k * float(np.power(0.5, (asof_week - 1) / half_life))
        off, de, ic, hfa = v6.ridge_team(
            usable["off_i"].to_numpy(dtype=int),
            usable["def_i"].to_numpy(dtype=int),
            usable["is_home"].to_numpy(dtype=float),
            y, weight, n, prior_off, prior_def, max(k_eff, 0.5),
        )
        ended[season] = (off, de, ic, hfa)
        end_off, end_def = off, de
    return asof, ended


def draft_table(players: pd.DataFrame) -> dict[str, tuple[str, int | None]]:
    """gsis_id → (round bucket, draft year)."""
    out: dict[str, tuple[str, int | None]] = {}
    for row in players.itertuples(index=False):
        qb = getattr(row, "gsis_id", None)
        if not isinstance(qb, str):
            continue
        rnd = getattr(row, "draft_round", None)
        year = getattr(row, "draft_year", None)
        try:
            rnd_i = int(rnd) if pd.notna(rnd) else None
        except (TypeError, ValueError):
            rnd_i = None
        try:
            year_i = int(year) if pd.notna(year) else None
        except (TypeError, ValueError):
            year_i = None
        if rnd_i == 1:
            bucket = "1"
        elif rnd_i == 2:
            bucket = "2"
        elif rnd_i == 3:
            bucket = "3"
        elif rnd_i is not None:
            bucket = "4-7"
        else:
            bucket = "udfa"
        out[qb] = (bucket, year_i)
    return out


def exp_bucket(season: int, draft_year: int | None) -> str:
    if draft_year is None:
        return "3+"
    exp = season - draft_year
    if exp <= 0:
        return "0"
    if exp == 1:
        return "1"
    if exp == 2:
        return "2"
    return "3+"


def qb_deltas(
    qb_games: pd.DataFrame,
    pass_asof: dict,
    pass_end: dict,
    half_life: float,
    qb_k: float,
    starters: dict,
    teams: list[str],
    ti: dict[str, int],
    draft: dict[str, tuple[str, int | None]],
    weeks_by_season: dict[int, list[int]],
) -> dict[tuple[int, int], np.ndarray]:
    """Starter rating minus the QB value already inside the pass rating, by team index."""
    qg = qb_games.sort_values(["season", "week"]).reset_index(drop=True)
    qg["idx"] = qg["season"].to_numpy(dtype=int) * 22 + qg["week"].to_numpy(dtype=int)
    # Residualize each QB-game with that season's end-of-season pass surface
    # when building the draft prior (past seasons only). Live ratings subtract
    # the as-of pass surface, which uses only games before the prediction week.
    priors: dict[int, dict[tuple[str, str], float]] = {}
    global_prior: dict[int, float] = {}
    for season in range(2015, 2027):
        num = {}
        den = {}
        for yr, (off, de, ic, hfa) in pass_end.items():
            if yr >= season:
                continue
            part = qg[qg["season"] == yr]
            if len(part) == 0:
                continue
            adj = (
                part["epa"].to_numpy(dtype=float) / part["n"].to_numpy(dtype=float)
                - ic
                - de[part["def_i"].to_numpy(dtype=int)]
                - hfa * part["is_home"].to_numpy(dtype=float)
            )
            for qb, n, a in zip(part["qb"], part["n"].to_numpy(dtype=float), adj):
                bucket, drafted = draft.get(qb, ("udfa", None))
                key = (bucket, exp_bucket(yr, drafted))
                num[key] = num.get(key, 0.0) + n * a
                den[key] = den.get(key, 0.0) + n
        priors[season] = {k: num[k] / den[k] for k in num if den[k] > 0}
        if den:
            global_prior[season] = sum(num.values()) / sum(den.values())
        else:
            global_prior[season] = 0.0

    def prior_for(season: int, qb: str) -> float:
        bucket, drafted = draft.get(qb, ("udfa", None))
        table = priors.get(season, {})
        return float(table.get((bucket, exp_bucket(season, drafted)), global_prior.get(season, 0.0)))

    out: dict[tuple[int, int], np.ndarray] = {}
    for season, weeks in weeks_by_season.items():
        if season < 2015:
            continue
        for week in weeks:
            asof_idx = season * 22 + week
            hist = qg[qg["idx"] < asof_idx]
            surface = pass_asof.get((season, week))
            delta = np.zeros(len(teams), dtype=float)
            if surface is None or len(hist) == 0:
                out[(season, week)] = delta
                continue
            age = (asof_idx - 1) - hist["idx"].to_numpy(dtype=float)
            decay = np.power(0.5, np.maximum(age, 0.0) / half_life)
            n = hist["n"].to_numpy(dtype=float)
            # Past seasons use that season's final defense (already over).
            # The current season uses the as-of surface, which excludes this week.
            adj = np.empty(len(hist), dtype=float)
            epa_db = hist["epa"].to_numpy(dtype=float) / n
            def_i = hist["def_i"].to_numpy(dtype=int)
            home = hist["is_home"].to_numpy(dtype=float)
            seasons_h = hist["season"].to_numpy(dtype=int)
            _off, de_now, ic_now, hfa_now = surface
            for yr in np.unique(seasons_h):
                mask = seasons_h == yr
                if yr < season and yr in pass_end:
                    _o, de_y, ic_y, hfa_y = pass_end[yr]
                else:
                    de_y, ic_y, hfa_y = de_now, ic_now, hfa_now
                adj[mask] = epa_db[mask] - ic_y - de_y[def_i[mask]] - hfa_y * home[mask]
            w = n * decay
            tmp = hist.assign(_w=w, _wa=w * adj)
            grouped = tmp.groupby("qb", sort=False)[["_w", "_wa"]].sum()
            ratings = {}
            for qb, grow in grouped.iterrows():
                p0 = prior_for(season, str(qb))
                ratings[qb] = (float(grow["_wa"]) + qb_k * p0) / (float(grow["_w"]) + qb_k)
            # Baked mix: dropback-weighted rating of QBs who produced the team's passes.
            team_w = np.zeros(len(teams))
            team_r = np.zeros(len(teams))
            off_i = tmp["off_i"].to_numpy(dtype=int)
            qb_ids = tmp["qb"].to_numpy()
            ww = tmp["_w"].to_numpy(dtype=float)
            rated = np.array([ratings.get(qb, 0.0) for qb in qb_ids])
            np.add.at(team_w, off_i, ww)
            np.add.at(team_r, off_i, ww * rated)
            for team in teams:
                i = ti[team]
                starter = starters.get((season, week, team))
                if not starter or team_w[i] <= 0:
                    continue
                if starter in ratings:
                    starter_r = ratings[starter]
                else:
                    starter_r = prior_for(season, starter)
                delta[i] = starter_r - (team_r[i] / team_w[i])
            out[(season, week)] = delta
    return out


def build_game_frame(sched: pd.DataFrame, teams: list[str]) -> pd.DataFrame:
    ids = set(teams)
    g = sched[sched["home_team"].isin(ids) & sched["away_team"].isin(ids)].copy()
    g = g[g["season"].between(2015, 2026)]
    return g


def assemble(
    games: pd.DataFrame,
    pass_asof, rush_asof, sr_asof, deltas, ti, def_weight: float,
) -> pd.DataFrame:
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
        rows.append({
            "game_id": rec.game_id,
            "season": int(rec.season),
            "week": int(rec.week),
            "gameday": rec.gameday,
            "home_team": rec.home_team,
            "away_team": rec.away_team,
            "hfa": 0.0 if int(rec.neutral) == 1 else 1.0,
            "home_score": rec.home_score,
            "away_score": rec.away_score,
            "spread_line": rec.spread_line,
            "total_line": rec.total_line,
            "pass_off_h": po[h], "pass_off_a": po[a],
            "pass_def_h": pd_[h], "pass_def_a": pd_[a],
            "rush_off_h": ro[h], "rush_off_a": ro[a],
            "rush_def_h": rd[h], "rush_def_a": rd[a],
            "sr_off_h": so[h], "sr_off_a": so[a],
            "sr_def_h": sd[h], "sr_def_a": sd[a],
            "qb_delta_h": dq[h], "qb_delta_a": dq[a],
        })
    raw = pd.DataFrame(rows)
    comps = v6.component_frame(raw, def_weight=def_weight)
    keep = [
        "game_id", "season", "week", "gameday", "home_team", "away_team", "hfa",
        "home_score", "away_score", "spread_line", "total_line",
    ]
    out = pd.concat([raw[keep].reset_index(drop=True), comps.reset_index(drop=True)], axis=1)
    scored = out["home_score"].notna() & out["away_score"].notna()
    out["result"] = np.where(scored, out["home_score"] - out["away_score"], np.nan)
    out["total_pts"] = np.where(scored, out["home_score"] + out["away_score"], np.nan)
    return out


def score_block(frame: pd.DataFrame, margin_coef, total_coef) -> dict:
    """Grade against the close. Lines are not used to form the prediction."""
    pred_m = v6.apply_margin(frame, margin_coef)
    pred_t = v6.apply_total(frame, total_coef)
    y = frame["result"].to_numpy(dtype=float)
    close = frame["spread_line"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(close) & np.isfinite(pred_m)
    y, close, pred_m = y[ok], close[ok], pred_m[ok]
    if len(y) == 0:
        return {"n": 0}
    model_crps = float(v6.crps_normal(y, pred_m, v6.MARGIN_SIGMA).mean())
    close_crps = float(v6.crps_normal(y, close, v6.MARGIN_SIGMA).mean())
    model_rmse = float(np.sqrt(np.mean((pred_m - y) ** 2)))
    close_rmse = float(np.sqrt(np.mean((close - y) ** 2)))
    model_mae = float(np.mean(np.abs(pred_m - y)))
    push = np.isclose(y, close)
    bet_home = pred_m > close
    home_covers = y > close
    decided = ~push & (pred_m != close)
    wins = int(((bet_home & home_covers) | (~bet_home & ~home_covers))[decided].sum())
    n_ats = int(decided.sum())
    lo, hi = v6.wilson_interval(wins, n_ats)
    abs_edge = np.abs(pred_m - close)
    covered = ((bet_home & home_covers) | (~bet_home & ~home_covers)) & decided
    buckets = []
    edges = [(0, 1), (1, 2), (2, 3), (3, None)]
    for lo_e, hi_e in edges:
        if hi_e is None:
            mask = decided & (abs_edge >= lo_e)
            label = "3+"
        else:
            mask = decided & (abs_edge >= lo_e) & (abs_edge < hi_e)
            label = f"{lo_e}-{hi_e}"
        n = int(mask.sum())
        w = int(covered[mask].sum()) if n else 0
        blo, bhi = v6.wilson_interval(w, n)
        buckets.append({
            "bucket": label, "n": n, "ats": (w / n) if n else None,
            "wilson_lo": blo, "wilson_hi": bhi,
        })
    tot_y = frame["total_pts"].to_numpy(dtype=float)
    tot_line = frame["total_line"].to_numpy(dtype=float)
    pred_total = pred_t
    tok = np.isfinite(tot_y) & np.isfinite(tot_line) & np.isfinite(pred_total)
    totals_crps = (
        float(v6.crps_normal(tot_y[tok], pred_total[tok], v6.TOTAL_SIGMA).mean()) if tok.any() else None
    )
    close_totals_crps = (
        float(v6.crps_normal(tot_y[tok], tot_line[tok], v6.TOTAL_SIGMA).mean()) if tok.any() else None
    )
    return {
        "n": int(ok.sum()),
        "n_ats": n_ats,
        "ats": (wins / n_ats) if n_ats else None,
        "wilson_lo": lo,
        "wilson_hi": hi,
        "margin_crps": model_crps,
        "close_margin_crps": close_crps,
        "margin_rmse": model_rmse,
        "close_margin_rmse": close_rmse,
        "margin_mae": model_mae,
        "totals_crps": totals_crps,
        "close_totals_crps": close_totals_crps,
        "n_totals": int(tok.sum()),
        "buckets": buckets,
        "clv": None,
        "opener_n": 0,
    }


def walk_forward_crps(design: pd.DataFrame) -> float:
    """Mean margin CRPS on 2016–2023 with coefficients fit only on earlier seasons."""
    pieces = []
    for season in range(2016, 2024):
        train = design[(design["season"] < season) & design["result"].notna()]
        test = design[(design["season"] == season) & design["result"].notna()]
        if len(train) < 50 or len(test) == 0:
            continue
        coef = v6.fit_margin(train[list(v6.MARGIN_FEATURES) + ["result"]])
        mu = v6.apply_margin(test, coef)
        y = test["result"].to_numpy(dtype=float)
        pieces.append(v6.crps_normal(y, mu, v6.MARGIN_SIGMA))
    if not pieces:
        return float("inf")
    return float(np.concatenate(pieces).mean())


def data_hash(team_games: pd.DataFrame, sched: pd.DataFrame) -> str:
    cols_tg = ["game_id", "season", "week", "posteam", "defteam", "pass_epa", "pass_n", "rush_epa", "rush_n", "suc", "suc_n"]
    cols_sc = ["game_id", "season", "week", "gameday", "home_team", "away_team", "home_score", "away_score", "location"]
    h = hashlib.sha256()
    tg = team_games[cols_tg].sort_values(["season", "game_id", "posteam"])
    sc = sched.loc[sched["season"].between(2014, 2026), cols_sc].sort_values("game_id")
    h.update(tg.to_csv(index=False).encode())
    h.update(sc.to_csv(index=False).encode())
    return h.hexdigest()


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def fmt_num(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def markdown_report(payload: dict) -> str:
    bar = payload["core_bar"]
    lines = [
        "# v6-blind core",
        "",
        "Market-blind team units and a starter QB delta. Graded against the nflverse close.",
        "`spread_line` is positive when the home team is favored, the same direction as home margin.",
        "A home cover is `result > spread_line`. Pushes are left out of ATS.",
        "Margin CRPS uses sigma 13.5 for the model and the close. Totals CRPS uses sigma 10.",
        "Neither sigma was tuned.",
        "",
        "## Core bar, 2024–25 vs the close",
        "",
        "| Check | Result | Model | Close | Limit |",
        "| --- | --- | --- | --- | --- |",
    ]
    c = bar["crps_within_2pct_of_close"]
    r = bar["rmse_within_close_plus_0_3"]
    a = bar["ats_at_least_0_50"]
    lines.append(f"| CRPS within 2% of the close | {'PASS' if c['pass'] else 'FAIL'} | {c['model']:.3f} | {c['close']:.3f} | {c['limit']:.3f} |")
    lines.append(f"| RMSE no worse than close + 0.3 | {'PASS' if r['pass'] else 'FAIL'} | {r['model']:.3f} | {r['close']:.3f} | {r['limit']:.3f} |")
    lines.append(f"| ATS vs the close ≥ .50 (baseline .490) | {'PASS' if a['pass'] else 'FAIL'} | {a['model']:.3f} |  | .500 |")
    lines += [
        "",
        "The edge on this holdout is not a claim of a significant betting advantage.",
        "Wilson intervals on the buckets below include 0.50 unless a row says otherwise.",
        "",
        "## Opener coverage",
        "",
        "Usable openers, when a file exists, are 2016 (all weeks) and 2023 from week 7 on.",
        "This snapshot has no opener file. 2024–25 opener n = 0. Nothing is filled in.",
        "2026 DraftKings openers are treated as unreliable and are not shown.",
        "CLV needs an open and a close, so CLV is unavailable.",
        "",
        "## Versus the close",
        "",
    ]
    for block in payload["tables"]:
        lines.append(f"### {block['label']}")
        lines.append("")
        lines.append("| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | MAE | Totals CRPS | Close totals CRPS |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        s = block["summary"]
        if s.get("n", 0) == 0:
            lines.append("| 0 | — | — | — | — | — | — | — | — | — |")
        else:
            wilson = "—"
            if s.get("wilson_lo") is not None:
                wilson = f"{s['wilson_lo']:.3f}–{s['wilson_hi']:.3f}"
            lines.append(
                f"| {s['n']} | {fmt_pct(s.get('ats'))} | {wilson} | {fmt_num(s.get('margin_crps'))} | "
                f"{fmt_num(s.get('close_margin_crps'))} | {fmt_num(s.get('margin_rmse'))} | "
                f"{fmt_num(s.get('close_margin_rmse'))} | {fmt_num(s.get('margin_mae'))} | "
                f"{fmt_num(s.get('totals_crps'))} | {fmt_num(s.get('close_totals_crps'))} |"
            )
        lines.append("")
        lines.append("| Edge vs close (points) | n | ATS | Wilson 95% |")
        lines.append("| --- | --- | --- | --- |")
        for b in s.get("buckets") or []:
            span = "—"
            if b.get("wilson_lo") is not None:
                span = f"{b['wilson_lo']:.3f}–{b['wilson_hi']:.3f}"
            lines.append(f"| {b['bucket']} | {b['n']} | {fmt_pct(b.get('ats'))} | {span} |")
        lines.append("")
        lines.append("Versus opener: n = 0. CLV unavailable.")
        lines.append("")
    hp = payload["hyperparams"]
    lines += [
        "## Frozen choices (2016–2023 walk-forward margin CRPS)",
        "",
        f"- Half-life (weeks): {hp['half_life']}",
        f"- Pass shrinkage k (games): {hp['k_pass']}",
        f"- Rush shrinkage k (games): {hp['k_rush']}",
        f"- Success shrinkage k (games): {hp['k_success']}",
        f"- Defense weight: {hp['def_weight']}",
        f"- QB shrinkage k (dropbacks): {hp['qb_k']}",
        f"- Selection CRPS: {payload['selection_crps']:.4f}",
        "",
        "Preseason prior is last season kept at 2/3, then multiplied by",
        "0.5 · roster continuity + 0.5 · week-1 starter's share of last year's dropbacks.",
        "Roster continuity is week-1 roster snaps over last season's offensive snaps.",
        "The prior is the ridge target. Current-season games are not stacked on top of raw prior-season games.",
        "",
        f"Play gate frozen on 2016–2023 vs the close: {payload['play_gate']}.",
        "The core bar above is every holdout game, not the gated subset.",
        "",
        "## Starter",
        "",
        payload["starter_audit"]["proxy"],
        "",
        f"- Injury `date_modified` non-null rate: {payload['starter_audit']['injury_date_modified_nonnull']:.3f}",
        f"- Depth-chart timestamp column: {payload['starter_audit']['depth_has_timestamp_column']}",
        f"- Starter assigned: {payload['starter_audit'].get('starter_assigned')} / {payload['starter_audit'].get('team_games')}",
        f"- Missing depth chart (QB delta left at 0): {payload['starter_audit'].get('missing_depth_chart')}",
        f"- QB1 replaced because the T-24h report said Out: {payload['starter_audit'].get('injury_overrides_of_qb1')}",
        "",
        "2014–2024 depth charts have no captured_at, so the weekly file is the proxy for those seasons. "
        "2025–2026 charts are timestamped and are cut at kickoff minus 24 hours. "
        "Injury files in 2025–2026 have no date_modified, so they are not applied.",
        "",
        "## Snapshot",
        "",
        f"- Data SHA-256: `{payload['data_sha256']}`",
        f"- Design SHA-256: `{payload['design_sha256']}`",
        "",
        "Ablations that are not in this model: QB-transition, luck, wind, offensive line, rest, lookahead, revenge, primetime.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    t0 = time.time()
    teams = sorted(nfl_team_ids())
    print(f"loading schedule and pbp, {len(teams)} teams", flush=True)
    sched = load_schedule()
    team_games, qb_games = aggregate_pbp()
    team_games = apply_neutral(team_games, sched)
    team_games, qb_games, ti = code_teams(team_games, qb_games, teams)
    print(f"team games {len(team_games)} qb games {len(qb_games)}", flush=True)
    roster = roster_continuity(teams)
    starters, starter_audit = build_starters(sched)
    qb_share = qb_continuity(qb_games, starters)
    players = pd.read_parquet(
        DATA / "aux" / "players.parquet",
        columns=["gsis_id", "draft_round", "draft_year"],
    )
    draft = draft_table(players)
    weeks_by_season: dict[int, list[int]] = {}
    sub = sched[sched["season"].between(2014, 2026) & sched["home_team"].isin(teams)]
    for season, g in sub.groupby("season"):
        weeks_by_season[int(season)] = sorted(int(w) for w in g["week"].unique())

    print("fitting metric caches", flush=True)
    pass_cache = {}
    rush_cache = {}
    sr_cache = {}
    for hl in v6.HALF_LIVES:
        for k in v6.K_PASS:
            print(f"  pass hl={hl} k={k}", flush=True)
            pass_cache[(hl, k)] = metric_cache(
                team_games, "pass_epa", "pass_n", hl, k, teams, roster, qb_share, weeks_by_season,
            )
        for k in v6.K_RUSH:
            print(f"  rush hl={hl} k={k}", flush=True)
            rush_cache[(hl, k)] = metric_cache(
                team_games, "rush_epa", "rush_n", hl, k, teams, roster, qb_share, weeks_by_season,
            )
        for k in v6.K_SUCCESS:
            print(f"  success hl={hl} k={k}", flush=True)
            sr_cache[(hl, k)] = metric_cache(
                team_games, "suc", "suc_n", hl, k, teams, roster, qb_share, weeks_by_season,
            )

    games = build_game_frame(sched, teams)
    print("selecting on 2016-2023", flush=True)
    best = None
    best_key = None
    n_tried = 0
    for hl in v6.HALF_LIVES:
        for kp in v6.K_PASS:
            for qbk in v6.QB_K:
                deltas = qb_deltas(
                    qb_games, pass_cache[(hl, kp)][0], pass_cache[(hl, kp)][1],
                    hl, qbk, starters, teams, ti, draft, weeks_by_season,
                )
                for kr in v6.K_RUSH:
                    for ks in v6.K_SUCCESS:
                        for dw in v6.DEF_WEIGHTS:
                            n_tried += 1
                            design = assemble(
                                games, pass_cache[(hl, kp)][0], rush_cache[(hl, kr)][0],
                                sr_cache[(hl, ks)][0], deltas, ti, dw,
                            )
                            window = design[design["season"].between(2015, 2023) & design["result"].notna()]
                            crps = walk_forward_crps(window)
                            key = (hl, kp, kr, ks, dw, qbk)
                            if best is None or crps < best:
                                best = crps
                                best_key = (key, design)
                            print(f"  {key} crps {crps:.4f}", flush=True)
    assert best_key is not None
    (hl, kp, kr, ks, dw, qbk), design = best_key
    print(f"selected {best_key[0]} crps {best:.4f} after {n_tried} configs", flush=True)

    train = design[design["season"].between(2015, 2023) & design["result"].notna()].copy()
    design_cols = list(v6.MARGIN_FEATURES) + list(v6.TOTAL_FEATURES) + ["result", "total_pts", "season", "week", "hfa"]
    # hfa is already in MARGIN_FEATURES; keep a clean column set
    design_cols = ["season", "week", "hfa", "pass_comp", "rush_comp", "sr_comp", "pass_sum", "rush_sum", "sr_sum", "result", "total_pts"]
    design_fit = train[design_cols].copy()
    v6.assert_blind_columns(design_fit.columns)
    margin_coef = v6.fit_margin(design_fit)
    total_coef = v6.fit_total(design_fit)
    by_season = {}
    for season in range(2016, 2024):
        part = design_fit[design_fit["season"] < season]
        by_season[str(season)] = {
            "margin": v6.fit_margin(part),
            "total": v6.fit_total(part),
        }
    frozen = {"margin": margin_coef, "total": total_coef}
    for season in (2024, 2025, 2026):
        by_season[str(season)] = frozen

    def predict_seasons(seasons: list[int]) -> pd.DataFrame:
        part = design[design["season"].isin(seasons)].copy()
        margins = np.full(len(part), np.nan)
        totals = np.full(len(part), np.nan)
        for season, idx in part.groupby("season").groups.items():
            coef_m = by_season[str(int(season))]["margin"]
            coef_t = by_season[str(int(season))]["total"]
            sl = part.loc[idx]
            margins[part.index.get_indexer(idx)] = v6.apply_margin(sl, coef_m)
            totals[part.index.get_indexer(idx)] = v6.apply_total(sl, coef_t)
        part = part.reset_index(drop=True)
        # groupby groups are labels of the original index; recompute simply
        return part

    # Recompute predictions with a clean positional index.
    pred_rows = []
    for season, sl in design.groupby("season"):
        spec = by_season.get(str(int(season)), frozen)
        coef_m = spec["margin"]
        coef_t = spec["total"]
        sl = sl.copy()
        sl["margin"] = v6.apply_margin(sl, coef_m)
        sl["total"] = v6.apply_total(sl, coef_t)
        pred_rows.append(sl)
    predicted = pd.concat(pred_rows, ignore_index=True)

    def block(label: str, seasons: list[int], week_min: int | None = None) -> dict:
        sl = predicted[predicted["season"].isin(seasons) & predicted["result"].notna()]
        if week_min is not None:
            sl = sl[sl["week"] >= week_min]
        return {"label": label, "summary": score_block(sl, by_season[str(seasons[-1])]["margin"], by_season[str(seasons[-1])]["total"]) if False else _score_mixed(sl)}

    def _score_mixed(sl: pd.DataFrame) -> dict:
        if len(sl) == 0:
            return {"n": 0, "buckets": []}
        # Predictions are already on the frame. Grade those, not a single coef.
        y = sl["result"].to_numpy(dtype=float)
        close = sl["spread_line"].to_numpy(dtype=float)
        pred_m = sl["margin"].to_numpy(dtype=float)
        ok = np.isfinite(y) & np.isfinite(close) & np.isfinite(pred_m)
        graded = sl.loc[ok].copy()
        if len(graded) == 0:
            return {"n": 0, "buckets": []}
        # Reuse score_block by temporarily fitting identity: pass predictions through
        # as if they were the only column. Direct calculation here keeps mixed coefs.
        fake = graded.copy()
        # score_block recomputes from coefs. Build a one-feature identity by
        # calling the same stats inline via a shim coef on pass_comp = margin
        # and zeroing other features. That would change totals. Compute directly.
        return _grade_predicted(fake)

    tables = [
        {"label": "2016–2023 selection window vs CLOSE (walk-forward coefficients)", "summary": _grade_predicted(predicted[predicted["season"].between(2016, 2023)])},
        {"label": "2024–25 CLEAN HOLDOUT vs CLOSE (frozen 2015–2023 coefficients)", "summary": _grade_predicted(predicted[predicted["season"].between(2024, 2025)])},
        {"label": "2026 LIVE CHECK vs CLOSE (frozen coefficients, scored games only)", "summary": _grade_predicted(predicted[predicted["season"] == 2026])},
    ]
    hold = tables[1]["summary"]
    bar = v6.core_bar(
        hold["margin_crps"], hold["close_margin_crps"],
        hold["margin_rmse"], hold["close_margin_rmse"],
        hold["ats"] if hold.get("ats") is not None else float("nan"),
    )
    sel = predicted[predicted["season"].between(2016, 2023) & predicted["result"].notna() & predicted["spread_line"].notna()]
    y = sel["result"].to_numpy(dtype=float)
    close = sel["spread_line"].to_numpy(dtype=float)
    pred_m = sel["margin"].to_numpy(dtype=float)
    decided = ~np.isclose(y, close)
    covered = decided & (((pred_m > close) & (y > close)) | ((pred_m < close) & (y < close)))
    gate, gate_rows = v6.select_play_gate(np.abs(pred_m - close)[decided], covered[decided])

    digest = hashlib.sha256(design_fit.to_csv(index=False).encode()).hexdigest()
    snap = data_hash(team_games, sched)
    payload = {
        "model_version": v6.MODEL_VERSION,
        "hyperparams": {
            "half_life": hl, "k_pass": kp, "k_rush": kr, "k_success": ks,
            "def_weight": dw, "qb_k": qbk,
        },
        "selection_crps": best,
        "margin_coefs": margin_coef,
        "total_coefs": total_coef,
        "by_season": by_season,
        "play_gate": gate,
        "play_gate_rows": gate_rows,
        "core_bar": bar,
        "tables": tables,
        "starter_audit": starter_audit,
        "data_sha256": snap,
        "design_sha256": digest,
        "sigma_margin": v6.MARGIN_SIGMA,
        "sigma_total": v6.TOTAL_SIGMA,
        "opener": {
            "n_2024_2025": 0,
            "note": "No opener file in the snapshot. 2016 and 2023-from-week-7 would be the only usable seasons. 2026 DK opens are excluded as unreliable.",
        },
    }

    fixture = ROOT / "backend" / "tests" / "fixtures"
    fixture.mkdir(parents=True, exist_ok=True)
    design_fit.to_csv(fixture / "v6_blind_design.csv", index=False)
    export_path = ROOT / "backend" / "app" / "services" / "v6_blind_export.json"
    export_path.write_text(json.dumps({k: payload[k] for k in (
        "model_version", "hyperparams", "selection_crps", "margin_coefs", "total_coefs",
        "by_season", "play_gate", "play_gate_rows", "core_bar", "starter_audit",
        "data_sha256", "design_sha256", "sigma_margin", "sigma_total", "opener",
    )}, indent=2))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "MODEL_V6_BLIND.md").write_text(markdown_report(payload))

    harness = ROOT / "backtest" / "harness_model"
    priors = harness / "priors"
    priors.mkdir(parents=True, exist_ok=True)
    feature_cols = [
        "game_id", "season", "week", "gameday", "home_team", "away_team", "hfa",
        "pass_comp", "rush_comp", "sr_comp", "pass_sum", "rush_sum", "sr_sum",
    ]
    features = design[feature_cols].copy()
    v6.assert_blind_columns(features.columns)
    features.to_csv(priors / "week_features.csv", index=False)
    preds = predicted.loc[predicted["result"].notna(), [
        "game_id", "season", "week", "gameday", "home_team", "away_team", "margin", "total",
    ]]
    preds.to_csv(priors / "predictions.csv", index=False)
    coefs = {
        "model_version": v6.MODEL_VERSION,
        "hyperparams": payload["hyperparams"],
        "margin": margin_coef,
        "total": total_coef,
        "by_season": by_season,
        "features": list(v6.MARGIN_FEATURES) + list(v6.TOTAL_FEATURES),
        "starter_proxy": starter_audit["proxy"],
        "data_sha256": snap,
    }
    (harness / "coefs.json").write_text(json.dumps(coefs, indent=2))
    feat_sha = hashlib.sha256((priors / "week_features.csv").read_bytes()).hexdigest()
    coef_sha = hashlib.sha256((harness / "coefs.json").read_bytes()).hexdigest()
    readme = f"""# v6-blind priors

Built by `backend/scripts/backtest_v6_blind.py` from the nflverse mirror in
`NFLVERSE_DIR` (play-by-play, games, depth charts, injuries, weekly rosters,
snap counts, players). No spread, total, moneyline, or other price entered
the design.

`week_features.csv` is one row per scheduled game, 2015–2026. Ratings use only
earlier weeks. Garbage time (win probability outside 10–90%), kneels and spikes
are dropped. Team units are opponent-adjusted ridge ratings of pass EPA/play,
rush EPA/play and success rate, shrunk by k games toward a preseason prior
(last season × 2/3 × continuity). Defense is weighted at {dw}. The pass
component adds the expected starter's QB EPA/dropback minus the QB value
already in the team pass rating (shrinkage k = {qbk} dropbacks toward a
draft-round × experience prior fit on earlier seasons only).

Expected starter: weekly depth-chart QB, replaced when an injury row with
`date_modified` ≤ kickoff−24h says Out. Depth charts have no timestamp.
`home_qb_id` is not used. If the depth chart is missing, the QB delta is 0.

Coefficients in `../coefs.json`: `by_season` for 2016–2023 is fit on earlier
seasons only. 2024–2026 use the single 2015–2023 fit.

- week_features.csv SHA-256: `{feat_sha}`
- coefs.json SHA-256: `{coef_sha}`
- input data SHA-256: `{snap}`
"""
    (priors / "README.md").write_text(readme)
    print(json.dumps(bar, indent=2), flush=True)
    print(f"done in {time.time() - t0:.1f}s", flush=True)


def _grade_predicted(sl: pd.DataFrame) -> dict:
    sl = sl[sl["result"].notna()].copy()
    if len(sl) == 0:
        return {"n": 0, "buckets": [], "opener_n": 0, "clv": None}
    y = sl["result"].to_numpy(dtype=float)
    close = pd.to_numeric(sl["spread_line"], errors="coerce").to_numpy(dtype=float)
    pred_m = sl["margin"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(close) & np.isfinite(pred_m)
    y, close, pred_m = y[ok], close[ok], pred_m[ok]
    if len(y) == 0:
        return {"n": 0, "buckets": [], "opener_n": 0, "clv": None}
    model_crps = float(v6.crps_normal(y, pred_m, v6.MARGIN_SIGMA).mean())
    close_crps = float(v6.crps_normal(y, close, v6.MARGIN_SIGMA).mean())
    model_rmse = float(np.sqrt(np.mean((pred_m - y) ** 2)))
    close_rmse = float(np.sqrt(np.mean((close - y) ** 2)))
    model_mae = float(np.mean(np.abs(pred_m - y)))
    push = np.isclose(y, close)
    decided = ~push
    bet_home = pred_m > close
    home_covers = y > close
    wins_mask = decided & ((bet_home & home_covers) | (~bet_home & ~home_covers))
    n_ats = int(decided.sum())
    wins = int(wins_mask.sum())
    lo, hi = v6.wilson_interval(wins, n_ats)
    abs_edge = np.abs(pred_m - close)
    buckets = []
    for lo_e, hi_e, label in ((0, 1, "0-1"), (1, 2, "1-2"), (2, 3, "2-3"), (3, None, "3+")):
        if hi_e is None:
            mask = decided & (abs_edge >= lo_e)
        else:
            mask = decided & (abs_edge >= lo_e) & (abs_edge < hi_e)
        n = int(mask.sum())
        w = int(wins_mask[mask].sum()) if n else 0
        blo, bhi = v6.wilson_interval(w, n)
        buckets.append({"bucket": label, "n": n, "ats": (w / n) if n else None, "wilson_lo": blo, "wilson_hi": bhi})
    tot_y = sl["total_pts"].to_numpy(dtype=float)
    tot_line = pd.to_numeric(sl["total_line"], errors="coerce").to_numpy(dtype=float)
    pred_t = sl["total"].to_numpy(dtype=float)
    tok = np.isfinite(tot_y) & np.isfinite(tot_line) & np.isfinite(pred_t)
    return {
        "n": int(len(y)),
        "n_ats": n_ats,
        "ats": (wins / n_ats) if n_ats else None,
        "wilson_lo": lo,
        "wilson_hi": hi,
        "margin_crps": model_crps,
        "close_margin_crps": close_crps,
        "margin_rmse": model_rmse,
        "close_margin_rmse": close_rmse,
        "margin_mae": model_mae,
        "totals_crps": float(v6.crps_normal(tot_y[tok], pred_t[tok], v6.TOTAL_SIGMA).mean()) if tok.any() else None,
        "close_totals_crps": float(v6.crps_normal(tot_y[tok], tot_line[tok], v6.TOTAL_SIGMA).mean()) if tok.any() else None,
        "n_totals": int(tok.sum()),
        "buckets": buckets,
        "clv": None,
        "opener_n": 0,
    }


if __name__ == "__main__":
    main()
