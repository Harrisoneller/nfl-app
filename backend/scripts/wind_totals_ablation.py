"""Wind-on-totals ablation on top of the frozen v6-blind core.

The feature is the archived wind forecast issued at or before kickoff minus
24 hours. Observed weather, the ``temp`` and ``wind`` columns, and any
``weather_*`` column are not read. Precipitation and temperature are not
features. The total is the only score that moves.

Modes, in order:

* ``fetch`` writes the market-free forecast cache.
* ``select`` fits the form, threshold, and slope on 2016–23 and records how
  many 2024–25 games the chosen feature would touch. It does not grade them.
* ``evaluate`` grades the pre-registered bar. Run it only after PREREG.md is
  committed. It does not refit.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.data.weather import STADIUMS
from app.services import v6_blind as v6

DATA = Path(os.environ.get("NFLVERSE_DIR", "/tmp/nflverse"))
OUT_DIR = ROOT / "research" / "wind_totals"
FORECASTS_PATH = OUT_DIR / "forecasts.csv"
README_PATH = OUT_DIR / "README.md"
FROZEN_PATH = OUT_DIR / "frozen.json"
VERDICT_PATH = OUT_DIR / "verdict.json"
RESULTS_PATH = ROOT / "WIND_RESULTS.md"
PREDICTIONS_PATH = ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv"
HOURLY_DIR = Path("/tmp/wind_jma_prev1")

API_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
MODEL_NAME = "jma_gsm"
VARIABLE = "wind_speed_10m_previous_day1"
WIND_CAP_MPH = 30.0
THRESHOLDS = (10.0, 12.0, 15.0)
FORMS = ("hinge", "step")
PREFERRED_THRESHOLD = 12.0
TIE_EPS = 1e-6
FIT_SEASONS = range(2016, 2024)
HOLDOUT_SEASONS = (2024, 2025)
CONFIRM_SEASON = 2026
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20241024
ATS_BAR = 0.525
MIN_PLAYS = 400
P_BAR = 0.01
TOTAL_SIGMA = v6.TOTAL_SIGMA
GAME_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR",
    "OAK": "LV", "SD": "LAC", "STL": "LAR", "HST": "HOU", "BLT": "BAL", "CLV": "CLE",
}

# Allowlist. ``temp``, ``wind``, and ``weather_*`` are observed and stay out.
FETCH_COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "gametime",
    "away_team", "home_team", "location",
]
GRADE_COLUMNS = FETCH_COLUMNS + [
    "home_score", "away_score", "spread_line", "total_line", "roof",
]
CACHE_COLUMNS = [
    "game_id", "season", "week", "home_team", "away_team", "gameday",
    "lat", "lon", "kickoff_utc", "issue_time_utc", "valid_time_utc",
    "wind_mph", "model", "variable",
]


def canonical_team(team: str | None) -> str | None:
    if not isinstance(team, str) or not team.strip():
        return None
    token = team.strip().upper()
    return ALIASES.get(token, token)


def _roof_token(roof) -> str:
    if roof is None or (isinstance(roof, float) and math.isnan(roof)):
        return ""
    text = str(roof).strip().lower()
    if text in {"nan", "none", ""}:
        return ""
    return text


def is_neutral(location) -> bool:
    return isinstance(location, str) and location.strip().lower() == "neutral"


def uses_forecast(roof, location, is_indoor: bool) -> bool:
    """True when the kickoff forecast is allowed to move the total."""
    if is_neutral(location):
        return False
    token = _roof_token(roof)
    if token in {"dome", "closed"}:
        return False
    if token in {"outdoors", "open"}:
        return True
    return not bool(is_indoor)


def _finite_wind(wind_mph) -> float:
    try:
        value = float(wind_mph)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or value < 0:
        return 0.0
    return value


def applied_wind(roof, location, wind_mph, is_indoor: bool) -> float:
    """Mph that enters the feature. Domes, closed roofs, and neutral sites are 0.

    A missing forecast is 0, same as a game with no weather adjustment. An open
    retractable roof uses the forecast even when ``weather.py`` marks the
    current building indoor. A null roof falls back to that indoor flag.
    """
    if not uses_forecast(roof, location, is_indoor):
        return 0.0
    return _finite_wind(wind_mph)


def wind_feature(wind_applied: float, form: str, threshold: float, cap: float = WIND_CAP_MPH) -> float:
    """Hinge is mph above the threshold. Step is 1 at or above it. Both cap at 30."""
    capped = min(max(float(wind_applied), 0.0), float(cap))
    if form == "hinge":
        return max(0.0, capped - float(threshold))
    if form == "step":
        return 1.0 if capped >= float(threshold) else 0.0
    raise ValueError(f"unknown form {form}")


def predict_total(v6_total, feature, w: float) -> np.ndarray:
    """Additive wind adjustment. A zero feature leaves the v6 total unchanged."""
    return np.asarray(v6_total, dtype=float) + float(w) * np.asarray(feature, dtype=float)


def ols_through_origin(y: np.ndarray, x: np.ndarray) -> float:
    """One slope, no intercept. Rows with x = 0 do not move the slope."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    den = float(np.dot(x, x))
    if den < 1e-8:
        return 0.0
    return float(np.dot(x, y) / den)


