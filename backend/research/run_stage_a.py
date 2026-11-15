"""Assemble free nflverse inputs and run the locked Stage A screen once.

This file does not change a gate, a threshold, a feature definition, or a
hyperparameter. It does not call the harm check or the confirmatory look.
Kickoffs on or after 2027-01-01 America/New_York, and every 2027-season row,
are dropped before a feature is built and before a model is fit.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.services.team_ratings_v5 import (
    DEFAULT_PARAMS,
    attach_points,
    build_week_model,
    team_game_rows,
)
from research.nfl_single_feature.calendar import before_screen_cutoff, eastern_kickoff
from research.nfl_single_feature.design import DesignError
from research.nfl_single_feature.evaluate import exploratory_screen
from research.nfl_single_feature.holm import sign_matches
from research.nfl_single_feature.neutral_pace import neutral_pace_feature
from research.nfl_single_feature.ol_availability import ol_matchup_feature
from research.nfl_single_feature.pressure import (
    assert_pressure_not_season_grain,
    filter_dropbacks,
    pressure_matchup_feature,
)
from research.nfl_single_feature.specs import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    FEATURES,
    SCREEN_UB_QUANTILE,
)

EVALUATION_SHA = "63985bbad78f08bc2be5d794c49d734b1df72376"
CACHE = Path("/tmp/nflverse_stage_a")
RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
RESULTS = BACKEND / "research" / "nfl_single_feature" / "stage_a_results.json"
MANIFEST = BACKEND / "research" / "nfl_single_feature" / "stage_a_manifest.sha256"
ALLOWED_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "LA": "LAR",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",
    "HST": "HOU",
    "BLT": "BAL",
    "CLV": "CLE",
}
NOTES: list[str] = []


def canon(value: object) -> object:
    if not isinstance(value, str):
        return value
    text = value.strip().upper()
    if text in ("", "NAN", "NONE", "NAT"):
        return None
    return ALIASES.get(text, text)


def log(message: str) -> None:
    print(message, flush=True)


def fetch(url: str) -> Path | None:
    """Cache one free nflverse file. A missing file is a coverage gap, not a substitute."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / url.rstrip("/").split("/")[-1]
    if dest.exists() and dest.stat().st_size > 100:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "statletics-research"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            if tmp.exists():
                tmp.unlink()
            return None
        raise
    tmp.replace(dest)
    log(f"downloaded {dest.name}")
    return dest


def fetch_first(urls: list[str]) -> Path | None:
    for url in urls:
        path = fetch(url)
        if path is not None:
            return path
    return None


def read_frame(path: Path, columns: list[str]) -> pd.DataFrame:
    if path.suffix == ".parquet":
        schema = pq.read_schema(path)
        kept = [column for column in columns if column in schema.names]
        table = pq.read_table(path, columns=kept)
        return table.to_pandas()
    header = pd.read_csv(path, nrows=0)
    kept = [column for column in columns if column in header.columns]
    return pd.read_csv(path, usecols=kept)


def kickoff_wall(gameday: object, gametime: object) -> str | None:
    """Eastern wall clock. A missing gametime is 13:00, the nflverse default."""
    if gameday is None or (isinstance(gameday, float) and np.isnan(gameday)):
        return None
    day = str(gameday)[:10]
    if day in ("", "nan", "None", "NaT"):
        return None
    raw = "" if gametime is None or (isinstance(gametime, float) and np.isnan(gametime)) else str(gametime).strip()
    clock = raw[:5] if len(raw) >= 4 and raw.lower() not in ("nan", "none", "nat") else "13:00"
    if len(clock) == 4:
        clock = "0" + clock
    wall = f"{day} {clock}"
    if eastern_kickoff(wall) is None:
        return None
    return wall


def wall_minus_hour(wall: str) -> str:
    stamp = eastern_kickoff(wall)
    if stamp is None:
        raise DesignError("kickoff missing while setting a pre-kickoff quarterback time")
    earlier = stamp - timedelta(hours=1)
    return earlier.strftime("%Y-%m-%d %H:%M")


def assert_locked_files() -> str:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    diff = subprocess.check_output(
        [
            "git", "diff", "--name-only", EVALUATION_SHA, "--",
            "docs/prereg", "backend/research/nfl_single_feature",
        ],
        cwd=REPO,
        text=True,
    ).strip()
    if diff:
        raise SystemExit(f"locked prereg or evaluation files differ from {EVALUATION_SHA}:\n{diff}")
    return head


