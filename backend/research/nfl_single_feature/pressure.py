"""Pass-rush versus pass-protection interaction.

The only column this module emits for the model is ``pressure_x``:

    (offense residual_A * defense effect_B) - (offense residual_B * defense effect_A)

A is the home team when the matchup row is a game. The expected stage-2 sign
on home margin is negative. Main effects are not returned.

Inputs are caller-supplied frames. This module does not fetch PFR, NGS, or
play-by-play.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .design import DesignError
from .specs import (
    PRESSURE_FIRST_SEASON,
    PRESSURE_K_GAMES,
    PRESSURE_WP_HI,
    PRESSURE_WP_LO,
    REGIME_LAST,
)

_TEAM_GAME_COLUMNS = (
    "season", "week", "game_id", "team", "opponent",
    "off_pressures", "off_dropbacks", "qb_att",
)


def filter_dropbacks(plays: pd.DataFrame) -> pd.DataFrame:
    """Keep dropbacks inside the win-probability band. Drop kneels and spikes.

    Inclusion is ``qb_dropback == 1`` (pass attempt, sack, or scramble).
    Missing win probability is out. Seasons before the PFR era are out.
    Seasons through 2012 are out.
    """
    required = {"qb_dropback", "wp", "qb_kneel", "qb_spike", "season"}
    missing = required - set(plays.columns)
    if missing:
        raise DesignError(f"dropback filter missing columns: {sorted(missing)}")
    kept = plays.copy()
    mask = kept["qb_dropback"].fillna(0).astype(float).eq(1.0)
    mask &= kept["wp"].between(PRESSURE_WP_LO, PRESSURE_WP_HI, inclusive="both")
    mask &= kept["qb_kneel"].fillna(0).astype(float).ne(1.0)
    mask &= kept["qb_spike"].fillna(0).astype(float).ne(1.0)
    mask &= kept["season"].astype(int).ge(PRESSURE_FIRST_SEASON)
    mask &= kept["season"].astype(int).gt(REGIME_LAST)
    if "special_teams_play" in kept.columns:
        mask &= kept["special_teams_play"].fillna(0).astype(float).ne(1.0)
    return kept.loc[mask].copy()


def team_games_from_plays(plays: pd.DataFrame) -> pd.DataFrame:
    """Aggregate filtered dropbacks to one offense row per team-game.

    ``pressure`` is 1 when PFR charts a pressure on that dropback.
    ``qb_att`` is the quarterback's average time to throw entering that
    team-game. This function averages it within the team-game; it does not
    read any other time-to-throw column.
    """
    required = {"posteam", "defteam", "game_id", "season", "week", "pressure", "qb_att"}
    missing = required - set(plays.columns)
    if missing:
        raise DesignError(f"play aggregation missing columns: {sorted(missing)}")
    kept = filter_dropbacks(plays)
    if len(kept) == 0:
        return pd.DataFrame(columns=list(_TEAM_GAME_COLUMNS))
    kept = kept.copy()
    kept["pressure"] = kept["pressure"].fillna(0).astype(float)
    grouped = kept.groupby(["season", "week", "game_id", "posteam", "defteam"], as_index=False).agg(
        off_pressures=("pressure", "sum"),
        off_dropbacks=("pressure", "size"),
        qb_att=("qb_att", "mean"),
    )
    grouped = grouped.rename(columns={"posteam": "team", "defteam": "opponent"})
    return grouped[list(_TEAM_GAME_COLUMNS)]


def _with_rates(team_games: pd.DataFrame) -> pd.DataFrame:
    missing = [col for col in _TEAM_GAME_COLUMNS if col not in team_games.columns]
    if missing:
        raise DesignError(f"team-game pressure frame missing columns: {missing}")
    frame = team_games.copy()
    frame = frame[frame["season"].astype(int).gt(REGIME_LAST)]
    frame = frame[frame["season"].astype(int).ge(PRESSURE_FIRST_SEASON)]
    frame = frame[frame["off_dropbacks"].astype(float) > 0]
    if (frame["off_pressures"].astype(float) < 0).any() or (frame["off_dropbacks"].astype(float) < 0).any():
        raise DesignError("pressure counts cannot be negative")
    frame["rate"] = frame["off_pressures"].astype(float) / frame["off_dropbacks"].astype(float)
    frame["season"] = frame["season"].astype(int)
    frame["week"] = frame["week"].astype(int)
    return frame


def season_centers(team_games: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time season center of the raw pressure-allowed rate.

    The center for week ``w`` is the mean team-game rate in that season at
    weeks strictly before ``w``. Week 1 uses the previous season's mean rate.
    If neither exists, the center is missing and the row cannot enter the ridge.
    """
    frame = _with_rates(team_games)
    if len(frame) == 0:
        frame["center"] = pd.Series(dtype=float)
        frame["centered"] = pd.Series(dtype=float)
        return frame
    lookup: dict[tuple[int, int], float] = {}
    for season, week in frame.groupby(["season", "week"]).size().index:
        prior = frame[(frame["season"] == season) & (frame["week"] < week)]["rate"]
        if len(prior) == 0:
            previous = frame[frame["season"] == season - 1]["rate"]
            lookup[(int(season), int(week))] = float(previous.mean()) if len(previous) else np.nan
        else:
            lookup[(int(season), int(week))] = float(prior.mean())
    frame["center"] = [lookup[(int(s), int(w))] for s, w in zip(frame["season"], frame["week"])]
    frame["centered"] = frame["rate"] - frame["center"]
    return frame