def kickoff_utc(gameday, gametime) -> pd.Timestamp:
    """nflverse ``gametime`` is Eastern. A missing clock defaults to 13:00 ET."""
    clock = gametime if isinstance(gametime, str) and len(gametime.strip()) >= 4 else "13:00"
    clock = clock.strip()
    local = pd.Timestamp(f"{gameday} {clock}")
    local = local.tz_localize("America/New_York", nonexistent="shift_forward", ambiguous="NaT")
    if pd.isna(local):
        raise ValueError(f"could not place kickoff {gameday} {clock} in America/New_York")
    return local.tz_convert("UTC")


def choose_forecast(series: pd.Series | None, kickoff: pd.Timestamp) -> dict:
    """Previous-day forecast for the kickoff hour, issued at or before T−24h.

    ``series`` is indexed by the valid hour in UTC. The value at that hour is
    the forecast issued 24 hours earlier. If that hour is missing, use the
    latest filled hour at or before kickoff. Never use a later hour.
    """
    empty = {"wind_mph": None, "valid_time_utc": None, "issue_time_utc": None}
    if kickoff.tzinfo is None:
        raise ValueError("kickoff must be timezone-aware UTC")
    kickoff = kickoff.tz_convert("UTC")
    if series is None or len(series) == 0:
        return empty
    values = series.copy()
    index = pd.DatetimeIndex(values.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    values.index = index
    values = values[~values.index.duplicated(keep="last")].sort_index()
    valid = kickoff.floor("h")
    chosen = None
    if valid in values.index and pd.notna(values.loc[valid]):
        chosen = valid
    else:
        prior = values[(values.index <= kickoff) & values.notna()]
        if len(prior):
            chosen = prior.index[-1]
    if chosen is None:
        return empty
    issue = chosen - pd.Timedelta(hours=24)
    if issue > kickoff - pd.Timedelta(hours=24):
        raise RuntimeError("forecast issue time is after T-24h")
    return {
        "wind_mph": float(values.loc[chosen]),
        "valid_time_utc": chosen,
        "issue_time_utc": issue,
    }


def choose_candidate(candidates: list[dict]) -> dict:
    """Lowest walk-forward totals CRPS. Within 1e-6, prefer threshold 12, then hinge."""
    best = min(row["walk_forward_totals_crps"] for row in candidates)
    pool = [row for row in candidates if row["walk_forward_totals_crps"] <= best + TIE_EPS]
    pool.sort(key=lambda row: (
        0 if row["threshold"] == PREFERRED_THRESHOLD else 1,
        0 if row["form"] == "hinge" else 1,
        float(row["threshold"]),
    ))
    return pool[0]


def walk_forward_totals_crps(df: pd.DataFrame) -> tuple[float, list[dict]]:
    """Totals CRPS on 2016–23. The 2016 weight is 0. 2015 and 2024+ are rejected."""
    seasons = {int(s) for s in df["season"].unique()}
    if not seasons <= set(FIT_SEASONS):
        raise RuntimeError(f"walk-forward saw seasons outside 2016-2023: {sorted(seasons)}")
    pieces: list[np.ndarray] = []
    rows: list[dict] = []
    for season in FIT_SEASONS:
        train = df[df["season"] < season]
        test = df[df["season"] == season]
        if len(test) == 0:
            continue
        if len(train) < 30:
            w = 0.0
        else:
            residual = train["actual_total"].to_numpy(dtype=float) - train["v6_total"].to_numpy(dtype=float)
            w = ols_through_origin(residual, train["feature"].to_numpy(dtype=float))
        mu = predict_total(test["v6_total"], test["feature"], w)
        y = test["actual_total"].to_numpy(dtype=float)
        crps = v6.crps_normal(y, mu, TOTAL_SIGMA)
        base = v6.crps_normal(y, test["v6_total"].to_numpy(dtype=float), TOTAL_SIGMA)
        pieces.append(crps)
        rows.append({
            "season": int(season),
            "n": int(len(test)),
            "w": round(float(w), 6),
            "crps": float(crps.mean()),
            "v6_crps": float(base.mean()),
        })
    if not pieces:
        return float("inf"), rows
    return float(np.concatenate(pieces).mean()), rows


def grade_totals(actual, prediction, close, v6_total) -> dict:
    """Over/under versus the close. Pushes and a prediction on the number are not plays."""
    y = np.asarray(actual, dtype=float)
    pred = np.asarray(prediction, dtype=float)
    line = np.asarray(close, dtype=float)
    base = np.asarray(v6_total, dtype=float)
    ok = np.isfinite(y) & np.isfinite(pred) & np.isfinite(line) & np.isfinite(base)
    y, pred, line, base = y[ok], pred[ok], line[ok], base[ok]
    push = np.isclose(y, line)
    no_edge = np.isclose(pred, line)
    decided = ~push & ~no_edge
    bet_over = pred > line
    went_over = y > line
    win = (bet_over & went_over) | (~bet_over & ~went_over)
    wins = int(win[decided].sum()) if decided.any() else 0
    n_ats = int(decided.sum())
    ats = (wins / n_ats) if n_ats else None
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
    totals_crps = float(v6.crps_normal(y, pred, TOTAL_SIGMA).mean()) if len(y) else None
    v6_crps = float(v6.crps_normal(y, base, TOTAL_SIGMA).mean()) if len(y) else None
    close_crps = float(v6.crps_normal(y, line, TOTAL_SIGMA).mean()) if len(y) else None
    return {
        "n": int(ok.sum()),
        "n_ats": n_ats,
        "wins": wins,
        "ats": ats,
        "bootstrap_90_lo": lo,
        "bootstrap_90_hi": hi,
        "p_value": p_value,
        "totals_crps": totals_crps,
        "v6_totals_crps": v6_crps,
        "close_totals_crps": close_crps,
        "clv": None,
        "opener_n": 0,
        "n_feature_moves_total": None,
    }


def bar_verdict(summary: dict) -> dict:
    """CLV is unavailable with opener n = 0 and is not a pass or a fail."""
    ats = summary["ats"] if summary["ats"] is not None else float("nan")
    n_ok = summary["n_ats"] >= MIN_PLAYS
    ats_ok = bool(ats >= ATS_BAR) and n_ok
    boot_ok = summary["bootstrap_90_lo"] is not None and summary["bootstrap_90_lo"] > 0.50
    crps_ok = (
        summary["totals_crps"] is not None
        and summary["v6_totals_crps"] is not None
        and summary["totals_crps"] <= summary["v6_totals_crps"]
    )
    p_ok = summary["p_value"] is not None and summary["p_value"] < P_BAR
    return {
        "ats_52_5_on_400": {
            "pass": ats_ok,
            "ats": summary["ats"],
            "n_ats": summary["n_ats"],
            "wins": summary["wins"],
            "need_ats": ATS_BAR,
            "need_n": MIN_PLAYS,
        },
        "bootstrap_90_lo_above_50": {
            "pass": boot_ok,
            "lo": summary["bootstrap_90_lo"],
            "hi": summary["bootstrap_90_hi"],
        },
        "clv": {"pass": None, "status": "unavailable", "opener_n": 0},
        "no_totals_crps_regression_vs_v6": {
            "pass": crps_ok,
            "ablation": summary["totals_crps"],
            "v6": summary["v6_totals_crps"],
            "close": summary.get("close_totals_crps"),
        },
        "p_below_0_01": {"pass": p_ok, "p_value": summary["p_value"]},
        "overall_pass": bool(ats_ok and boot_ok and crps_ok and p_ok),
    }


def assert_selection_payload(payload: dict) -> None:
    """Selection may count 2024–25 windy games. It may not grade them."""
    if payload.get("holdout_scored") is not False:
        raise RuntimeError("selection payload must set holdout_scored false")
    if int(payload.get("scored_seasons_max", 0)) > 2023:
        raise RuntimeError("selection scored past 2023")
    forbidden = {
        "ats", "wins", "p_value", "bootstrap_90_lo", "bootstrap_90_hi",
        "totals_crps", "v6_totals_crps", "close_totals_crps", "overall_pass",
    }
    present = forbidden & set(payload)
    if present:
        raise RuntimeError(f"selection payload contains holdout grades: {sorted(present)}")
    for row in payload.get("candidates", []):
        leaked = forbidden & set(row)
        if leaked:
            raise RuntimeError(f"candidate row contains holdout grades: {sorted(leaked)}")


def _iso(stamp) -> str:
    if stamp is None or (isinstance(stamp, float) and math.isnan(stamp)):
        return ""
    if isinstance(stamp, str):
        return stamp
    ts = pd.Timestamp(stamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stadium(team: str | None) -> dict | None:
    cid = canonical_team(team)
    if cid is None:
        return None
    return STADIUMS.get(cid)


def _load_games(columns: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(DATA / "games.csv", usecols=columns, low_memory=False)
    banned = [c for c in frame.columns if c in {"temp", "wind"} or str(c).startswith("weather")]
    if banned:
        raise RuntimeError(f"schedule load included observed weather: {banned}")
    frame = frame[frame["game_type"].isin(GAME_TYPES)].copy()
    frame = frame.drop_duplicates("game_id")
    return frame


def _coord_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 6), round(float(lon), 6))


def _unique_coords() -> list[tuple[float, float]]:
    found: dict[tuple[float, float], None] = {}
    for stadium in STADIUMS.values():
        found[_coord_key(stadium["lat"], stadium["lon"])] = None
    return list(found)


def _season_span(season: int) -> tuple[str, str]:
    start = date(season, 8, 1)
    end = date(season + 1, 2, 28)
    today = date.today()
    if end > today:
        end = today
    if start > end:
        raise RuntimeError(f"season {season} window is empty")
    return start.isoformat(), end.isoformat()


def _hourly_path(lat: float, lon: float, season: int) -> Path:
    return HOURLY_DIR / f"{MODEL_NAME}_{lat:.4f}_{lon:.4f}_{season}.json"


def _fetch_hourly(lat: float, lon: float, season: int) -> dict:
    path = _hourly_path(lat, lon, season)
    if path.exists():
        return json.loads(path.read_text())
    import httpx

    start, end = _season_span(season)
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": VARIABLE,
        "models": MODEL_NAME,
        "wind_speed_unit": "mph",
        "timezone": "UTC",
        "start_date": start,
        "end_date": end,
    }
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = httpx.get(
                API_URL, params=params, timeout=120,
                headers={"User-Agent": "nfl-wind-totals-research"},
            )
            response.raise_for_status()
            payload = response.json()
            hourly = payload["hourly"]
            if VARIABLE not in hourly:
                raise RuntimeError(f"response missing {VARIABLE}: {list(hourly)}")
            if len(hourly["time"]) != len(hourly[VARIABLE]):
                raise RuntimeError("time and wind length differ")
            body = {"time": hourly["time"], "wind": hourly[VARIABLE]}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body))
            return body
        except Exception as exc:  # noqa: BLE001 — retry the HTTP call, then surface it
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"forecast fetch failed for {lat}, {lon}, {season}: {last_error}")