def load_schedule() -> pd.DataFrame:
    path = fetch(f"{RELEASE}/schedules/games.csv")
    if path is None:
        raise SystemExit("nflverse schedule file is missing")
    frame = read_frame(
        path,
        [
            "game_id", "season", "week", "game_type", "gameday", "gametime",
            "home_team", "away_team", "home_score", "away_score", "location",
        ],
    )
    leaked = [column for column in frame.columns if any(
        part in column.lower() for part in ("spread", "moneyline", "odds", "total_line", "mkt")
    )]
    if leaked:
        raise SystemExit(f"schedule load picked up market columns: {leaked}")
    frame["season"] = frame["season"].astype(int)
    frame["week"] = frame["week"].astype(int)
    frame["game_type"] = frame["game_type"].astype(str).str.upper()
    frame["kickoff"] = [kickoff_wall(day, clock) for day, clock in zip(frame["gameday"], frame["gametime"])]
    frame["home"] = frame["home_team"].map(canon)
    frame["away"] = frame["away_team"].map(canon)
    before = len(frame)
    future = frame["season"].ge(2027) | ~frame["kickoff"].map(before_screen_cutoff)
    NOTES.append(
        f"Schedule rows dropped before any score was used: {int(future.sum())} of {before} "
        "had season 2027 or a kickoff on or after 2027-01-01 America/New_York."
    )
    frame = frame.loc[~future].copy()
    other = ~frame["game_type"].isin(ALLOWED_TYPES)
    NOTES.append(
        f"Schedule rows outside REG/WC/DIV/CON/SB dropped: {int(other.sum())} "
        f"(types {sorted(frame.loc[other, 'game_type'].unique())})."
    )
    frame = frame.loc[~other].copy()
    frame = frame[frame["season"].between(2015, 2026)]
    unplayed = frame["home_score"].isna() | frame["away_score"].isna()
    NOTES.append(
        f"Unplayed games, both scores not present, dropped before fit and scoring: {int(unplayed.sum())}."
    )
    frame = frame.loc[~unplayed].copy()
    frame["margin"] = frame["home_score"].astype(float) - frame["away_score"].astype(float)
    frame["total"] = frame["home_score"].astype(float) + frame["away_score"].astype(float)
    frame["hfa"] = np.where(frame["location"].astype(str).str.lower().eq("neutral"), 0.0, 1.0)
    frame = frame.dropna(subset=["home", "away", "kickoff"]).drop_duplicates("game_id")
    if (~frame["kickoff"].map(before_screen_cutoff)).any() or frame["season"].ge(2027).any():
        raise SystemExit("eligible schedule still contains a 2027 row")
    log(f"eligible scored games {len(frame)} seasons {sorted(frame['season'].unique())}")
    return frame.reset_index(drop=True)


def load_pbp(game_ids: set[str]) -> pd.DataFrame:
    wanted = [
        "game_id", "season", "week", "posteam", "defteam", "home_team", "away_team",
        "epa", "pass", "rush", "qb_kneel", "qb_spike", "wp", "qtr", "score_differential",
        "special_teams_play", "qb_dropback", "passer_player_id",
        "game_seconds_remaining", "drive", "half_seconds_remaining", "quarter_seconds_remaining",
    ]
    pieces = []
    for year in range(2015, 2027):
        path = fetch(f"{RELEASE}/pbp/play_by_play_{year}.parquet")
        if path is None:
            NOTES.append(f"No play-by-play file for {year}.")
            continue
        schema = pq.read_schema(path)
        columns = [column for column in wanted if column in schema.names]
        table = pq.read_table(path, columns=columns)
        plays = table.to_pandas()
        plays = plays[plays["game_id"].isin(game_ids)]
        for column in ("posteam", "defteam", "home_team", "away_team"):
            if column in plays.columns:
                plays[column] = plays[column].map(canon)
        pieces.append(plays)
        log(f"pbp {year} eligible plays {len(plays)}")
        del table
    if not pieces:
        raise SystemExit("no play-by-play loaded")
    return pd.concat(pieces, ignore_index=True)


