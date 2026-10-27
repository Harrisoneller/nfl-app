"""2016–23 selection for the rest/travel features. Does not score 2024 or 2025.

The holdout stays unspent. This script counts how many 2024–25 regular-season
games have a score and a closing line, and that count is only the power
sample size from PREREG.md. It never fits or predicts those seasons.

    NFLVERSE_DIR=/path/to/nflverse python research/nfl_rest_travel/select.py
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
VENUES = HERE / "venues.csv"
PREREG = HERE / "PREREG.md"
OUT_JSON = HERE / "selection.json"
OUT_MD = HERE / "SELECTION.md"
VENUE_SHA = "d6f426dd638aeeef90600b1fe8a83cf0ab77cb776859863e6f5f8c5030c1fcf3"
SIGMA = 13.5
EARTH_MILES = 3958.7613
Z_CRIT = 1.959963984540054
Z_80 = 0.8416212335729143
V5_BLIND_ALLOW = ("epa_d", "pts_d", "st_d", "qb_d", "hfa")
AUG_ALLOW = V5_BLIND_ALLOW + (
    "rest_d", "bye_d", "thursday", "intl", "travel_d", "tz_east_d", "tz_cross_d", "body_d",
)
V5_FULL = ["mkt_d", "epa_d", "pts_d", "st_d", "qb_d_actual", "hfa"]
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
    allowed = set(allow)
    extra = [str(c) for c in columns if str(c) not in allowed]
    if extra or len(list(columns)) != len(set(columns)):
        raise ValueError(
            f"design columns {list(columns)} are not a subset of the allowlist {list(allow)}"
        )


def _self_check() -> None:
    try:
        require_allowlist(["epa_d", "mkt_d", "spread_line"], AUG_ALLOW)
    except ValueError:
        pass
    else:
        raise RuntimeError("the allowlist did not reject a market column")
    require_allowlist(list(V5_BLIND_ALLOW), V5_BLIND_ALLOW)
    require_allowlist(list(AUG_ALLOW), AUG_ALLOW)
    digest = hashlib.sha256(VENUES.read_bytes()).hexdigest()
    if digest != VENUE_SHA:
        raise RuntimeError(f"venue table hash {digest} does not match the preregistration")
    if haversine(40.0, -74.0, 40.0, -74.0) != 0.0:
        raise RuntimeError("haversine of a point with itself is not zero")


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2
    return 2.0 * EARTH_MILES * math.asin(min(1.0, math.sqrt(a)))


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


def load_venues() -> pd.DataFrame:
    venues = pd.read_csv(VENUES)
    if list(venues.columns) != ["stadium_id", "stadium_name", "lat", "lon", "tz", "country", "label"]:
        raise RuntimeError(f"unexpected venue columns {list(venues.columns)}")
    return venues


def resolve_venue(venues: pd.DataFrame, stadium_id: str, stadium_name: str) -> pd.Series:
    named = venues[(venues["stadium_id"] == stadium_id) & (venues["stadium_name"] == stadium_name)]
    if len(named) == 1:
        return named.iloc[0]
    default = venues[(venues["stadium_id"] == stadium_id) & (venues["stadium_name"] == "*")]
    if len(default) != 1:
        raise RuntimeError(f"no venue row for {stadium_id} / {stadium_name}")
    return default.iloc[0]


def attach_venues(sched: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cache: dict[tuple[str, str], pd.Series] = {}
    for sid, name in sched[["stadium_id", "stadium"]].drop_duplicates().itertuples(index=False):
        cache[(sid, name)] = resolve_venue(venues, sid, name if isinstance(name, str) else "")
    out = sched.copy()
    lats, lons, tzs, countries = [], [], [], []
    for sid, name in zip(out["stadium_id"], out["stadium"]):
        row = cache[(sid, name)]
        lats.append(float(row["lat"]))
        lons.append(float(row["lon"]))
        tzs.append(str(row["tz"]))
        countries.append(str(row["country"]))
    out["lat"] = lats
    out["lon"] = lons
    out["tz"] = tzs
    out["country"] = countries
    return out


def kickoff_utc(gameday, gametime) -> pd.Timestamp:
    day = str(gameday)[:10]
    clock = gametime if isinstance(gametime, str) and ":" in gametime else "13:00"
    clock = clock.strip()[:5]
    return pd.Timestamp(f"{day} {clock}", tz="America/New_York").tz_convert("UTC")


def offset_hours(tz_name: str, utc: pd.Timestamp) -> float:
    return utc.tz_convert(tz_name).utcoffset().total_seconds() / 3600.0


def body_hour(tz_name: str, utc: pd.Timestamp) -> float:
    local = utc.tz_convert(tz_name)
    return float(local.hour) + float(local.minute) / 60.0


def _mode_key(keys: list[str]) -> str:
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    for i, key in enumerate(keys):
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, i)
    return max(counts, key=lambda key: (counts[key], -first[key]))


def franchise_homes(sched: pd.DataFrame) -> dict[tuple[int, str], dict]:
    """Modal US home venue on the published schedule. Scores are not used."""
    home = sched[(sched["location"] == "Home") & (sched["country"] == "US")].copy()
    home = home.sort_values(["season", "gameday", "gametime", "game_id"])
    by_season: dict[tuple[int, str], dict] = {}
    for (season, team), g in home.groupby(["season", "home_team"], sort=False):
        key = _mode_key(g["venue_key"].tolist())
        row = g[g["venue_key"] == key].iloc[0]
        by_season[(int(season), team)] = {
            "lat": float(row["lat"]), "lon": float(row["lon"]), "tz": str(row["tz"]),
        }
    seasons = sorted({s for s, _ in by_season})
    teams = sorted({t for _, t in by_season})
    resolved: dict[tuple[int, str], dict] = {}
    for season in seasons:
        for team in teams:
            if (season, team) in by_season:
                resolved[(season, team)] = by_season[(season, team)]
                continue
            prior = [s for s in seasons if s < season and (s, team) in by_season]
            if prior:
                resolved[(season, team)] = by_season[(prior[-1], team)]
    return resolved


def team_trip_table(sched: pd.DataFrame, homes: dict[tuple[int, str], dict]) -> pd.DataFrame:
    """One row per team in each played game. Unscored games are not previous games."""
    played = sched[np.isfinite(sched["home_score"]) & np.isfinite(sched["away_score"])].copy()
    played["kick"] = [kickoff_utc(d, t) for d, t in zip(played["gameday"], played["gametime"])]
    long_rows = []
    for rec in played.itertuples(index=False):
        for team, is_home in ((rec.home_team, 1), (rec.away_team, 0)):
            long_rows.append({
                "game_id": rec.game_id,
                "season": int(rec.season),
                "week": int(rec.week),
                "team": team,
                "is_home": is_home,
                "kick": rec.kick,
                "gameday": rec.gameday,
                "lat": float(rec.lat),
                "lon": float(rec.lon),
                "tz": rec.tz,
                "country": rec.country,
                "weekday": rec.weekday,
            })
    long = pd.DataFrame(long_rows).sort_values(["team", "kick", "game_id"]).reset_index(drop=True)
    prev_season = long.groupby("team")["season"].shift(1)
    prev_week = long.groupby("team")["week"].shift(1)
    prev_day = long.groupby("team")["gameday"].shift(1)
    prev_lat = long.groupby("team")["lat"].shift(1)
    prev_lon = long.groupby("team")["lon"].shift(1)
    prev_tz = long.groupby("team")["tz"].shift(1)
    out_rows = []
    for i, rec in enumerate(long.itertuples(index=False)):
        home = homes.get((int(rec.season), rec.team))
        if home is None:
            raise RuntimeError(f"no franchise home for {rec.team} {rec.season}")
        same = pd.notna(prev_season.iloc[i]) and int(prev_season.iloc[i]) == int(rec.season)
        if same:
            days = (pd.Timestamp(rec.gameday) - pd.Timestamp(prev_day.iloc[i])).days
            bye = int(int(rec.week) - int(prev_week.iloc[i]) >= 2)
        else:
            days = 7
            bye = 0
        if same and days <= 8:
            olat, olon, otz = float(prev_lat.iloc[i]), float(prev_lon.iloc[i]), str(prev_tz.iloc[i])
        else:
            olat, olon, otz = home["lat"], home["lon"], home["tz"]
        miles = haversine(olat, olon, float(rec.lat), float(rec.lon))
        signed = offset_hours(str(rec.tz), rec.kick) - offset_hours(otz, rec.kick)
        out_rows.append({
            "game_id": rec.game_id,
            "team": rec.team,
            "is_home": rec.is_home,
            "days_rest": int(days),
            "bye": bye,
            "miles": miles,
            "tz_east": signed,
            "tz_cross": abs(signed),
            "body": body_hour(home["tz"], rec.kick),
        })
    return pd.DataFrame(out_rows)


def game_context(sched: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    home = trips[trips["is_home"] == 1].set_index("game_id")
    away = trips[trips["is_home"] == 0].set_index("game_id")
    both = home.index.intersection(away.index)
    base = sched.drop_duplicates("game_id").set_index("game_id").loc[both]
    return pd.DataFrame({
        "game_id": both,
        "rest_d": home.loc[both, "days_rest"].to_numpy() - away.loc[both, "days_rest"].to_numpy(),
        "bye_d": home.loc[both, "bye"].to_numpy() - away.loc[both, "bye"].to_numpy(),
        "thursday": (base["weekday"] == "Thursday").astype(float).to_numpy(),
        "intl": (base["country"] != "US").astype(float).to_numpy(),
        "travel_d": home.loc[both, "miles"].to_numpy() - away.loc[both, "miles"].to_numpy(),
        "tz_east_d": home.loc[both, "tz_east"].to_numpy() - away.loc[both, "tz_east"].to_numpy(),
        "tz_cross_d": home.loc[both, "tz_cross"].to_numpy() - away.loc[both, "tz_cross"].to_numpy(),
        "body_d": home.loc[both, "body"].to_numpy() - away.loc[both, "body"].to_numpy(),
    })


def _check_cancelled_rule() -> None:
    """A null-score game between two played games is not the previous game."""
    venues = pd.DataFrame([
        {"stadium_id": "H", "stadium_name": "*", "lat": 40.0, "lon": -74.0, "tz": "America/New_York", "country": "US", "label": "H"},
        {"stadium_id": "A", "stadium_name": "*", "lat": 41.0, "lon": -87.0, "tz": "America/Chicago", "country": "US", "label": "A"},
        {"stadium_id": "C", "stadium_name": "*", "lat": 34.0, "lon": -118.0, "tz": "America/Los_Angeles", "country": "US", "label": "C"},
    ])
    raw = pd.DataFrame([
        {"game_id": "g0", "season": 2020, "week": 1, "game_type": "REG", "gameday": "2020-09-06", "gametime": "13:00",
         "weekday": "Sunday", "home_team": "AWY", "away_team": "HOM", "location": "Home",
         "home_score": 1.0, "away_score": 0.0, "stadium_id": "H", "stadium": "Home"},
        {"game_id": "g1", "season": 2020, "week": 1, "game_type": "REG", "gameday": "2020-09-13", "gametime": "13:00",
         "weekday": "Sunday", "home_team": "HOM", "away_team": "AWY", "location": "Home",
         "home_score": 1.0, "away_score": 0.0, "stadium_id": "H", "stadium": "Home"},
        {"game_id": "g2", "season": 2020, "week": 2, "game_type": "REG", "gameday": "2020-09-20", "gametime": "13:00",
         "weekday": "Sunday", "home_team": "HOM", "away_team": "OTH", "location": "Home",
         "home_score": np.nan, "away_score": np.nan, "stadium_id": "A", "stadium": "Away"},
        {"game_id": "g3", "season": 2020, "week": 3, "game_type": "REG", "gameday": "2020-09-27", "gametime": "13:00",
         "weekday": "Sunday", "home_team": "HOM", "away_team": "AWY", "location": "Home",
         "home_score": 1.0, "away_score": 0.0, "stadium_id": "C", "stadium": "Coast"},
    ])
    # The toy venues do not match these stadium ids. Use a local table.
    toy_venues = pd.DataFrame([
        {"stadium_id": "H", "stadium_name": "*", "lat": 40.0, "lon": -74.0, "tz": "America/New_York", "country": "US", "label": "H"},
        {"stadium_id": "A", "stadium_name": "*", "lat": 41.0, "lon": -87.0, "tz": "America/Chicago", "country": "US", "label": "A"},
        {"stadium_id": "C", "stadium_name": "*", "lat": 34.0, "lon": -118.0, "tz": "America/Los_Angeles", "country": "US", "label": "C"},
    ])
    del venues
    sched = attach_venues(raw, toy_venues)
    sched["venue_key"] = sched["stadium_id"]
    homes = franchise_homes(sched)
    trips = team_trip_table(sched, homes)
    row = trips[(trips["game_id"] == "g3") & (trips["team"] == "HOM")].iloc[0]
    if int(row["bye"]) != 1:
        raise RuntimeError("cancelled game was used as the previous game")
    if int(row["days_rest"]) != 14:
        raise RuntimeError(f"rest should span the cancelled game, got {row['days_rest']}")


def load_main_v5():
    src = git_show("main:backend/app/services/team_ratings_v5.py")
    path = Path("/tmp/team_ratings_v5_rest.py")
    path.write_bytes(src)
    name = "team_ratings_v5_rest"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _materialize_v6():
    dest = Path("/tmp/v6rest")
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
    name = "v6_backtest_rest"
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
        if not path.exists():
            raise RuntimeError(f"missing {path}")
        pbp = pd.read_parquet(path, columns=PBP_COLS)
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
        f"Feature audit on the selection games: Thursday share {audit['thursday_share']:.4f}, "
        f"international share {audit['intl_share']:.4f}, "
        f"mean absolute travel differential {audit['mean_abs_travel_d']:.1f} miles, "
        f"bye differential nonzero on {audit['bye_nonzero']} games.",
        "",
        f"Preregistration file SHA-256 `{payload['prereg_sha256']}`.",
        f"Venue table SHA-256 `{payload['venue_sha256']}`.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines))


def main() -> None:
    _self_check()
    _check_cancelled_rule()
    if not (DATA / "games.csv").exists():
        raise SystemExit(f"nflverse mirror not found at {DATA}")
    n_h = holdout_count()
    print(f"holdout sample size n_h={n_h}", flush=True)
    print("loading schedule through 2023", flush=True)
    mod = _materialize_v6()
    sched = mod.load_schedule()
    sched = sched[sched["season"] <= 2023].copy()
    if int(sched["season"].max()) >= 2024:
        raise RuntimeError("schedule filter failed")
    venues = load_venues()
    sched = attach_venues(sched, venues)
    sched["venue_key"] = sched["stadium_id"] + "|" + sched["stadium"].astype(str)
    # 2024 stadium ids are checked without using scores.
    ids_2024 = set()
    raw_ids = pd.read_csv(DATA / "games.csv", usecols=["season", "stadium_id", "stadium"])
    raw_ids = raw_ids[raw_ids["season"].isin([2024, 2025])]
    for sid, name in raw_ids[["stadium_id", "stadium"]].drop_duplicates().itertuples(index=False):
        resolve_venue(venues, sid, name if isinstance(name, str) else "")
        ids_2024.add(sid)
    print(f"2024-25 stadium ids resolve: {len(ids_2024)}", flush=True)

    homes = franchise_homes(sched)
    print("building rest and travel", flush=True)
    trips = team_trip_table(sched, homes)
    context = game_context(sched, trips)
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
        "thursday_share": float(sample["thursday"].mean()),
        "intl_share": float(sample["intl"].mean()),
        "mean_abs_travel_d": float(sample["travel_d"].abs().mean()),
        "bye_nonzero": int((sample["bye_d"] != 0).sum()),
        "rest_d_sd": float(sample["rest_d"].std(ddof=1)),
        "body_d_sd": float(sample["body_d"].std(ddof=1)),
    }
    print(f"feature audit {audit}", flush=True)
    if audit["thursday_share"] <= 0 or audit["intl_share"] <= 0 or audit["bye_nonzero"] == 0:
        raise RuntimeError("rest/travel features did not vary on the selection games")
    if audit["mean_abs_travel_d"] < 10 or audit["body_d_sd"] == 0:
        raise RuntimeError("travel or body-clock features look empty")

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
        "venue_sha256": VENUE_SHA,
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
        "go_rule": "paired z >= 2 and projected holdout power >= 0.50",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    write_markdown(payload)
    print(json.dumps({"verdict": verdict, "selection": payload["selection"], "levels": payload["levels"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