def _series_from_hourly(body: dict) -> pd.Series:
    index = pd.to_datetime(body["time"], utc=True)
    return pd.Series(body["wind"], index=index, dtype="float64")


def fetch() -> None:
    """Cache one home-stadium forecast per game. The roof mask is applied later."""
    games = _load_games(FETCH_COLUMNS)
    games = games[games["season"].between(2016, CONFIRM_SEASON)].copy()
    coords = _unique_coords()
    seasons = list(range(2016, CONFIRM_SEASON + 1))
    tasks = [(lat, lon, season) for lat, lon in coords for season in seasons]
    print(f"fetching {len(tasks)} stadium-seasons", flush=True)
    hourly: dict[tuple[float, float, int], pd.Series] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {
            pool.submit(_fetch_hourly, lat, lon, season): (lat, lon, season)
            for lat, lon, season in tasks
        }
        done = 0
        for future in as_completed(future_map):
            lat, lon, season = future_map[future]
            done += 1
            try:
                hourly[(lat, lon, season)] = _series_from_hourly(future.result())
            except Exception as exc:  # noqa: BLE001 — one stadium-season must not invent weather
                failures += 1
                print(f"FAILED {lat} {lon} {season}: {exc}", flush=True)
            if done % 30 == 0 or done == len(tasks):
                print(f"fetched {done}/{len(tasks)} failures {failures}", flush=True)
    if failures:
        raise RuntimeError(f"{failures} stadium-seasons failed; not writing a partial cache")

    rows = []
    for game in games.itertuples(index=False):
        stadium = _stadium(game.home_team)
        kick = kickoff_utc(game.gameday, game.gametime)
        lat = lon = None
        chosen = {"wind_mph": None, "valid_time_utc": None, "issue_time_utc": None}
        if stadium is not None:
            lat, lon = _coord_key(stadium["lat"], stadium["lon"])
            series = hourly.get((lat, lon, int(game.season)))
            chosen = choose_forecast(series, kick)
        rows.append({
            "game_id": game.game_id,
            "season": int(game.season),
            "week": int(game.week) if pd.notna(game.week) else "",
            "home_team": game.home_team,
            "away_team": game.away_team,
            "gameday": game.gameday,
            "lat": lat if lat is not None else "",
            "lon": lon if lon is not None else "",
            "kickoff_utc": _iso(kick),
            "issue_time_utc": _iso(chosen["issue_time_utc"]),
            "valid_time_utc": _iso(chosen["valid_time_utc"]),
            "wind_mph": chosen["wind_mph"] if chosen["wind_mph"] is not None else "",
            "model": MODEL_NAME,
            "variable": VARIABLE,
        })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=CACHE_COLUMNS)
    frame = frame.sort_values(["season", "gameday", "game_id"]).reset_index(drop=True)
    frame.to_csv(FORECASTS_PATH, index=False, lineterminator="\n")
    digest = hashlib.sha256(FORECASTS_PATH.read_bytes()).hexdigest()
    filled = pd.to_numeric(frame["wind_mph"], errors="coerce").notna().sum()
    README_PATH.write_text(_readme(len(frame), int(filled), digest))
    print(f"wrote {FORECASTS_PATH} rows {len(frame)} filled {filled} sha {digest}", flush=True)