def base_features(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    lookup: dict[tuple[int, int, str, str], str],
) -> pd.DataFrame:
    """Week-level market-blind v5 features. Stage-2 coefficients are not read."""
    pieces = []
    for season, group in plays.groupby(plays["season"].astype(int)):
        built = team_game_rows(group, int(season), team_map=canon, params=DEFAULT_PARAMS)
        if len(built):
            pieces.append(built)
    rows = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    score_frame = games[["game_id", "home_score", "away_score"]].copy()
    rows = attach_points(rows, score_frame)
    model_schedule = games[["game_id", "season", "week", "home", "away"]].rename(
        columns={"home": "home_team", "away": "away_team"},
    )
    if "spread_line" in model_schedule.columns:
        raise SystemExit("market column reached the base model")
    teams = sorted(set(rows["posteam"].dropna()) | set(rows["defteam"].dropna()))
    models: dict[tuple[int, int], object] = {}
    keys = sorted({(int(s), int(w)) for s, w in zip(games["season"], games["week"])})
    out_rows = []
    for index, (season, week) in enumerate(keys, start=1):
        models[(season, week)] = build_week_model(
            rows, model_schedule, season, week, teams, DEFAULT_PARAMS,
        )
        if index % 25 == 0:
            log(f"base models {index}/{len(keys)}")
    for game in games.itertuples(index=False):
        model = models[(int(game.season), int(game.week))]
        home_qb = lookup.get((int(game.season), int(game.week), game.home, game.game_type))
        away_qb = lookup.get((int(game.season), int(game.week), game.away, game.game_type))
        features = model.features(game.home, game.away, home_qb, away_qb)
        if "mkt_d" in features:
            raise SystemExit("base features include mkt_d")
        out_rows.append({
            "game_id": game.game_id,
            "epa_d": features.get("epa_d", np.nan),
            "pts_d": features.get("pts_d", np.nan),
            "st_d": features.get("st_d", np.nan),
            "qb_d": features.get("qb_d", np.nan),
            "pts_s": features.get("pts_s", np.nan),
            "epa_s": features.get("epa_s", np.nan),
            "env2": features.get("env2", np.nan),
            "plays_s": features.get("plays_s", np.nan),
            "qb_s": features.get("qb_s", np.nan),
        })
    built = pd.DataFrame(out_rows)
    NOTES.append(
        "Base quarterback ids are the weekly depth-chart QB1 (gsis), not the passer who played. "
        "The depth-chart file has no captured_at. home_qb_asof and away_qb_asof are one hour "
        "before kickoff so the pre-game guard sees the weekly listing. "
        "Team ratings use DEFAULT_PARAMS and games from 2015 through this cutoff only. "
        "No shipped stage-2 coefficient is an input."
    )
    return built


def quarterback_lookup(games: pd.DataFrame) -> dict[tuple[int, int, str, str], str]:
    frames = []
    for year in range(2015, 2027):
        path = fetch_first([
            f"{RELEASE}/depth_charts/depth_charts_{year}.csv.gz",
            f"{RELEASE}/depth_charts/depth_charts_{year}.csv",
            f"{RELEASE}/depth_charts/depth_charts_{year}.parquet",
        ])
        if path is None:
            NOTES.append(f"No depth chart file for {year}.")
            continue
        chart = read_frame(
            path,
            ["season", "week", "game_type", "club_code", "depth_team", "position", "gsis_id", "depth_position"],
        )
        frames.append(chart)
    if not frames:
        NOTES.append("No depth charts loaded. Quarterback ids are missing and OL change detection has no starter id.")
        return {}
    chart = pd.concat(frames, ignore_index=True)
    chart["season"] = chart["season"].astype(int)
    chart["week"] = chart["week"].astype(int)
    chart["team"] = chart["club_code"].map(canon)
    chart["game_type"] = chart["game_type"].astype(str).str.upper()
    chart = chart[chart["game_type"].isin(ALLOWED_TYPES)]
    chart = chart[chart["gsis_id"].notna()]
    is_qb = chart["position"].astype(str).str.upper().eq("QB")
    if "depth_position" in chart.columns:
        is_qb = is_qb | chart["depth_position"].astype(str).str.upper().eq("QB")
    starters = chart[is_qb & chart["depth_team"].astype(str).isin(["1", "1.0"])].copy()
    starters = starters.sort_values(["season", "week", "team", "game_type", "gsis_id"])
    starters = starters.drop_duplicates(["season", "week", "team", "game_type"], keep="first")
    lookup = {
        (int(row.season), int(row.week), row.team, row.game_type): str(row.gsis_id)
        for row in starters.itertuples(index=False)
    }
    covered = 0
    for game in games.itertuples(index=False):
        if (int(game.season), int(game.week), game.home, game.game_type) in lookup:
            covered += 1
    NOTES.append(
        f"Depth-chart QB1 found for the home team on {covered} of {len(games)} eligible games."
    )
    return lookup