def residualize_rate(centered: np.ndarray, qb_att: np.ndarray) -> tuple[np.ndarray, float, float]:
    """In-sample residual of centered pressure-allowed on average time to throw.

    Fit is ``centered ~ 1 + qb_att`` on the rows where both are finite. A
    zero-variance time-to-throw leaves the slope at 0 and subtracts the mean.
    """
    y = np.asarray(centered, dtype=float)
    x = np.asarray(qb_att, dtype=float)
    residual = np.full(len(y), np.nan)
    mask = np.isfinite(y) & np.isfinite(x)
    if int(mask.sum()) == 0:
        return residual, np.nan, np.nan
    if int(mask.sum()) < 2 or float(np.std(x[mask])) == 0.0:
        mu = float(np.mean(y[mask]))
        residual[mask] = y[mask] - mu
        return residual, mu, 0.0
    design = np.column_stack([np.ones(int(mask.sum())), x[mask]])
    coef, _, _, _ = np.linalg.lstsq(design, y[mask], rcond=None)
    residual[mask] = y[mask] - design @ coef
    return residual, float(coef[0]), float(coef[1])


def ridge_effects(
    history: pd.DataFrame,
    *,
    k_games: float = PRESSURE_K_GAMES,
) -> tuple[dict[str, float], dict[str, float]]:
    """Equal-weight ridge ``residual = ic + off_i + def_j`` with ``k_games`` pseudo-games.

    Only teams that appear in ``history`` are in the design. Effects are then
    centered across those teams. A team with no prior team-game is absent from
    the dict; the caller treats that as the zero prior.
    """
    names = sorted(set(history["team"]) | set(history["opponent"]))
    k = len(names)
    zero = {team: 0.0 for team in names}
    if k == 0 or len(history) == 0:
        return zero, zero
    index = {team: i for i, team in enumerate(names)}
    n = len(history)
    design = np.zeros((n, 2 * k + 1))
    rows = np.arange(n)
    design[rows, history["team"].map(index).to_numpy()] = 1.0
    design[rows, k + history["opponent"].map(index).to_numpy()] = 1.0
    design[:, -1] = 1.0
    penalty = np.full(2 * k + 1, float(k_games))
    penalty[-1] = 1e-8
    y = history["resid"].to_numpy(dtype=float)
    gram = design.T @ design + np.diag(penalty)
    coef = np.linalg.solve(gram, design.T @ y)
    off = coef[:k] - coef[:k].mean()
    deff = coef[k:2 * k] - coef[k:2 * k].mean()
    return (
        {team: float(off[i]) for i, team in enumerate(names)},
        {team: float(deff[i]) for i, team in enumerate(names)},
    )


def effects_asof(
    centered_games: pd.DataFrame,
    season: int,
    week: int,
    *,
    k_games: float = PRESSURE_K_GAMES,
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Opponent-adjusted effects using team-games strictly before ``(season, week)``."""
    hist = centered_games[
        (centered_games["season"] < season)
        | ((centered_games["season"] == season) & (centered_games["week"] < week))
    ].copy()
    hist = hist[np.isfinite(hist["centered"]) & np.isfinite(hist["qb_att"].astype(float))]
    if len(hist) == 0:
        return None
    residual, _, _ = residualize_rate(
        hist["centered"].to_numpy(dtype=float),
        hist["qb_att"].to_numpy(dtype=float),
    )
    hist["resid"] = residual
    hist = hist[np.isfinite(hist["resid"])]
    if len(hist) == 0:
        return None
    return ridge_effects(hist, k_games=k_games)


def centered_interaction(off_a: float, def_b: float, off_b: float, def_a: float) -> float:
    """Antisymmetric interaction. Positive when A is the more pressured side."""
    return float(off_a * def_b - off_b * def_a)


def pressure_matchup_feature(team_games: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """One ``pressure_x`` per game. History is prior weeks only. ``k`` is the locked 8.

    ``games`` columns: ``game_id, season, week, home, away``.
    The current game's row in ``team_games`` is not in its own history.
    """
    required = {"game_id", "season", "week", "home", "away"}
    missing = required - set(games.columns)
    if missing:
        raise DesignError(f"matchup frame missing columns: {sorted(missing)}")
    centered = season_centers(team_games)
    out_rows: list[dict[str, object]] = []
    cache: dict[tuple[int, int], tuple[dict[str, float], dict[str, float]] | None] = {}
    for game in games.itertuples(index=False):
        key = (int(game.season), int(game.week))
        if key not in cache:
            cache[key] = effects_asof(centered, key[0], key[1])
        effects = cache[key]
        if effects is None:
            value = np.nan
        else:
            off, deff = effects
            value = centered_interaction(
                off.get(game.home, 0.0),
                deff.get(game.away, 0.0),
                off.get(game.away, 0.0),
                deff.get(game.home, 0.0),
            )
        out_rows.append({
            "game_id": game.game_id,
            "season": int(game.season),
            "week": int(game.week),
            "pressure_x": value,
        })
    return pd.DataFrame(out_rows)