def _readme(n_rows: int, n_filled: int, digest: str) -> str:
    return f"""# T−24h wind forecasts

Market-free cache of the wind forecast used by the totals ablation. One row
per regular-season or playoff game from 2016 through 2026. The file has no
scores, spreads, totals, moneylines, temperature, precipitation, or observed
wind.

## Source

* Endpoint: `{API_URL}`
* Model: `{MODEL_NAME}` (JMA GSM), the same model for every season
* Variable: `{VARIABLE}`
* Units: miles per hour (`wind_speed_unit=mph`)
* Time zone of the hourly stamps: UTC
* Window: 1 August of the season through 28 February of the next year, cut
  at the fetch date when that end is still in the future

`previous_day1` is the archived forecast whose valid time is the hour in the
file and whose issue time is 24 hours earlier. That issue time is at or before
kickoff minus 24 hours. This is not the Historical Weather reanalysis, and it
is not the stitched `wind_speed_10m` series (the first hours of each run,
which sit close to the analysis). GFS `previous_day1` wind is empty for
2016–2023 on this API, so it is not used. Switching to GFS only for 2024
would give the holdout a sharper model than the selection window had.

Checked against the previous-runs host for the Arrowhead hour of 2016-09-11:
the same `previous_day1` values. On 2018-09-09 the stitched analysis and
`previous_day1` differ (about 1 mph on average that day).

## Kickoff

`games.csv` `gametime` is Eastern. It is converted from `America/New_York` to
UTC. A missing clock would default to 13:00 ET; none of the 2016–2026 games
in this file were missing one. The valid hour is the UTC hour containing
kickoff. If that hour is null, the latest filled hour at or before kickoff is
used, and its issue time is still at or before T−24h. A later hour is never
used.

## What the number in `wind_mph` is

The raw forecast at the home franchise's coordinates in
`backend/app/adapters/data/weather.py`. The roof mask is **not** baked into
this file. Domes, closed roofs, and neutral sites are set to 0 when the
feature is built, by re-reading `roof` and `location` from the schedule.
Open retractable roofs keep the forecast. A null roof uses the `is_indoor`
flag only as a fallback. A null forecast becomes a zero feature and does not
move the v6 total.

Neutral games (London, Germany, Mexico, Brazil, and the rest) stay at 0.
`weather.py` is keyed by team, not by stadium name, and it has no coordinates
for those sites. Some neutral games were played in an NFL building; the home
team's coordinate is still the wrong coordinate, so those rows are not used.

## Relocation limit

Historical clubs are looked up under the current franchise id (`LA`→`LAR`,
`OAK`→`LV`, `SD`→`LAC`, `STL`→`LAR`). The coordinate is therefore the current
stadium in `weather.py`: SoFi for the Rams and Chargers, Allegiant for Las
Vegas. That is wrong for the Los Angeles Coliseum (2016–2019), the Oakland
Coliseum, and Qualcomm Stadium. The task uses the coordinates already in
`weather.py` and does not add sites that are not in that file. Shared
coordinates: NYG and NYJ (MetLife), LAR and LAC (SoFi).

## File

* Rows: {n_rows}
* Rows with a forecast value: {n_filled}
* SHA-256: `{digest}`

Rebuild with `python3 backend/scripts/wind_totals_ablation.py fetch`.
The hourly responses are cached under `/tmp/wind_jma_prev1` and are not
committed.
"""


