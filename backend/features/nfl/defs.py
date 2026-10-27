"""Market-blind NFL team features from an nflverse play-by-play frame.

The input is a plays ``DataFrame``. The output is a team-game frame and a
team-season frame. Nothing in this module reads a database, a file, or the
network.

Team-game rows are as-of the start of that week: season-to-date, last-4,
percentiles, the ridge, and luck use only earlier weeks in the same season.
Team-season rows include every game of that season. The ridge prior carries
across seasons at two-thirds of the previous end-of-season rating (v6's
preseason keep) with continuity 1, because roster continuity is not a function
of the play frame.

Recency in the raw rates is the last four team-games, not a half-life. The
ridge uses equal weight per prior team-game and the frozen v6-blind shrinkage:
pass and EPA k = 8, rush k = 10, success k = 12, defense weight 0.55.
Explosive rate is its own ridge with success's k. The 2–3× multiplier searched
in the feature bundle is not part of this definition.

Luck columns are inputs, shrunk by ``n / (n + k)``. They are not a combined
luck index. Positive means the team was luckier than the baseline.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

import warnings

import numpy as np
import pandas as pd

WP_LO = 0.10
WP_HI = 0.90
EXPLOSIVE_YARDS = 20
LAST_N = 4
DEF_WEIGHT = 0.55
K_PASS = 8.0
K_RUSH = 10.0
K_SUCCESS = 12.0
K_EPA = 8.0
K_EXPLOSIVE = 12.0
PRESEASON_KEEP = 2.0 / 3.0
RIDGE_EPS = 1e-4

# Prior sample sizes for luck shrinkage. They are definition constants.
K_FUMBLE = 50.0
K_INT = 200.0
K_RZ = 30.0
K_ONE_SCORE = 16.0
K_FG = 40.0
BASE_INT_RATE = 0.025
BASE_RZ_TD = 0.55
FUMBLE_EXPECTED = 0.5
ONE_SCORE_EXPECTED = 0.5
ONE_SCORE_MARGIN = 8
RZ_YARDLINE = 20

# Frozen distance curve, not fit to the caller's frame.
# logit(p) = 7.0 - 0.13 * kick_distance. About 96% at 30, 86% at 40, 62% at 50.
FG_LOGIT_INTERCEPT = 7.0
FG_LOGIT_SLOPE = -0.13

_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR",
    "OAK": "LV", "SD": "LAC", "STL": "LAR", "HST": "HOU", "BLT": "BAL", "CLV": "CLE",
}

REQUIRED = (
    "game_id", "season", "week", "posteam", "defteam", "home_team", "away_team",
    "epa", "success", "pass", "rush", "down", "ydstogo", "yards_gained", "wp",
    "qb_kneel", "qb_spike", "special_teams_play",
)

# Exact play-by-play columns this module reads. The market guard is this
# allowlist: a column that is not on it and is market-like is rejected.
# ``wp`` is the nflverse model win probability. ``vegas_wp`` and
# ``vegas_home_wp`` are not on the list. ``fumble_lost`` is not read.
PBP_INPUT_COLUMNS: tuple[str, ...] = (
    "game_id", "season", "week", "season_type",
    "posteam", "defteam", "home_team", "away_team",
    "epa", "success", "pass", "rush", "down", "ydstogo", "yards_gained", "wp",
    "qb_kneel", "qb_spike", "special_teams_play",
    "home_score", "away_score",
    "interception", "fumble", "fumble_forced", "fumble_recovery_1_team",
    "pass_attempt", "pass_defense_1_player_id", "pass_defense_2_player_id",
    "field_goal_attempt", "field_goal_result", "kick_distance",
    "yardline_100", "touchdown", "drive", "fixed_drive",
)

# Raw offensive rates. Each produces ``{name}_season`` and ``{name}_last4``.
RATE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("epa_play", "epa/play", "Offensive EPA per scrimmage play inside the WP band"),
    ("success_rate", "rate", "Offensive success rate. nflverse success flag. Explosive plays stay out of this rate"),
    ("explosive_rate", "rate", "Share of scrimmage plays with yards_gained >= 20. Separate from success rate"),
    ("pass_epa", "epa/play", "EPA per pass play"),
    ("rush_epa", "epa/play", "EPA per rush play"),
    ("pass_sr", "rate", "Success rate on pass plays"),
    ("rush_sr", "rate", "Success rate on rush plays"),
    ("early_epa", "epa/play", "EPA per early-down play. Early: 1st, or 2nd and <= 7"),
    ("passing_epa", "epa/play", "EPA per passing-down play. Passing: 2nd and >= 8, or 3rd/4th and >= 5"),
    ("early_sr", "rate", "Success rate on early downs"),
    ("passing_sr", "rate", "Success rate on passing downs"),
    ("early_pass_epa", "epa/play", "EPA per early-down pass"),
    ("passing_pass_epa", "epa/play", "EPA per passing-down pass"),
    ("early_rush_epa", "epa/play", "EPA per early-down rush"),
    ("passing_rush_epa", "epa/play", "EPA per passing-down rush"),
    ("early_pass_sr", "rate", "Success rate on early-down passes"),
    ("passing_pass_sr", "rate", "Success rate on passing-down passes"),
    ("early_rush_sr", "rate", "Success rate on early-down rushes"),
    ("passing_rush_sr", "rate", "Success rate on passing-down rushes"),
    ("epa_allowed", "epa/play", "EPA per scrimmage play allowed on defense"),
)

PCT_METRICS = (
    "epa_play", "success_rate", "explosive_rate", "pass_epa", "rush_epa",
    "early_epa", "passing_epa", "sched_strength",
)

_SUM_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("epa_play", "epa_num", "epa_den"),
    ("success_rate", "sr_num", "epa_den"),
    ("explosive_rate", "xpl_num", "epa_den"),
    ("pass_epa", "pass_epa_num", "pass_den"),
    ("rush_epa", "rush_epa_num", "rush_den"),
    ("pass_sr", "pass_sr_num", "pass_den"),
    ("rush_sr", "rush_sr_num", "rush_den"),
    ("early_epa", "early_epa_num", "early_den"),
    ("passing_epa", "passdown_epa_num", "passdown_den"),
    ("early_sr", "early_sr_num", "early_den"),
    ("passing_sr", "passdown_sr_num", "passdown_den"),
    ("early_pass_epa", "early_pass_epa_num", "early_pass_den"),
    ("passing_pass_epa", "passdown_pass_epa_num", "passdown_pass_den"),
    ("early_rush_epa", "early_rush_epa_num", "early_rush_den"),
    ("passing_rush_epa", "passdown_rush_epa_num", "passdown_rush_den"),
    ("early_pass_sr", "early_pass_sr_num", "early_pass_den"),
    ("passing_pass_sr", "passdown_pass_sr_num", "passdown_pass_den"),
    ("early_rush_sr", "early_rush_sr_num", "early_rush_den"),
    ("passing_rush_sr", "passdown_rush_sr_num", "passdown_rush_den"),
    ("epa_allowed", "epa_allowed_num", "epa_allowed_den"),
)


def _contract() -> dict[str, tuple[type, str, str]]:
    """Column -> (dtype, unit, description). Shared by both output frames."""
    asof = (
        " On a team-game row the season window and the luck counts use only earlier "
        "weeks. On a team-season row they include every game."
    )
    out: dict[str, tuple[type, str, str]] = {
        "season": (int, "season", "NFL season"),
        "week": (int, "week", "NFL week. Team-game only"),
        "game_id": (str, "game", "nflverse game id. Team-game only"),
        "team": (str, "team", "Canonical franchise code. LA, OAK, SD, and STL map to the current franchise"),
        "opponent": (str, "team", "Canonical opponent. Team-game only"),
        "is_home": (int, "indicator", "1 when the team is home. Team-game only"),
        "games": (int, "games", "Games in the season frame. Team-season only"),
        "n_plays_season": (int, "plays", "Scrimmage plays inside the WP band in the season window." + asof),
        "n_plays_last4": (int, "plays", "Scrimmage plays inside the WP band over the last four team-games"),
    }
    for name, unit, desc in RATE_SPECS:
        out[f"{name}_season"] = (float, unit, desc + "." + asof)
        out[f"{name}_last4"] = (float, unit, desc + " over the last four team-games.")
    for name in PCT_METRICS:
        out[f"{name}_season_pct"] = (
            float, "percentile",
            f"Midrank percentile (0-100, higher is better) of {name}_season among teams that week or season.",
        )
        out[f"{name}_last4_pct"] = (
            float, "percentile",
            f"Midrank percentile (0-100, higher is better) of {name}_last4 among teams that week or season.",
        )
    ridge = (
        ("pass_off_adj", "epa/play", "Ridge pass offense. k = 8 games, shrunk toward the preseason prior"),
        ("pass_def_adj", "epa/play", "Ridge pass defense. Positive means more EPA allowed to the opposing passer. k = 8"),
        ("rush_off_adj", "epa/play", "Ridge rush offense. k = 10"),
        ("rush_def_adj", "epa/play", "Ridge rush defense. Positive means more EPA allowed. k = 10"),
        ("sr_off_adj", "rate", "Ridge success-rate offense. k = 12"),
        ("sr_def_adj", "rate", "Ridge success-rate defense. Positive means a higher success rate allowed. k = 12"),
        ("xpl_off_adj", "rate", "Ridge explosive-rate offense, separate from success. k = 12, not the searched 2-3x multiplier"),
        ("xpl_def_adj", "rate", "Ridge explosive-rate defense. Positive means more explosives allowed. k = 12"),
        ("epa_off_adj", "epa/play", "Ridge EPA/play offense. k = 8"),
        ("epa_def_adj", "epa/play", "Ridge EPA/play defense. Positive means more EPA allowed. k = 8"),
        ("sched_strength", "epa/play", "Schedule-adjusted strength: epa_off_adj - 0.55 * epa_def_adj"),
    )
    for name, unit, desc in ridge:
        out[name] = (float, unit, desc + "." + asof)
    luck = (
        ("fumble_rec_luck", "Fumble recovery rate minus 0.5, shrunk with k = 50. When fumble_forced is present the count is forced fumbles, including ones the offense recovered. fumble_lost is not read. Positive means more recoveries than half"),
        ("int_luck", "Pass-attempt INT rate below the passes-defensed expectation, shrunk with k = 200 attempts. Positive means fewer interceptions than expected"),
        ("rz_td_luck", "Red-zone trip TD rate minus the league rate, shrunk with k = 30 trips. Positive means more red-zone TDs than expected"),
        ("one_score_luck", "Win rate in games decided by 8 points or fewer, minus 0.5, shrunk with k = 16 games"),
        ("fg_luck", "Field-goal make rate minus the frozen distance curve, shrunk with k = 40 attempts"),
    )
    for name, desc in luck:
        out[name] = (float, "shrunk residual", desc + "." + asof)
    for name, desc in (
        ("fumble_n", "Forced fumbles when fumble_forced is present, otherwise fumbles. Counted whether or not the ball was lost"),
        ("int_n", "Pass attempts"),
        ("rz_n", "Red-zone offensive trips"),
        ("one_score_n", "One-score games"),
        ("fg_n", "Field-goal attempts"),
    ):
        out[name] = (int, "count", desc + " in the same window as the luck column." + asof)
    return out


CONTRACT: dict[str, tuple[type, str, str]] = _contract()

TEAM_GAME_COLUMNS: tuple[str, ...] = (
    "season", "week", "game_id", "team", "opponent", "is_home",
    "n_plays_season", "n_plays_last4",
    *(f"{name}_season" for name, _, _ in RATE_SPECS),
    *(f"{name}_last4" for name, _, _ in RATE_SPECS),
    *(f"{name}_season_pct" for name in PCT_METRICS),
    *(f"{name}_last4_pct" for name in PCT_METRICS),
    "pass_off_adj", "pass_def_adj", "rush_off_adj", "rush_def_adj",
    "sr_off_adj", "sr_def_adj", "xpl_off_adj", "xpl_def_adj",
    "epa_off_adj", "epa_def_adj", "sched_strength",
    "fumble_rec_luck", "int_luck", "rz_td_luck", "one_score_luck", "fg_luck",
    "fumble_n", "int_n", "rz_n", "one_score_n", "fg_n",
)
TEAM_SEASON_COLUMNS: tuple[str, ...] = tuple(
    col for col in TEAM_GAME_COLUMNS if col not in {"week", "game_id", "opponent", "is_home"}
)
# ``games`` sits next to the team on the season frame.
TEAM_SEASON_COLUMNS = ("season", "team", "games") + tuple(
    col for col in TEAM_SEASON_COLUMNS if col not in {"season", "team"}
)


# Final scores. They contain "total" and are not a betting total.
_SCORE_COLUMNS = frozenset({"total_home_score", "total_away_score"})


def assert_no_market_columns(columns: Iterable[str]) -> None:
    """Raise if a frame carries a market column outside the pbp input allowlist.

    Allowed inputs are ``PBP_INPUT_COLUMNS`` — the columns ``build_features``
    reads. Anything else that is market-like is rejected. That includes
    ``vegas_wp`` and ``vegas_home_wp``, which a spread/odds/moneyline token
    list does not catch, plus ``spread_line``, ``total_line``, any ``*_odds``
    column, and any moneyline column. The same rule is applied to output
    frames: none of the contract columns are market-like, so a leaked market
    name fails here too.

    ``total_home_score`` and ``total_away_score`` are final scores, not a
    betting total, and are allowed. A column named ``total`` or ``total_line``
    is rejected. Non-market columns this module does not read (a play id, a
    description) may sit on the input and are ignored.
    """
    allowed = set(PBP_INPUT_COLUMNS)
    bad = [
        str(col) for col in columns
        if str(col) not in allowed and _is_market_like(str(col))
    ]
    if bad:
        raise ValueError(f"market columns are not features: {bad}")


def assert_output_columns(columns: Iterable[str], allowed: Sequence[str]) -> None:
    """Closed check for a team-game or team-season frame.

    Output columns must be the contract allowlist. A market-like name is
    rejected on these frames the same way it is rejected on input, including
    the college-football market names (``vegas_wp``, ``vegas_home_wp``,
    ``spread``, ``over_under``, moneylines, and ``*_odds``) that a short
    NFL token list misses.
    """
    allow = {str(col) for col in allowed}
    market = [str(col) for col in columns if _is_market_like(str(col))]
    if market:
        raise ValueError(f"market columns are not features: {market}")
    extra = [str(col) for col in columns if str(col) not in allow]
    if extra:
        raise ValueError(f"output columns outside the allowlist: {extra}")


def _is_market_like(name: str) -> bool:
    """True for NFL and college-football market columns that are not inputs.

    Detection is only applied to names outside the allowlist. ``wp`` is an
    allowed model win probability. ``vegas_wp`` is not.
    """
    key = name.lower()
    if key in _SCORE_COLUMNS:
        return False
    if "vegas" in key:
        return True
    if "moneyline" in key or key == "ml" or key.endswith("_ml"):
        return True
    if "odds" in key:
        return True
    if "spread" in key:
        return True
    if "over_under" in key or key in {"ou", "overunder"}:
        return True
    if key in {"total", "total_line"} or key.endswith("_total") or "total_line" in key:
        return True
    if key.endswith("_line") and "yardline" not in key:
        return True
    return False


def canon_team(value: object) -> str | None:
    """Map a team code onto the current franchise. Missing stays missing."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip().upper()
    if text in {"", "NAN", "NONE", "NAT"}:
        return None
    return _ALIASES.get(text, text)