def attach_quarterbacks(games: pd.DataFrame, lookup: dict[tuple[int, int, str, str], str]) -> pd.DataFrame:
    out = games.copy()
    home_ids = []
    away_ids = []
    for game in out.itertuples(index=False):
        key_home = (int(game.season), int(game.week), game.home, game.game_type)
        key_away = (int(game.season), int(game.week), game.away, game.game_type)
        home_ids.append(lookup.get(key_home))
        away_ids.append(lookup.get(key_away))
    out["home_qb"] = home_ids
    out["away_qb"] = away_ids
    out["home_qb_asof"] = out["kickoff"].map(wall_minus_hour)
    out["away_qb_asof"] = out["home_qb_asof"]
    return out


def load_players() -> pd.DataFrame:
    path = fetch_first([
        f"{RELEASE}/players/players.csv.gz",
        f"{RELEASE}/players/players.csv",
        f"{RELEASE}/players/players.parquet",
    ])
    if path is None:
        raise SystemExit("nflverse players id table is missing; OL snaps cannot be joined without substituting names")
    players = read_frame(path, ["gsis_id", "pfr_id"])
    players = players.dropna(subset=["gsis_id", "pfr_id"]).drop_duplicates("pfr_id")
    return players


def load_snaps_summed(game_ids: set[str], players: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for year in range(2015, 2027):
        path = fetch_first([
            f"{RELEASE}/snap_counts/snap_counts_{year}.csv.gz",
            f"{RELEASE}/snap_counts/snap_counts_{year}.csv",
            f"{RELEASE}/snap_counts/snap_counts_{year}.parquet",
        ])
        if path is None:
            NOTES.append(f"No snap counts for {year}.")
            continue
        snaps = read_frame(
            path,
            ["game_id", "season", "week", "team", "pfr_player_id", "position", "offense_snaps", "offense_pct"],
        )
        snaps = snaps[snaps["game_id"].isin(game_ids)]
        frames.append(snaps)
        log(f"snaps {year} eligible rows {len(snaps)}")
    if not frames:
        raise SystemExit("no snap counts loaded")
    snaps = pd.concat(frames, ignore_index=True)
    snaps["team"] = snaps["team"].map(canon)
    snaps["season"] = snaps["season"].astype(int)
    snaps["week"] = snaps["week"].astype(int)
    id_map = dict(zip(players["pfr_id"].astype(str), players["gsis_id"].astype(str)))
    snaps["player_id"] = snaps["pfr_player_id"].astype(str).map(id_map)
    unmatched = int(snaps["player_id"].isna().sum())
    NOTES.append(
        f"Snap rows with no gsis crosswalk from the players pfr_id table: {unmatched} of {len(snaps)}. "
        "Those rows are dropped. Injury status is not inferred from who played."
    )
    snaps = snaps[snaps["player_id"].notna()].copy()
    snaps["offense_snaps"] = pd.to_numeric(snaps["offense_snaps"], errors="coerce").fillna(0.0)
    pct = pd.to_numeric(snaps["offense_pct"], errors="coerce")
    positive = pct[pct > 0]
    scale = 100.0 if len(positive) and float(positive.quantile(0.9)) > 1.5 else 1.0
    snaps["_team_est"] = (snaps["offense_snaps"] / (pct / scale)).where(pct > 0)
    team_total = snaps.groupby(["game_id", "team"])["_team_est"].median()
    snaps = snaps.groupby(
        ["season", "week", "game_id", "team", "player_id"], as_index=False,
    ).agg(offense_snaps=("offense_snaps", "sum"), position=("position", "last"))
    snaps["team_offense_snaps"] = [
        team_total.get((game_id, team), np.nan)
        for game_id, team in zip(snaps["game_id"], snaps["team"])
    ]
    return snaps.drop(columns=[column for column in snaps.columns if column.startswith("_")], errors="ignore")


def load_injuries(games: pd.DataFrame) -> pd.DataFrame:
    eligible_weeks = set(zip(games["season"].astype(int), games["week"].astype(int), games["home"]))
    eligible_weeks |= set(zip(games["season"].astype(int), games["week"].astype(int), games["away"]))
    frames = []
    for year in range(2015, 2027):
        path = fetch_first([
            f"{RELEASE}/injuries/injuries_{year}.csv.gz",
            f"{RELEASE}/injuries/injuries_{year}.csv",
            f"{RELEASE}/injuries/injuries_{year}.parquet",
        ])
        if path is None:
            NOTES.append(f"No injury file for {year}.")
            continue
        injuries = read_frame(
            path,
            ["season", "week", "team", "gsis_id", "report_status", "date_modified", "position"],
        )
        frames.append(injuries)
        log(f"injuries {year} rows {len(injuries)}")
    if not frames:
        NOTES.append("No injury files loaded. OL availability cannot be verified.")
        return pd.DataFrame(columns=["season", "week", "team", "player_id", "report_status", "report_time"])
    injuries = pd.concat(frames, ignore_index=True)
    injuries["season"] = injuries["season"].astype(int)
    injuries["week"] = injuries["week"].astype(int)
    injuries["team"] = injuries["team"].map(canon)
    keep = [
        (int(season), int(week), team) in eligible_weeks
        for season, week, team in zip(injuries["season"], injuries["week"], injuries["team"])
    ]
    injuries = injuries.loc[keep].copy()
    injuries = injuries[injuries["gsis_id"].notna()]
    injuries["player_id"] = injuries["gsis_id"].astype(str)
    injuries["report_time"] = injuries["date_modified"]
    blank_time = injuries["report_time"].isna() | injuries["report_time"].astype(str).str.lower().isin(["", "nan", "none", "nat"])
    NOTES.append(
        f"Injury rows on eligible team-weeks with a blank date_modified: {int(blank_time.sum())} of {len(injuries)}. "
        "A blank or not-strictly-before-kickoff time leaves that game unverified. No time was filled in."
    )
    return injuries[["season", "week", "team", "player_id", "report_status", "report_time"]]


def prior_time_to_throw(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Attempts-weighted NGS average time to throw from weeks strictly before the team-game."""
    frames = []
    for year in range(2016, 2027):
        path = fetch_first([
            f"{RELEASE}/nextgen_stats/ngs_{year}_passing.csv.gz",
            f"{RELEASE}/nextgen_stats/ngs_{year}_passing.csv",
            f"{RELEASE}/nextgen_stats/ngs_{year}_passing.parquet",
        ])
        if path is None:
            NOTES.append(f"No weekly NGS passing file for {year}.")
            continue
        ngs = read_frame(
            path,
            ["season", "week", "team_abbr", "player_gsis_id", "avg_time_to_throw", "attempts"],
        )
        frames.append(ngs)
    if not frames:
        NOTES.append("No NGS passing files. Time to throw is missing. Sacks were not used as a substitute.")
        return pd.DataFrame(columns=["game_id", "team", "qb_att", "qb_att_time"])
    ngs = pd.concat(frames, ignore_index=True)
    ngs["season"] = ngs["season"].astype(int)
    ngs["week"] = pd.to_numeric(ngs["week"], errors="coerce")
    dropped_weeks = int((ngs["week"].isna() | (ngs["week"] <= 0)).sum())
    NOTES.append(
        f"NGS passing rows with week missing or week 0 (season aggregate) dropped and not copied onto weeks: {dropped_weeks}."
    )
    ngs = ngs[ngs["week"].notna() & (ngs["week"] > 0) & ngs["season"].between(2016, 2026)].copy()
    ngs["week"] = ngs["week"].astype(int)
    ngs["gsis"] = ngs["player_gsis_id"].astype(str)
    ngs["att"] = pd.to_numeric(ngs["avg_time_to_throw"], errors="coerce")
    ngs["attempts"] = pd.to_numeric(ngs["attempts"], errors="coerce").fillna(0.0)
    ngs = ngs[ngs["att"].notna() & ngs["gsis"].notna()]
    # One row per passer-week. Attempts weight the weekly average.
    ngs = ngs.groupby(["gsis", "season", "week"], as_index=False).agg(
        att=("att", "mean"), attempts=("attempts", "sum"),
    )
    history: dict[str, list[tuple[int, int, float, float]]] = {}
    for row in ngs.sort_values(["gsis", "season", "week"]).itertuples(index=False):
        history.setdefault(row.gsis, []).append((int(row.season), int(row.week), float(row.att), float(row.attempts)))
    dropbacks = plays[plays["qb_dropback"].fillna(0).astype(float).eq(1.0) & plays["passer_player_id"].notna()]
    passer = (
        dropbacks.groupby(["game_id", "posteam", "passer_player_id"]).size()
        .rename("n").reset_index()
        .sort_values("n", ascending=False)
        .drop_duplicates(["game_id", "posteam"])
    )
    kickoff_by_game = dict(zip(games["game_id"].astype(str), games["kickoff"]))
    game_season = dict(zip(games["game_id"].astype(str), games["season"].astype(int)))
    game_week = dict(zip(games["game_id"].astype(str), games["week"].astype(int)))
    # Kickoff of each (passer, season, week) from the game where he was the primary passer.
    passer_week_kick: dict[tuple[str, int, int], str] = {}
    for row in passer.itertuples(index=False):
        game_id = str(row.game_id)
        wall = kickoff_by_game.get(game_id)
        if wall is None or game_id not in game_season:
            continue
        passer_week_kick[(str(row.passer_player_id), game_season[game_id], game_week[game_id])] = wall
    rows = []
    for row in passer.itertuples(index=False):
        game_id = str(row.game_id)
        if game_id not in game_season:
            continue
        season = game_season[game_id]
        week = game_week[game_id]
        gsis = str(row.passer_player_id)
        prior = [
            item for item in history.get(gsis, [])
            if item[0] < season or (item[0] == season and item[1] < week)
        ]
        timed = []
        weighted = 0.0
        weight = 0.0
        for item_season, item_week, att, attempts in prior:
            stamp = passer_week_kick.get((gsis, item_season, item_week))
            if stamp is None or not (attempts > 0):
                continue
            timed.append(stamp)
            weighted += att * attempts
            weight += attempts
        if weight <= 0 or not timed:
            rows.append({"game_id": game_id, "team": row.posteam, "qb_att": np.nan, "qb_att_time": pd.NaT})
        else:
            rows.append({
                "game_id": game_id,
                "team": row.posteam,
                "qb_att": weighted / weight,
                "qb_att_time": max(timed, key=lambda value: eastern_kickoff(value)),
            })
    return pd.DataFrame(rows)


def load_pressure(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Team-game pressure from weekly PFR. Season files are not opened."""
    game_ids = set(games["game_id"].astype(str))
    frames = []
    weekly_rows = 0
    for year in range(2018, 2027):
        path = fetch_first([
            f"{RELEASE}/pfr_advstats/advstats_week_pass_{year}.csv.gz",
            f"{RELEASE}/pfr_advstats/advstats_week_pass_{year}.csv",
            f"{RELEASE}/pfr_advstats/advstats_week_pass_{year}.parquet",
        ])
        if path is None:
            NOTES.append(f"No weekly PFR passing file for {year}.")
            continue
        pfr = read_frame(path, ["game_id", "season", "week", "team", "opponent", "times_pressured"])
        if "times_pressured" not in pfr.columns or "week" not in pfr.columns or "game_id" not in pfr.columns:
            raise DesignError("PFR pressure is season-grain; halting")
        pfr = pfr[pfr["game_id"].astype(str).isin(game_ids)].copy()
        bad_week = pfr["week"].isna() | (pd.to_numeric(pfr["week"], errors="coerce") <= 0)
        if int(bad_week.sum()):
            NOTES.append(
                f"PFR {year} dropped {int(bad_week.sum())} rows with a missing or nonpositive week. "
                "Those rows were not copied onto team-games."
            )
        pfr = pfr.loc[~bad_week].copy()
        weekly_rows += len(pfr)
        frames.append(pfr)
        log(f"pfr {year} eligible player-games {len(pfr)}")
    if weekly_rows == 0 or not frames:
        raise DesignError("PFR pressure is season-grain; halting")
    pfr = pd.concat(frames, ignore_index=True)
    pfr["team"] = pfr["team"].map(canon)
    pfr["season"] = pfr["season"].astype(int)
    pfr["week"] = pfr["week"].astype(int)
    pfr["times_pressured"] = pd.to_numeric(pfr["times_pressured"], errors="coerce")
    grouped = pfr.groupby(["season", "week", "game_id", "team"], as_index=False).agg(
        off_pressures=("times_pressured", "sum"),
        charted=("times_pressured", "count"),
        missing_pressure=("times_pressured", lambda series: int(series.isna().sum())),
    )
    grouped = grouped[grouped["missing_pressure"] < grouped["charted"]].copy()
    dropbacks = filter_dropbacks(plays)
    counts = dropbacks.groupby(["game_id", "posteam"], as_index=False).agg(
        off_dropbacks=("posteam", "size"),
        opponent=("defteam", "first"),
    )
    counts = counts.rename(columns={"posteam": "team"})
    team_games = grouped.merge(counts, on=["game_id", "team"], how="inner")
    team_games = team_games[team_games["off_dropbacks"] > 0].copy()
    team_games = team_games[team_games["opponent"].notna()].copy()
    kickoffs = games[["game_id", "kickoff"]]
    team_games = team_games.merge(kickoffs, on="game_id", how="left")
    att = prior_time_to_throw(plays, games)
    team_games = team_games.merge(att, on=["game_id", "team"], how="left")
    team_games = team_games[
        ["season", "week", "game_id", "team", "opponent", "off_pressures", "off_dropbacks", "qb_att", "kickoff", "qb_att_time"]
    ]
    finite_att = int(np.isfinite(pd.to_numeric(team_games["qb_att"], errors="coerce")).sum())
    NOTES.append(
        f"PFR weekly team-games joined to filtered dropbacks: {len(team_games)}. "
        f"Finite pre-game average time to throw on those rows: {finite_att}. "
        "Season PFR files were not opened. Sacks and hits were not substituted."
    )
    assert_pressure_not_season_grain(team_games)
    feature = pressure_matchup_feature(
        team_games,
        games[["game_id", "season", "week", "home", "away", "kickoff"]],
    )
    return feature


def load_pace(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    return neutral_pace_feature(
        plays,
        games[["game_id", "season", "week", "home", "away"]],
    )


def load_ol(games: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    players = load_players()
    snaps = load_snaps_summed(set(games["game_id"].astype(str)), players)
    injuries = load_injuries(games)
    feature = ol_matchup_feature(
        snaps,
        injuries,
        games[[
            "game_id", "season", "week", "kickoff", "home", "away",
            "home_qb", "away_qb", "home_qb_asof", "away_qb_asof",
        ]],
    )
    reasons = feature["ol_exclude_reason"].astype(str).value_counts(dropna=False).to_dict() if "ol_exclude_reason" in feature.columns else {}
    NOTES.append(
        "OL exclude reasons: " + ", ".join(f"{key}={int(value)}" for key, value in sorted(reasons.items()))
    )
    return feature[["game_id", "ol_d"]], {str(key): int(value) for key, value in reasons.items()}


def screen_payload(result: dict[str, object]) -> dict[str, object]:
    graded = result["graded"]
    assert isinstance(graded, pd.DataFrame)
    clusters = int(graded.groupby(["season", "week"]).ngroups)
    coefficients = result["coefficients"]
    assert isinstance(coefficients, list)
    expected = int(FEATURES[str(result["feature"])]["expected_sign"])
    sign_rows = []
    for row in coefficients:
        coefficient = float(row["coefficient"])
        sign_rows.append({
            "season": int(row["season"]),
            "coefficient": coefficient,
            "matches_prereg_sign": bool(sign_matches(coefficient, expected)),
        })
    signs_ok = bool(sign_rows) and all(row["matches_prereg_sign"] for row in sign_rows)
    decision = "continue" if bool(result["retain"]) else "stop"
    return {
        "feature": result["feature"],
        "endpoint": result["endpoint"],
        "n_games": int(result["n"]),
        "n_clusters": clusters,
        "sd_d": float(result["structured_shift_sd"]),
        "mean_d": float(result["mean_delta"]),
        "upper_90": float(result["upper_90"]),
        "expected_sign": expected,
        "sign_check": signs_ok,
        "decision": decision,
        "coefficient_by_season": sign_rows,
        "seasons_graded": result["seasons_graded"],
        "seasons_skipped": result["seasons_skipped"],
        "mde_80": float(result["mde_80"]),
        "halt": None,
    }


def halt_payload(feature: str, message: str) -> dict[str, object]:
    return {
        "feature": feature,
        "endpoint": FEATURES[feature]["endpoint"],
        "n_games": None,
        "n_clusters": None,
        "sd_d": None,
        "mean_d": None,
        "upper_90": None,
        "expected_sign": int(FEATURES[feature]["expected_sign"]),
        "sign_check": None,
        "decision": "halt",
        "coefficient_by_season": [],
        "seasons_graded": [],
        "seasons_skipped": [],
        "mde_80": None,
        "halt": message,
    }


def json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not np.isfinite(number):
            return None
        return number
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def main() -> None:
    head = assert_locked_files()
    log(f"HEAD {head}")
    log(f"evaluation modules match {EVALUATION_SHA}")
    games = load_schedule()
    plays = load_pbp(set(games["game_id"].astype(str)))
    lookup = quarterback_lookup(games)
    games = attach_quarterbacks(games, lookup)
    log("building base features")
    base = base_features(plays, games, lookup)
    design = games.merge(base, on="game_id", how="left")
    log("building neutral pace")
    pace = load_pace(plays, design)
    design = design.merge(pace[["game_id", "neutral_pace_sum"]], on="game_id", how="left")
    finite_pace = int(np.isfinite(design["neutral_pace_sum"].to_numpy(dtype=float)).sum())
    NOTES.append(f"Games with finite neutral_pace_sum: {finite_pace} of {len(design)}.")
    log("building OL availability")
    ol, ol_reasons = load_ol(design)
    design = design.merge(ol, on="game_id", how="left")
    finite_ol = int(np.isfinite(pd.to_numeric(design["ol_d"], errors="coerce")).sum())
    NOTES.append(f"Games with finite ol_d: {finite_ol} of {len(design)}.")
    log("building pressure")
    pressure_halt = None
    try:
        pressure = load_pressure(plays, design)
    except DesignError as exc:
        message = str(exc)
        if "season-grain" not in message:
            raise
        pressure_halt = message
        NOTES.append(f"HALT pressure_x: {message} No sack or hit substitute was built.")
        pressure = pd.DataFrame({"game_id": design["game_id"], "pressure_x": np.nan})
    design = design.merge(pressure[["game_id", "pressure_x"]], on="game_id", how="left")
    if pressure_halt is None:
        finite_pressure = int(np.isfinite(pd.to_numeric(design["pressure_x"], errors="coerce")).sum())
        NOTES.append(f"Games with finite pressure_x: {finite_pressure} of {len(design)}.")
    if (~design["kickoff"].map(before_screen_cutoff)).any() or design["season"].astype(int).ge(2027).any():
        raise SystemExit("design frame contains a row on or after the Stage A cutoff")
    keep = [
        "game_id", "season", "week", "kickoff", "game_type",
        "epa_d", "pts_d", "st_d", "qb_d", "hfa",
        "pts_s", "epa_s", "env2", "plays_s", "qb_s",
        "margin", "total", "pressure_x", "ol_d", "neutral_pace_sum",
    ]
    frame = design[keep].copy()
    stage_clusters = int(frame.groupby(["season", "week"]).ngroups)
    features_out = []
    for feature in ("pressure_x", "ol_d", "neutral_pace_sum"):
        if feature == "pressure_x" and pressure_halt is not None:
            features_out.append(halt_payload(feature, pressure_halt))
            log(f"{feature} HALT")
            continue
        log(f"exploratory screen {feature}")
        try:
            result = exploratory_screen(frame, feature)
        except DesignError as exc:
            features_out.append(halt_payload(feature, str(exc)))
            NOTES.append(f"{feature} screen raised: {exc}")
            log(f"{feature} raised {exc}")
            continue
        payload = screen_payload(result)
        features_out.append(payload)
        log(
            f"{feature} n={payload['n_games']} clusters={payload['n_clusters']} "
            f"mean_d={payload['mean_d']} upper_90={payload['upper_90']} decision={payload['decision']}"
        )
    document = {
        "stage": "A",
        "role": "exploratory_screen",
        "code_sha": head,
        "evaluation_code_sha": EVALUATION_SHA,
        "evaluation_modules_unchanged_from_evaluation_code_sha": True,
        "locked_prereg_sha": "d6482dd",
        "cutoff": "2027-01-01 America/New_York",
        "seasons_loaded": "2015 through December 2026 kickoffs. 2027-season rows were not loaded.",
        "inference": {
            "method": "webb_wild_cluster_bootstrap",
            "draws": BOOTSTRAP_DRAWS,
            "seed": BOOTSTRAP_SEED,
            "clusters": "season-week",
            "upper_quantile": SCREEN_UB_QUANTILE,
            "continuation_rule": "mean_d < 0 and prereg sign matches on every season coefficient and upper_90 < 0",
        },
        "n_games": len(frame),
        "n_clusters": stage_clusters,
        "features": features_out,
        "ol_exclude_reasons": ol_reasons,
        "data_coverage_notes": NOTES,
        "market_inputs": "none",
        "paid_apis": "none",
        "harm_check_called": False,
        "confirmatory_look_called": False,
    }
    RESULTS.write_text(json.dumps(json_ready(document), indent=2) + "\n")
    digest = hashlib.sha256(RESULTS.read_bytes()).hexdigest()
    MANIFEST.write_text(f"{digest}  {RESULTS.name}\n")
    manifest_digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    log(f"wrote {RESULTS}")
    log(f"sha256 {RESULTS.name} {digest}")
    log(f"sha256 {MANIFEST.name} {manifest_digest}")


if __name__ == "__main__":
    main()