def _read_forecasts() -> pd.DataFrame:
    frame = pd.read_csv(FORECASTS_PATH, low_memory=False)
    missing = [c for c in CACHE_COLUMNS if c not in frame.columns]
    if missing:
        raise RuntimeError(f"forecast cache missing columns: {missing}")
    banned = [c for c in frame.columns if c in {"temp", "wind", "spread_line", "total_line"} or "score" in c or str(c).startswith("weather")]
    if banned:
        raise RuntimeError(f"forecast cache is not market-free: {banned}")
    frame["wind_mph"] = pd.to_numeric(frame["wind_mph"], errors="coerce")
    return frame


def _harness():
    import importlib.util

    path = ROOT / "backtest" / "harness_model" / "model.py"
    spec = importlib.util.spec_from_file_location("v6_harness_model", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _analysis_frame(season_min: int, season_max: int) -> pd.DataFrame:
    """Schedule plus harness totals. Grading columns are present and not features."""
    games = _load_games(GRADE_COLUMNS)
    preds = pd.read_csv(PREDICTIONS_PATH)
    keep = preds["season"].between(season_min, season_max)
    ids = set(preds.loc[keep, "game_id"])
    target = games[games["game_id"].isin(ids)].copy()
    if len(target) != int(keep.sum()):
        raise RuntimeError(f"schedule is missing {int(keep.sum()) - len(target)} scored games")
    model = _harness()
    predicted = model.predict(target, target)
    if len(predicted) != len(target):
        raise RuntimeError("harness row count does not match the schedule")
    merged = target.merge(predicted, on="game_id", how="left", validate="one_to_one")
    stored = preds.loc[keep, ["game_id", "margin", "total"]].rename(
        columns={"margin": "stored_margin", "total": "stored_total"},
    )
    merged = merged.merge(stored, on="game_id", how="left", validate="one_to_one")
    gap_m = (merged["pred_margin"] - merged["stored_margin"]).abs().max()
    gap_t = (merged["pred_total"] - merged["stored_total"]).abs().max()
    if gap_m > 1e-6 or gap_t > 1e-6:
        raise RuntimeError(f"harness disagrees with stored v6 predictions: margin {gap_m} total {gap_t}")
    forecasts = _read_forecasts()
    wind_cols = forecasts[["game_id", "wind_mph", "issue_time_utc", "valid_time_utc"]]
    merged = merged.merge(wind_cols, on="game_id", how="left", validate="one_to_one")
    merged["actual_total"] = merged["home_score"] + merged["away_score"]
    indoor = []
    applied = []
    exposed = []
    for row in merged.itertuples(index=False):
        stadium = _stadium(row.home_team)
        is_indoor = bool(stadium["is_indoor"]) if stadium is not None else False
        indoor.append(is_indoor)
        exposed.append(uses_forecast(row.roof, row.location, is_indoor))
        applied.append(applied_wind(row.roof, row.location, row.wind_mph, is_indoor))
    merged["is_indoor_flag"] = indoor
    merged["uses_forecast"] = exposed
    merged["wind_applied"] = applied
    return merged


def _with_feature(frame: pd.DataFrame, form: str, threshold: float) -> pd.DataFrame:
    out = frame.copy()
    out["feature"] = [
        wind_feature(value, form, threshold) for value in out["wind_applied"].to_numpy(dtype=float)
    ]
    return out


def _fit_rows(frame: pd.DataFrame) -> pd.DataFrame:
    ok = (
        frame["season"].between(2016, 2023)
        & frame["actual_total"].notna()
        & frame["total_line"].notna()
        & frame["v6_total"].notna()
    )
    return frame.loc[ok].copy()


def _count_holdout_touch(frame: pd.DataFrame, form: str, threshold: float) -> dict:
    """How many holdout games the feature touches. Not a grade."""
    part = frame[frame["season"].between(*HOLDOUT_SEASONS)].copy()
    part = _with_feature(part, form, threshold)
    scored = part["actual_total"].notna() & part["total_line"].notna()
    windy = scored & (part["feature"] > 0)
    missing = part["uses_forecast"] & part["wind_mph"].isna() & scored
    return {
        "windy_subset_2024_25_n": int(windy.sum()),
        "totals_games_2024_25_n": int(scored.sum()),
        "outdoor_missing_forecast_2024_25_n": int(missing.sum()),
    }


def select() -> None:
    if not FORECASTS_PATH.exists():
        raise RuntimeError("forecast cache is missing; run fetch first")
    digest = hashlib.sha256(FORECASTS_PATH.read_bytes()).hexdigest()
    # 2016–23 only goes through the harness here. 2024–25 is counted, not graded.
    fit_frame = _analysis_frame(2016, 2023)
    fit_frame = fit_frame.rename(columns={"pred_total": "v6_total"})
    base = _fit_rows(fit_frame)
    if base["season"].max() > 2023:
        raise RuntimeError("fit frame includes a holdout season")
    candidates = []
    for form in FORMS:
        for threshold in THRESHOLDS:
            featured = _with_feature(base, form, threshold)
            crps, by_season = walk_forward_totals_crps(featured)
            residual = featured["actual_total"].to_numpy(dtype=float) - featured["v6_total"].to_numpy(dtype=float)
            w = ols_through_origin(residual, featured["feature"].to_numpy(dtype=float))
            candidates.append({
                "form": form,
                "threshold": threshold,
                "walk_forward_totals_crps": crps,
                "frozen_w": round(float(w), 6),
                "by_season": by_season,
                "n_2016_23": int(len(featured)),
                "n_feature_nonzero_2016_23": int((featured["feature"] > 0).sum()),
                "selected": False,
            })
            print(
                f"{form} t={threshold:.0f} walk-forward CRPS {crps:.4f} frozen_w {w:.4f} "
                f"nonzero {(featured['feature'] > 0).sum()}",
                flush=True,
            )
    chosen = choose_candidate(candidates)
    for row in candidates:
        row["selected"] = row["form"] == chosen["form"] and row["threshold"] == chosen["threshold"]
    # The touch count is allowed before scoring. It does not fit and does not grade.
    touch_src = _load_games(GRADE_COLUMNS)
    preds = pd.read_csv(PREDICTIONS_PATH, usecols=["game_id", "season"])
    holdout_ids = set(preds.loc[preds["season"].between(2024, 2025), "game_id"])
    holdout = touch_src[touch_src["game_id"].isin(holdout_ids)].copy()
    forecasts = _read_forecasts()
    holdout = holdout.merge(forecasts[["game_id", "wind_mph"]], on="game_id", how="left", validate="one_to_one")
    holdout["actual_total"] = holdout["home_score"] + holdout["away_score"]
    applied = []
    exposed = []
    for row in holdout.itertuples(index=False):
        stadium = _stadium(row.home_team)
        is_indoor = bool(stadium["is_indoor"]) if stadium is not None else False
        exposed.append(uses_forecast(row.roof, row.location, is_indoor))
        applied.append(applied_wind(row.roof, row.location, row.wind_mph, is_indoor))
    holdout["uses_forecast"] = exposed
    holdout["wind_applied"] = applied
    touch = _count_holdout_touch(holdout, chosen["form"], chosen["threshold"])
    bar_population = "windy_subset" if touch["windy_subset_2024_25_n"] >= MIN_PLAYS else "full_totals"
    fit_exposed = fit_frame["uses_forecast"] & fit_frame["actual_total"].notna()
    payload = {
        "model": "v6-blind plus wind on the total",
        "holdout_scored": False,
        "scored_seasons_max": 2023,
        "forms": list(FORMS),
        "thresholds_mph": list(THRESHOLDS),
        "cap_mph": WIND_CAP_MPH,
        "tie_break": "within 1e-6 of the best walk-forward totals CRPS, prefer threshold 12, then hinge",
        "selected_form": chosen["form"],
        "selected_threshold": chosen["threshold"],
        "frozen_w": chosen["frozen_w"],
        "walk_forward_totals_crps": chosen["walk_forward_totals_crps"],
        "candidates": candidates,
        "forecasts_sha256": digest,
        "n_fit_games": int(len(base)),
        "n_feature_nonzero_2016_23": chosen["n_feature_nonzero_2016_23"],
        "outdoor_missing_forecast_2016_23_n": int((fit_exposed & fit_frame["wind_mph"].isna()).sum()),
        "neutral_2016_23_n": int(fit_frame["location"].map(is_neutral).sum()),
        "bar_population": bar_population,
        "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED, "quantiles": [0.05, 0.95]},
        "sigma_total": TOTAL_SIGMA,
        "formula": "pred_total = v6_pred_total + w * feature; feature is 0 when the forecast is unused or missing",
        "training": "through-origin OLS on all 2016-2023 games with a result and a total line; 2015 excluded; 2016 walk-forward weight is 0",
        "shipped_reference": {
            "points_removed_per_mph_above_12": 0.22,
            "prior_points_at_15_plus_mph": [-5, -2],
            "note": "reference only; the slope above is the 2016-23 fit",
        },
        "precip_temperature_margin": "not used",
        "note": "2024 and later are counted for the bar population and are not scored in this file.",
        **touch,
    }
    assert_selection_payload(payload)
    text = json.dumps(payload, indent=2)
    FROZEN_PATH.write_text(text + "\n")
    print(
        f"selected {chosen['form']} threshold {chosen['threshold']} w {chosen['frozen_w']} "
        f"bar {bar_population} windy_2024_25 {touch['windy_subset_2024_25_n']}",
        flush=True,
    )
    print("HOLDOUT NOT SCORED", flush=True)


def _grade_population(frame: pd.DataFrame, windy_only: bool) -> dict:
    part = frame
    if windy_only:
        part = frame[frame["feature"] > 0]
    summary = grade_totals(
        part["actual_total"], part["prediction"], part["total_line"], part["v6_total"],
    )
    summary["n_feature_moves_total"] = int((part["feature"] > 0).sum()) if len(part) else 0
    summary["verdict"] = bar_verdict(summary)
    return summary


def evaluate() -> None:
    if not FROZEN_PATH.exists():
        raise RuntimeError("frozen.json is missing; select and commit PREREG before evaluate")
    frozen = json.loads(FROZEN_PATH.read_text())
    assert_selection_payload(frozen)
    digest = hashlib.sha256(FORECASTS_PATH.read_bytes()).hexdigest()
    if digest != frozen["forecasts_sha256"]:
        raise RuntimeError("forecast cache hash does not match the pre-registered file")
    form = frozen["selected_form"]
    threshold = float(frozen["selected_threshold"])
    w = float(frozen["frozen_w"])
    population = frozen["bar_population"]
    frame = _analysis_frame(2016, CONFIRM_SEASON)
    frame = frame.rename(columns={"pred_total": "v6_total"})
    frame = _with_feature(frame, form, threshold)
    frame["prediction"] = predict_total(frame["v6_total"], frame["feature"], w)
    # The frozen weight is not refit. 2016–23 is not used to change it here.
    holdout = frame[frame["season"].between(*HOLDOUT_SEASONS)].copy()
    confirm = frame[frame["season"] == CONFIRM_SEASON].copy()
    blocks = {
        "holdout_full_totals": _grade_population(holdout, windy_only=False),
        "holdout_windy_subset": _grade_population(holdout, windy_only=True),
        "confirm_2026_full_totals": _grade_population(confirm, windy_only=False),
        "confirm_2026_windy_subset": _grade_population(confirm, windy_only=True),
    }
    primary_key = "holdout_windy_subset" if population == "windy_subset" else "holdout_full_totals"
    primary = blocks[primary_key]
    verdict = {
        "holdout_scored": True,
        "retuned": False,
        "bar_population": population,
        "primary_block": primary_key,
        "selected_form": form,
        "selected_threshold": threshold,
        "frozen_w": w,
        "forecasts_sha256": digest,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "overall_pass": primary["verdict"]["overall_pass"],
        "blocks": blocks,
    }
    VERDICT_PATH.write_text(json.dumps(verdict, indent=2) + "\n")
    RESULTS_PATH.write_text(_render_results(frozen, verdict))
    print(f"primary {primary_key} overall_pass {verdict['overall_pass']}", flush=True)
    print(f"wrote {RESULTS_PATH}", flush=True)


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _block_lines(name: str, summary: dict, gated: bool) -> list[str]:
    ats = summary["ats"]
    lo, hi = summary["bootstrap_90_lo"], summary["bootstrap_90_hi"]
    verdict = summary["verdict"]
    role = "bar" if gated else "report"
    lines = [
        f"### {name} ({role})",
        "",
        "| Gate | Value | Result |",
        "| --- | --- | --- |",
        f"| Totals ATS | {_fmt(ats)} ({summary['wins']}/{summary['n_ats']}) | {verdict['ats_52_5_on_400']['pass']} |",
        f"| Plays | {summary['n_ats']} (games in block {summary['n']}) | {summary['n_ats'] >= MIN_PLAYS} |",
        f"| Bootstrap 90% CI | {_fmt(lo)}–{_fmt(hi)} | {verdict['bootstrap_90_lo_above_50']['pass']} |",
        "| CLV | unavailable (opener n = 0) | n/a |",
        (
            f"| Totals CRPS | ablation {_fmt(summary['totals_crps'], 4)} "
            f"v6 {_fmt(summary['v6_totals_crps'], 4)} "
            f"close {_fmt(summary['close_totals_crps'], 4)} | "
            f"{verdict['no_totals_crps_regression_vs_v6']['pass']} |"
        ),
        f"| p (one-sided binomial) | {_fmt(summary['p_value'], 4)} | {verdict['p_below_0_01']['pass']} |",
        "",
        f"Feature nonzero on {summary['n_feature_moves_total']} games in this block.",
        "",
    ]
    return lines


def _render_results(frozen: dict, verdict: dict) -> str:
    primary = verdict["blocks"][verdict["primary_block"]]
    word = "PASS" if verdict["overall_pass"] else "FAIL"
    lines = [
        "# Wind on totals",
        "",
        f"Verdict on the pre-registered 2024–25 bar: **{word}**.",
        "",
        "This is the first holdout look. The form, threshold, and slope were not refit. "
        "2026 is a one-look confirmation and is not part of the bar.",
        "",
        f"Form `{frozen['selected_form']}`, threshold {frozen['selected_threshold']} mph, "
        f"frozen slope {frozen['frozen_w']} points per feature unit "
        f"(negative removes points from the total). "
        f"Walk-forward 2016–23 totals CRPS was {frozen['walk_forward_totals_crps']:.4f}. "
        f"Bar population: `{verdict['bar_population']}`.",
        "",
        "The close CRPS is context. The CRPS gate is against v6-blind on the same games, sigma 10.",
        "",
        "## 2024–25 bar",
        "",
    ]
    lines.extend(_block_lines(
        "Full totals set",
        verdict["blocks"]["holdout_full_totals"],
        gated=verdict["primary_block"] == "holdout_full_totals",
    ))
    lines.extend(_block_lines(
        "Windy subset",
        verdict["blocks"]["holdout_windy_subset"],
        gated=verdict["primary_block"] == "holdout_windy_subset",
    ))
    lines.append(f"Overall: **{word}**. The gated block is `{verdict['primary_block']}`.")
    lines.append("")
    lines.append("## 2026 one-look")
    lines.append("")
    lines.extend(_block_lines("Full totals set", verdict["blocks"]["confirm_2026_full_totals"], gated=False))
    lines.extend(_block_lines("Windy subset", verdict["blocks"]["confirm_2026_windy_subset"], gated=False))
    lines.append("CLV is unavailable because no opener file is in this snapshot. That is neither a pass nor a fail.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"fetch", "select", "evaluate"}:
        raise SystemExit("usage: wind_totals_ablation.py fetch|select|evaluate")
    {"fetch": fetch, "select": select, "evaluate": evaluate}[sys.argv[1]]()


if __name__ == "__main__":
    main()