def shrink(residual: float, n: float, k: float) -> float:
    """``n / (n + k)`` times the residual. n = 0 returns 0."""
    n = float(n)
    k = float(k)
    if n <= 0 or not np.isfinite(n):
        return 0.0
    return (n / (n + k)) * float(residual)


def field_goal_expected_make(distance: float) -> float:
    """Make probability from the frozen distance curve."""
    logit = FG_LOGIT_INTERCEPT + FG_LOGIT_SLOPE * float(distance)
    return float(1.0 / (1.0 + np.exp(-logit)))


def ridge_team(
    offense: np.ndarray,
    defense: np.ndarray,
    is_home: np.ndarray,
    y: np.ndarray,
    weight: np.ndarray,
    n_teams: int,
    prior_off: np.ndarray,
    prior_def: np.ndarray,
    k: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Weighted ridge ``y = ic + off_i + def_j + hfa·home``, the v6-blind equation.

    Team effects shrink toward ``prior_*`` by ``k`` games. The intercept and
    home field take a ridge epsilon so the design stays invertible.
    """
    n = len(y)
    if n == 0:
        return prior_off.copy(), prior_def.copy(), 0.0, 0.0
    p = 2 * n_teams + 2
    x = np.zeros((n, p), dtype=float)
    rows = np.arange(n)
    x[rows, offense] = 1.0
    x[rows, n_teams + defense] = 1.0
    x[:, 2 * n_teams] = 1.0
    x[:, 2 * n_teams + 1] = is_home
    pen = np.full(p, RIDGE_EPS)
    pen[:n_teams] = k
    pen[n_teams:2 * n_teams] = k
    xtw = x.T * weight
    a = xtw @ x
    a.flat[:: p + 1] += pen
    rhs = xtw @ y
    rhs[:n_teams] += k * prior_off
    rhs[n_teams:2 * n_teams] += k * prior_def
    try:
        coef = np.linalg.solve(a, rhs)
    except np.linalg.LinAlgError:
        coef = np.linalg.lstsq(a, rhs, rcond=None)[0]
    return (
        coef[:n_teams].copy(),
        coef[n_teams:2 * n_teams].copy(),
        float(coef[2 * n_teams]),
        float(coef[2 * n_teams + 1]),
    )


def _require(plays: pd.DataFrame) -> None:
    missing = [col for col in REQUIRED if col not in plays.columns]
    if missing:
        raise ValueError(f"plays frame is missing columns: {missing}")


def _prepare(plays: pd.DataFrame) -> pd.DataFrame:
    df = plays.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].isin(["REG", "POST"])].copy()
    for col in ("posteam", "defteam", "home_team", "away_team"):
        df[col] = df[col].map(canon_team)
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df = df[df["season"].notna() & df["week"].notna() & df["game_id"].notna()]
    df["season"] = df["season"].astype(int)
    df["week"] = df["week"].astype(int)
    df["game_id"] = df["game_id"].astype(str)
    return df


def _scrimmage(df: pd.DataFrame) -> pd.DataFrame:
    """Hard WP filter. Kneels, spikes, special teams, and missing WP are out."""
    work = df[df["posteam"].notna() & df["defteam"].notna() & df["epa"].notna()].copy()
    kneel = work["qb_kneel"].fillna(0).astype(float) == 1
    spike = work["qb_spike"].fillna(0).astype(float) == 1
    special = work["special_teams_play"].fillna(0).astype(float) == 1
    band = work["wp"].between(WP_LO, WP_HI)
    is_pass = work["pass"].fillna(0).astype(float) == 1
    is_rush = work["rush"].fillna(0).astype(float) == 1
    work = work.loc[~kneel & ~spike & ~special & band & (is_pass | is_rush)].copy()
    if len(work) == 0:
        work["is_pass"] = []
        work["is_rush"] = []
        work["early"] = []
        work["passing_down"] = []
        work["is_xpl"] = []
        work["suc"] = []
        return work
    work["is_pass"] = is_pass.loc[work.index].to_numpy()
    work["is_rush"] = is_rush.loc[work.index].to_numpy()
    down = pd.to_numeric(work["down"], errors="coerce")
    ytg = pd.to_numeric(work["ydstogo"], errors="coerce")
    work["early"] = ((down == 1) | ((down == 2) & (ytg <= 7))).fillna(False).to_numpy()
    work["passing_down"] = (
        ((down == 2) & (ytg >= 8)) | (down.isin([3, 4]) & (ytg >= 5))
    ).fillna(False).to_numpy()
    yards = pd.to_numeric(work["yards_gained"], errors="coerce").fillna(0)
    work["is_xpl"] = (yards >= EXPLOSIVE_YARDS).to_numpy()
    work["suc"] = work["success"].fillna(0).astype(float).to_numpy()
    work["epa"] = work["epa"].astype(float)
    return work


def _spine(df: pd.DataFrame) -> pd.DataFrame:
    """One row per team per game, from home and away, ignoring play rows without both."""
    games = df.dropna(subset=["home_team", "away_team"]).drop_duplicates("game_id")
    rows = []
    for rec in games.itertuples(index=False):
        home = rec.home_team
        away = rec.away_team
        if home is None or away is None:
            continue
        base = {
            "season": int(rec.season), "week": int(rec.week), "game_id": rec.game_id,
        }
        rows.append({**base, "team": home, "opponent": away, "is_home": 1})
        rows.append({**base, "team": away, "opponent": home, "is_home": 0})
    if not rows:
        return pd.DataFrame(columns=["season", "week", "game_id", "team", "opponent", "is_home"])
    out = pd.DataFrame(rows)
    return out.sort_values(["season", "week", "game_id", "team"]).reset_index(drop=True)


def _fumble_rows(scr: pd.DataFrame) -> pd.DataFrame:
    """Fumbles inside the WP band. Recovery is not required.

    When ``fumble_forced`` is on the frame, the event is ``fumble_forced == 1``.
    A forced fumble the offense keeps still counts. ``fumble_lost`` is not read.
    The pinned slim fixture has no ``fumble_forced`` column, so that frame
    falls back to ``fumble == 1``.
    """
    if len(scr) == 0:
        return scr
    if "fumble_forced" in scr.columns:
        flag = pd.to_numeric(scr["fumble_forced"], errors="coerce").fillna(0).eq(1)
        return scr.loc[flag]
    if "fumble" in scr.columns:
        flag = pd.to_numeric(scr["fumble"], errors="coerce").fillna(0).eq(1)
        return scr.loc[flag]
    return scr.iloc[0:0]


def _player_present(value: object) -> bool:
    """True when a player-id cell names someone. Not a team code."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    text = str(value).strip()
    return text not in {"", "NA", "None", "nan", "NaN", "<NA>"}


def _offense_sums(scr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Offensive sums keyed by the offense, and EPA allowed keyed by the defense."""
    empty_off = pd.DataFrame(columns=["season", "week", "game_id", "team"])
    empty_allow = pd.DataFrame(columns=["season", "week", "game_id", "team", "epa_allowed_num", "epa_allowed_den"])
    if len(scr) == 0:
        return empty_off, empty_allow
    work = scr.copy()
    epa = work["epa"].to_numpy(dtype=float)
    suc = work["suc"].to_numpy(dtype=float)
    xpl = work["is_xpl"].astype(float).to_numpy()
    early = work["early"].to_numpy()
    passing = work["passing_down"].to_numpy()
    is_pass = work["is_pass"].to_numpy()
    is_rush = work["is_rush"].to_numpy()

    def masked(flag: np.ndarray, value: np.ndarray | None = None) -> np.ndarray:
        if value is None:
            return flag.astype(float)
        return np.where(flag, value, 0.0)

    work["epa_num"] = epa
    work["epa_den"] = 1.0
    work["sr_num"] = suc
    work["xpl_num"] = xpl
    work["pass_epa_num"] = masked(is_pass, epa)
    work["pass_den"] = masked(is_pass)
    work["pass_sr_num"] = masked(is_pass, suc)
    work["rush_epa_num"] = masked(is_rush, epa)
    work["rush_den"] = masked(is_rush)
    work["rush_sr_num"] = masked(is_rush, suc)
    work["early_epa_num"] = masked(early, epa)
    work["early_den"] = masked(early)
    work["early_sr_num"] = masked(early, suc)
    work["passdown_epa_num"] = masked(passing, epa)
    work["passdown_den"] = masked(passing)
    work["passdown_sr_num"] = masked(passing, suc)
    work["early_pass_epa_num"] = masked(early & is_pass, epa)
    work["early_pass_den"] = masked(early & is_pass)
    work["early_pass_sr_num"] = masked(early & is_pass, suc)
    work["passdown_pass_epa_num"] = masked(passing & is_pass, epa)
    work["passdown_pass_den"] = masked(passing & is_pass)
    work["passdown_pass_sr_num"] = masked(passing & is_pass, suc)
    work["early_rush_epa_num"] = masked(early & is_rush, epa)
    work["early_rush_den"] = masked(early & is_rush)
    work["early_rush_sr_num"] = masked(early & is_rush, suc)
    work["passdown_rush_epa_num"] = masked(passing & is_rush, epa)
    work["passdown_rush_den"] = masked(passing & is_rush)
    work["passdown_rush_sr_num"] = masked(passing & is_rush, suc)
    measure_cols = [col for col in work.columns if col.endswith("_num") or col.endswith("_den")]
    agg = work.groupby(["season", "week", "game_id", "posteam", "defteam"], as_index=False)[measure_cols].sum()
    agg = agg.rename(columns={"posteam": "team", "defteam": "defense"})
    allowed = agg[["season", "week", "game_id", "defense", "epa_num", "epa_den"]].rename(
        columns={"defense": "team", "epa_num": "epa_allowed_num", "epa_den": "epa_allowed_den"},
    )
    offense = agg.drop(columns=["defense"])
    return offense, allowed


def _luck_counts(df: pd.DataFrame, scr: pd.DataFrame) -> pd.DataFrame:
    """Per team-game luck numerators. Fumbles and interceptions use the WP band."""
    rows: dict[tuple, dict[str, float]] = {}

    def slot(season: int, week: int, game_id: str, team: str) -> dict[str, float]:
        key = (season, week, game_id, team)
        found = rows.get(key)
        if found is None:
            found = {
                "fum_n": 0.0, "fum_rec": 0.0,
                "int_att": 0.0, "int_pd": 0.0, "int_nopd": 0.0,
                "int_on_pd": 0.0, "int_on_nopd": 0.0,
                "rz_trips": 0.0, "rz_td": 0.0,
                "fg_n": 0.0, "fg_resid": 0.0,
                "one_score": 0.0, "win": 0.0,
            }
            rows[key] = found
        return found

    fum = _fumble_rows(scr)
    if len(fum):
        for rec in fum.itertuples(index=False):
            for team in (rec.posteam, rec.defteam):
                if team is not None:
                    slot(int(rec.season), int(rec.week), rec.game_id, team)["fum_n"] += 1.0
            recovered = canon_team(getattr(rec, "fumble_recovery_1_team", None)) if "fumble_recovery_1_team" in scr.columns else None
            if recovered in (rec.posteam, rec.defteam):
                slot(int(rec.season), int(rec.week), rec.game_id, recovered)["fum_rec"] += 1.0

    if len(scr):
        attempt = scr["pass_attempt"].fillna(0).astype(float).eq(1) if "pass_attempt" in scr.columns else scr["is_pass"]
        passes = scr.loc[attempt.to_numpy() if not isinstance(attempt, pd.Series) else attempt]
        has_pd_col = "pass_defense_1_player_id" in passes.columns or "pass_defense_2_player_id" in passes.columns
        for rec in passes.itertuples(index=False):
            bucket = slot(int(rec.season), int(rec.week), rec.game_id, rec.posteam)
            pd_flag = False
            if has_pd_col:
                for attr in ("pass_defense_1_player_id", "pass_defense_2_player_id"):
                    if hasattr(rec, attr) and _player_present(getattr(rec, attr)):
                        pd_flag = True
                        break
            intercepted = 0.0
            if hasattr(rec, "interception"):
                intercepted = float(pd.to_numeric(pd.Series([rec.interception]), errors="coerce").fillna(0).iloc[0] == 1)
            bucket["int_att"] += 1.0
            if pd_flag:
                bucket["int_pd"] += 1.0
                bucket["int_on_pd"] += intercepted
            else:
                bucket["int_nopd"] += 1.0
                bucket["int_on_nopd"] += intercepted

    rz_src = df[df["posteam"].notna() & df["special_teams_play"].fillna(0).astype(float).ne(1)].copy()
    if len(rz_src) and "yardline_100" in rz_src.columns and "drive" in rz_src.columns:
        yard = pd.to_numeric(rz_src["yardline_100"], errors="coerce")
        rz_src = rz_src[(yard <= RZ_YARDLINE) & (yard > 0)]
        drive_col = "fixed_drive" if "fixed_drive" in rz_src.columns else "drive"
        if len(rz_src):
            rz_src = rz_src.copy()
            rz_src["td_flag"] = pd.to_numeric(rz_src["touchdown"], errors="coerce").fillna(0) if "touchdown" in rz_src.columns else 0.0
            grouped = rz_src.groupby(["season", "week", "game_id", "posteam", drive_col], as_index=False)["td_flag"].max()
            for rec in grouped.itertuples(index=False):
                bucket = slot(int(rec.season), int(rec.week), rec.game_id, rec.posteam)
                bucket["rz_trips"] += 1.0
                bucket["rz_td"] += float(rec.td_flag > 0)

    if "field_goal_attempt" in df.columns:
        fg = df[pd.to_numeric(df["field_goal_attempt"], errors="coerce").fillna(0).eq(1)].copy()
        if len(fg):
            dist = pd.to_numeric(fg["kick_distance"], errors="coerce") if "kick_distance" in fg.columns else pd.Series(np.nan, index=fg.index)
            result = fg["field_goal_result"].astype(str).str.lower() if "field_goal_result" in fg.columns else pd.Series("", index=fg.index)
            fg = fg.assign(dist_yards=dist, made_flag=result.eq("made"))
            fg = fg[fg["dist_yards"].notna() & fg["posteam"].notna()]
            for rec in fg.itertuples(index=False):
                bucket = slot(int(rec.season), int(rec.week), rec.game_id, rec.posteam)
                bucket["fg_n"] += 1.0
                bucket["fg_resid"] += float(rec.made_flag) - field_goal_expected_make(float(rec.dist_yards))

    if "home_score" in df.columns and "away_score" in df.columns:
        scores = df.dropna(subset=["home_team", "away_team"]).groupby("game_id", as_index=False).agg(
            season=("season", "first"), week=("week", "first"),
            home_team=("home_team", "first"), away_team=("away_team", "first"),
            home_score=("home_score", "max"), away_score=("away_score", "max"),
        )
        for rec in scores.itertuples(index=False):
            if not np.isfinite(rec.home_score) or not np.isfinite(rec.away_score):
                continue
            margin = abs(float(rec.home_score) - float(rec.away_score))
            if margin > ONE_SCORE_MARGIN:
                continue
            home_win = float(rec.home_score > rec.away_score)
            away_win = float(rec.away_score > rec.home_score)
            slot(int(rec.season), int(rec.week), rec.game_id, rec.home_team)["one_score"] += 1.0
            slot(int(rec.season), int(rec.week), rec.game_id, rec.home_team)["win"] += home_win
            slot(int(rec.season), int(rec.week), rec.game_id, rec.away_team)["one_score"] += 1.0
            slot(int(rec.season), int(rec.week), rec.game_id, rec.away_team)["win"] += away_win

    if not rows:
        return pd.DataFrame(columns=["season", "week", "game_id", "team"])
    out = pd.DataFrame([{"season": k[0], "week": k[1], "game_id": k[2], "team": k[3], **v} for k, v in rows.items()])
    return out


def _windows(spine: pd.DataFrame, value_cols: Sequence[str]) -> pd.DataFrame:
    """Season-to-date and last-four sums, excluding the current game. Within a season only."""
    out = spine.sort_values(["season", "team", "week", "game_id"]).reset_index(drop=True)
    grouped = out.groupby(["season", "team"], sort=False)
    for col in value_cols:
        shifted = grouped[col].cumsum() - out[col]
        out[f"{col}_asof"] = shifted.to_numpy()
        rolled = grouped[col].transform(lambda s: s.rolling(LAST_N, min_periods=1).sum().shift(1))
        out[f"{col}_last4"] = rolled.fillna(0.0).to_numpy()
    return out


def _rate(num: pd.Series, den: pd.Series) -> pd.Series:
    out = np.full(len(num), np.nan, dtype=float)
    d = den.to_numpy(dtype=float)
    n = num.to_numpy(dtype=float)
    ok = np.isfinite(d) & (d > 0) & np.isfinite(n)
    out[ok] = n[ok] / d[ok]
    return pd.Series(out, index=num.index)


def _percentile(values: np.ndarray) -> np.ndarray:
    """Analytics midrank, 0–100, rounded to one decimal. NaN stays NaN and is left out."""
    out = np.full(len(values), np.nan, dtype=float)
    ok = np.isfinite(values)
    n = int(ok.sum())
    if n == 0:
        return out
    v = values[ok]
    less = (v[None, :] < v[:, None]).sum(axis=1)
    equal = (v[None, :] == v[:, None]).sum(axis=1)
    out[np.flatnonzero(ok)] = np.round(100.0 * (less + 0.5 * equal) / n, 1)
    return out


def _assign_percentiles(frame: pd.DataFrame, cols: Sequence[str], keys: Sequence[str]) -> None:
    for col in cols:
        pieces = []
        for _, group in frame.groupby(list(keys), sort=False):
            pieces.append(pd.Series(_percentile(group[col].to_numpy(dtype=float)), index=group.index))
        if pieces:
            frame[f"{col}_pct"] = pd.concat(pieces).reindex(frame.index)
        else:
            frame[f"{col}_pct"] = np.nan


def _ridge_panel(
    observations: pd.DataFrame,
    teams: list[str],
    y_col: str,
    den_col: str,
    k: float,
) -> dict[tuple[int, int | None], tuple[np.ndarray, np.ndarray]]:
    """As-of ratings keyed by (season, week). The None week is the end-of-season fit."""
    n = len(teams)
    ti = {team: i for i, team in enumerate(teams)}
    ratings: dict[tuple[int, int | None], tuple[np.ndarray, np.ndarray]] = {}
    end_off = np.zeros(n)
    end_def = np.zeros(n)
    use = observations[observations[den_col] > 0].copy()
    if len(use) == 0:
        return ratings
    use["off_i"] = use["team"].map(ti)
    use["def_i"] = use["opponent"].map(ti)
    use = use[use["off_i"].notna() & use["def_i"].notna()]
    use["y"] = use[y_col] / use[den_col]
    seasons = sorted(int(s) for s in use["season"].unique())
    # Seasons present only on the spine still need a carried prior, so walk every
    # season the caller will ask for via ``observations`` itself.
    for season in seasons:
        prior_off = PRESEASON_KEEP * end_off
        prior_def = PRESEASON_KEEP * end_def
        sg = use[use["season"] == season]
        weeks = sorted(int(w) for w in sg["week"].unique())
        for week in weeks:
            hist = sg[sg["week"] < week]
            off, de, _, _ = ridge_team(
                hist["off_i"].to_numpy(dtype=int),
                hist["def_i"].to_numpy(dtype=int),
                hist["is_home"].to_numpy(dtype=float),
                hist["y"].to_numpy(dtype=float),
                np.ones(len(hist), dtype=float),
                n, prior_off, prior_def, k,
            )
            ratings[(season, week)] = (off, de)
        off, de, _, _ = ridge_team(
            sg["off_i"].to_numpy(dtype=int),
            sg["def_i"].to_numpy(dtype=int),
            sg["is_home"].to_numpy(dtype=float),
            sg["y"].to_numpy(dtype=float),
            np.ones(len(sg), dtype=float),
            n, prior_off, prior_def, max(k, 0.5),
        )
        ratings[(season, None)] = (off, de)
        end_off, end_def = off, de
    return ratings


def _lookup_rating(
    ratings: dict[tuple[int, int | None], tuple[np.ndarray, np.ndarray]],
    season: int,
    week: int | None,
    index: int,
    side: int,
) -> float:
    found = ratings.get((season, week))
    if found is None:
        return 0.0
    return float(found[side][index])


def _luck_from_counts(
    fum_n: float, fum_rec: float,
    int_att: float, int_pd: float, int_nopd: float, int_on_pd: float, int_on_nopd: float,
    loo_pd: float, loo_nopd: float, loo_int_pd: float, loo_int_nopd: float,
    rz_n: float, rz_td: float, loo_rz_n: float, loo_rz_td: float,
    one_n: float, wins: float,
    fg_n: float, fg_resid: float,
) -> dict[str, float]:
    fumble_rate = (fum_rec / fum_n) if fum_n > 0 else FUMBLE_EXPECTED
    rate_pd = (loo_int_pd / loo_pd) if loo_pd > 0 else BASE_INT_RATE
    rate_nopd = (loo_int_nopd / loo_nopd) if loo_nopd > 0 else BASE_INT_RATE
    if int_att > 0:
        expected_int = (int_pd * rate_pd + int_nopd * rate_nopd) / int_att
        actual_int = (int_on_pd + int_on_nopd) / int_att
        int_resid = expected_int - actual_int
    else:
        int_resid = 0.0
    if rz_n > 0:
        expected_rz = (loo_rz_td / loo_rz_n) if loo_rz_n > 0 else BASE_RZ_TD
        rz_resid = (rz_td / rz_n) - expected_rz
    else:
        rz_resid = 0.0
    one_resid = ((wins / one_n) - ONE_SCORE_EXPECTED) if one_n > 0 else 0.0
    fg_resid_mean = (fg_resid / fg_n) if fg_n > 0 else 0.0
    return {
        "fumble_rec_luck": shrink(fumble_rate - FUMBLE_EXPECTED, fum_n, K_FUMBLE),
        "int_luck": shrink(int_resid, int_att, K_INT),
        "rz_td_luck": shrink(rz_resid, rz_n, K_RZ),
        "one_score_luck": shrink(one_resid, one_n, K_ONE_SCORE),
        "fg_luck": shrink(fg_resid_mean, fg_n, K_FG),
        "fumble_n": fum_n, "int_n": int_att, "rz_n": rz_n, "one_score_n": one_n, "fg_n": fg_n,
    }


def _empty_game() -> pd.DataFrame:
    return _cast(pd.DataFrame(columns=list(TEAM_GAME_COLUMNS)), TEAM_GAME_COLUMNS)


def _empty_season() -> pd.DataFrame:
    return _cast(pd.DataFrame(columns=list(TEAM_SEASON_COLUMNS)), TEAM_SEASON_COLUMNS)


def _cast(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    out = frame.reindex(columns=list(columns)).copy()
    for col in columns:
        dtype, _, _ = CONTRACT[col]
        if dtype is int:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(np.int64)
        elif dtype is float:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        else:
            out[col] = out[col].where(out[col].notna(), None).astype("string")
    return out


def build_features(plays: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(team_game, team_season)`` feature frames.

    Both frames obey ``CONTRACT``. Market columns on the input raise.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _build_features(plays)


def _build_features(plays: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    assert_no_market_columns(plays.columns)
    _require(plays)
    raw = _prepare(plays)
    if len(raw) == 0:
        return _empty_game(), _empty_season()
    scr = _scrimmage(raw)
    spine = _spine(raw)
    if len(spine) == 0:
        return _empty_game(), _empty_season()

    summed, allowed = _offense_sums(scr)
    if len(summed):
        spine = spine.merge(summed, on=["season", "week", "game_id", "team"], how="left")
    if len(allowed):
        spine = spine.merge(allowed, on=["season", "week", "game_id", "team"], how="left")
    luck = _luck_counts(raw, scr)
    if len(luck):
        spine = spine.merge(luck, on=["season", "week", "game_id", "team"], how="left")

    value_cols = [
        "epa_num", "epa_den", "sr_num", "xpl_num",
        "pass_epa_num", "pass_den", "pass_sr_num",
        "rush_epa_num", "rush_den", "rush_sr_num",
        "early_epa_num", "early_den", "early_sr_num",
        "passdown_epa_num", "passdown_den", "passdown_sr_num",
        "early_pass_epa_num", "early_pass_den", "early_pass_sr_num",
        "passdown_pass_epa_num", "passdown_pass_den", "passdown_pass_sr_num",
        "early_rush_epa_num", "early_rush_den", "early_rush_sr_num",
        "passdown_rush_epa_num", "passdown_rush_den", "passdown_rush_sr_num",
        "epa_allowed_num", "epa_allowed_den",
        "fum_n", "fum_rec", "int_att", "int_pd", "int_nopd", "int_on_pd", "int_on_nopd",
        "rz_trips", "rz_td", "fg_n", "fg_resid", "one_score", "win",
    ]
    for col in value_cols:
        if col not in spine.columns:
            spine[col] = 0.0
        spine[col] = spine[col].fillna(0.0).astype(float)

    # Opponent on the allowed merge can collide. Recover opponent from the spine build.
    if "opponent" not in spine.columns:
        raise RuntimeError("team-game spine lost opponent")

    windowed = _windows(spine, value_cols)
    for name, num, den in _SUM_PAIRS:
        windowed[f"{name}_season"] = _rate(windowed[f"{num}_asof"], windowed[f"{den}_asof"])
        windowed[f"{name}_last4"] = _rate(windowed[f"{num}_last4"], windowed[f"{den}_last4"])
    windowed["n_plays_season"] = windowed["epa_den_asof"]
    windowed["n_plays_last4"] = windowed["epa_den_last4"]

    teams = sorted(set(windowed["team"]).union(windowed["opponent"]))
    ti = {team: i for i, team in enumerate(teams)}
    ridge_specs = (
        ("pass", "pass_epa_num", "pass_den", K_PASS),
        ("rush", "rush_epa_num", "rush_den", K_RUSH),
        ("sr", "sr_num", "epa_den", K_SUCCESS),
        ("xpl", "xpl_num", "epa_den", K_EXPLOSIVE),
        ("epa", "epa_num", "epa_den", K_EPA),
    )
    panels = {
        name: _ridge_panel(windowed, teams, num, den, k)
        for name, num, den, k in ridge_specs
    }
    for name, _, _, _ in ridge_specs:
        off_col = f"{name}_off_adj"
        def_col = f"{name}_def_adj"
        windowed[off_col] = [
            _lookup_rating(panels[name], int(s), int(w), ti[t], 0)
            for s, w, t in zip(windowed["season"], windowed["week"], windowed["team"])
        ]
        windowed[def_col] = [
            _lookup_rating(panels[name], int(s), int(w), ti[t], 1)
            for s, w, t in zip(windowed["season"], windowed["week"], windowed["team"])
        ]
    windowed["sched_strength"] = windowed["epa_off_adj"] - DEF_WEIGHT * windowed["epa_def_adj"]

    # Luck baselines: leave this team out of weeks strictly before the game.
    week_league = windowed.groupby(["season", "week"], sort=False)[
        ["int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"]
    ].sum().reset_index().sort_values(["season", "week"])
    prior_rows = []
    for season, group in week_league.groupby("season", sort=False):
        cum = group[["int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"]].cumsum()
        prior = cum.shift(1).fillna(0.0)
        prior.insert(0, "week", group["week"].to_numpy())
        prior.insert(0, "season", int(season))
        prior_rows.append(prior)
    league_prior = pd.concat(prior_rows, ignore_index=True) if prior_rows else pd.DataFrame()
    if len(league_prior):
        windowed = windowed.merge(league_prior, on=["season", "week"], how="left", suffixes=("", "_league_prior"))
    else:
        for col in ("int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"):
            windowed[f"{col}_league_prior"] = 0.0
    for col in ("int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"):
        league_col = f"{col}_league_prior" if f"{col}_league_prior" in windowed.columns else col
        # merge suffix: the league prior columns were not suffixed if names differed.
        if f"{col}_league_prior" not in windowed.columns:
            # cumsum frame used the same names and merge added _league? We inserted season/week
            # and the measure names, so merge suffixes the right side when they collide.
            pass
    # The merge above collides on the measure names. Pandas suffixes the right frame.
    rename_map = {}
    for col in ("int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"):
        right = f"{col}_y" if f"{col}_y" in windowed.columns else f"{col}_league_prior"
        if right in windowed.columns:
            rename_map[right] = f"loo_base_{col}"
        elif col in league_prior.columns and f"{col}_league_prior" in windowed.columns:
            rename_map[f"{col}_league_prior"] = f"loo_base_{col}"
    if rename_map:
        windowed = windowed.rename(columns=rename_map)
    for col in ("int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"):
        base = f"loo_base_{col}"
        if base not in windowed.columns:
            windowed[base] = 0.0
        windowed[base] = windowed[base].fillna(0.0)

    # Season totals must sum the per-game increments. The luck block below
    # reuses fumble_n / fg_n / int_n as the as-of window sizes on the team-game rows.
    season_base = windowed.copy()
    luck_rows = []
    for rec in windowed.itertuples(index=False):
        luck_rows.append(_luck_from_counts(
            fum_n=rec.fum_n_asof, fum_rec=rec.fum_rec_asof,
            int_att=rec.int_att_asof, int_pd=rec.int_pd_asof, int_nopd=rec.int_nopd_asof,
            int_on_pd=rec.int_on_pd_asof, int_on_nopd=rec.int_on_nopd_asof,
            loo_pd=rec.loo_base_int_pd - rec.int_pd_asof,
            loo_nopd=rec.loo_base_int_nopd - rec.int_nopd_asof,
            loo_int_pd=rec.loo_base_int_on_pd - rec.int_on_pd_asof,
            loo_int_nopd=rec.loo_base_int_on_nopd - rec.int_on_nopd_asof,
            rz_n=rec.rz_trips_asof, rz_td=rec.rz_td_asof,
            loo_rz_n=rec.loo_base_rz_trips - rec.rz_trips_asof,
            loo_rz_td=rec.loo_base_rz_td - rec.rz_td_asof,
            one_n=rec.one_score_asof, wins=rec.win_asof,
            fg_n=rec.fg_n_asof, fg_resid=rec.fg_resid_asof,
        ))
    luck_df = pd.DataFrame(luck_rows, index=windowed.index)
    for col in luck_df.columns:
        windowed[col] = luck_df[col].to_numpy()

    pct_source = [f"{name}_season" for name in PCT_METRICS if name != "sched_strength"]
    pct_source += [f"{name}_last4" for name in PCT_METRICS if name != "sched_strength"]
    pct_source += ["sched_strength"]
    # Strength has one column, not a last-4 twin. Percentile it once per window by
    # also ranking the same as-of strength under both names' callers below.
    _assign_percentiles(windowed, [f"{name}_season" for name in PCT_METRICS if name != "sched_strength"], ["season", "week"])
    _assign_percentiles(windowed, [f"{name}_last4" for name in PCT_METRICS if name != "sched_strength"], ["season", "week"])
    _assign_percentiles(windowed, ["sched_strength"], ["season", "week"])
    windowed["sched_strength_season_pct"] = windowed["sched_strength_pct"]
    windowed["sched_strength_last4_pct"] = windowed["sched_strength_pct"]

    team_game = _cast(windowed, TEAM_GAME_COLUMNS)

    season = _season_frame(season_base, panels, teams, ti)
    assert_no_market_columns(team_game.columns)
    assert_no_market_columns(season.columns)
    assert_output_columns(team_game.columns, TEAM_GAME_COLUMNS)
    assert_output_columns(season.columns, TEAM_SEASON_COLUMNS)
    return team_game, season


def _season_frame(
    windowed: pd.DataFrame,
    panels: dict,
    teams: list[str],
    ti: dict[str, int],
) -> pd.DataFrame:
    """Full-season rates, last four games of the season, end-of-season ridge, season luck."""
    rows = []
    ordered = windowed.sort_values(["season", "team", "week", "game_id"])
    sum_cols = [num for _, num, _ in _SUM_PAIRS] + [den for _, _, den in _SUM_PAIRS]
    sum_cols += [
        "fum_n", "fum_rec", "int_att", "int_pd", "int_nopd", "int_on_pd", "int_on_nopd",
        "rz_trips", "rz_td", "fg_n", "fg_resid", "one_score", "win", "epa_den",
    ]
    sum_cols = list(dict.fromkeys(sum_cols))
    for (season, team), group in ordered.groupby(["season", "team"], sort=False):
        full = group[sum_cols].sum()
        last = group.tail(LAST_N)[sum_cols].sum()
        row: dict[str, object] = {
            "season": int(season),
            "team": team,
            "games": int(len(group)),
            "n_plays_season": full["epa_den"],
            "n_plays_last4": last["epa_den"],
        }
        for name, num, den in _SUM_PAIRS:
            row[f"{name}_season"] = (full[num] / full[den]) if full[den] > 0 else np.nan
            row[f"{name}_last4"] = (last[num] / last[den]) if last[den] > 0 else np.nan
        for name in ("pass", "rush", "sr", "xpl", "epa"):
            off, de = panels[name].get((int(season), None), (np.zeros(len(teams)), np.zeros(len(teams))))
            row[f"{name}_off_adj"] = float(off[ti[team]])
            row[f"{name}_def_adj"] = float(de[ti[team]])
        row["sched_strength"] = float(row["epa_off_adj"]) - DEF_WEIGHT * float(row["epa_def_adj"])
        rows.append((row, full))
    if not rows:
        return _empty_season()
    frame = pd.DataFrame([row for row, _ in rows])
    # Season luck uses the full-season leave-one-team-out league.
    league = ordered.groupby("season", sort=False)[
        ["int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td"]
    ].sum()
    luck_rows = []
    for row, full in rows:
        season = int(row["season"])
        base = league.loc[season] if season in league.index else None
        if base is None:
            loo = {k: 0.0 for k in ("int_pd", "int_nopd", "int_on_pd", "int_on_nopd", "rz_trips", "rz_td")}
        else:
            loo = {k: float(base[k]) - float(full[k]) for k in base.index}
        luck_rows.append(_luck_from_counts(
            fum_n=full["fum_n"], fum_rec=full["fum_rec"],
            int_att=full["int_att"], int_pd=full["int_pd"], int_nopd=full["int_nopd"],
            int_on_pd=full["int_on_pd"], int_on_nopd=full["int_on_nopd"],
            loo_pd=loo["int_pd"], loo_nopd=loo["int_nopd"],
            loo_int_pd=loo["int_on_pd"], loo_int_nopd=loo["int_on_nopd"],
            rz_n=full["rz_trips"], rz_td=full["rz_td"],
            loo_rz_n=loo["rz_trips"], loo_rz_td=loo["rz_td"],
            one_n=full["one_score"], wins=full["win"],
            fg_n=full["fg_n"], fg_resid=full["fg_resid"],
        ))
    luck_df = pd.DataFrame(luck_rows)
    for col in luck_df.columns:
        frame[col] = luck_df[col].to_numpy()
    _assign_percentiles(frame, [f"{name}_season" for name in PCT_METRICS if name != "sched_strength"], ["season"])
    _assign_percentiles(frame, [f"{name}_last4" for name in PCT_METRICS if name != "sched_strength"], ["season"])
    _assign_percentiles(frame, ["sched_strength"], ["season"])
    frame["sched_strength_season_pct"] = frame["sched_strength_pct"]
    frame["sched_strength_last4_pct"] = frame["sched_strength_pct"]
    return _cast(frame, TEAM_SEASON_COLUMNS)
