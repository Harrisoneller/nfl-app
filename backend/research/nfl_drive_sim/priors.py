"""Pre-game rolling estimates. Every lookup is strictly before kickoff."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .models import exp_weights, shrink, weighted_mean
from .pregame import rows_strictly_before

RATE_COLUMNS = (
    "sec_per_play",
    "plays_per_drive",
    "plays",
    "drives",
    "no_huddle",
    "incomplete",
    "pass_rate",
    "epa",
    "success",
    "expl_pass",
    "expl_run",
    "early_pass",
    "sack",
    "pressure",
    "ypp_pass",
    "ypp_run",
    "td_rate",
    "fg_rate",
    "punt_rate",
    "to_rate",
    "sec_neutral",
    "sec_leading",
    "sec_trailing",
    "sec_two_minute",
    "ppd_neutral",
    "ppd_leading",
    "ppd_trailing",
    "ppd_two_minute",
)


def prior_slice(frame: pd.DataFrame, kickoff: object, *, game_id: str | None = None) -> pd.DataFrame:
    """Rows whose kickoff is strictly earlier. The target game id is removed too."""
    kept = rows_strictly_before(frame, kickoff, kickoff_column="kickoff_et")
    if game_id is not None and len(kept):
        kept = kept[kept["game_id"].astype(str) != str(game_id)]
    return kept


def _rate(history: pd.DataFrame, column: str, league: float) -> float:
    if history is None or len(history) == 0 or column not in history.columns:
        return league
    values = history[column].to_numpy(dtype=float)
    weights = exp_weights(len(history), cfg.HALF_LIFE_GAMES)
    estimate, n_eff = weighted_mean(values, weights)
    return shrink(estimate, n_eff, league, cfg.SHRINK_K)


def league_means(history: pd.DataFrame) -> dict[str, float]:
    means = {}
    for column in RATE_COLUMNS:
        if history is None or len(history) == 0 or column not in history.columns:
            means[column] = float("nan")
            continue
        values = pd.to_numeric(history[column], errors="coerce").to_numpy(dtype=float)
        ok = values[np.isfinite(values)]
        means[column] = float(ok.mean()) if ok.size else float("nan")
    return means


def team_prior(history: pd.DataFrame, team: str, league: dict[str, float]) -> dict[str, float]:
    """Exponentially weighted, shrunk offense and defense rates for one team."""
    out = {}
    offense = history[history["team"].astype(str) == str(team)] if len(history) else history
    defense = history[history["opponent"].astype(str) == str(team)] if len(history) else history
    for column in RATE_COLUMNS:
        base = league.get(column, float("nan"))
        out[f"off_{column}"] = _rate(offense, column, base)
        out[f"def_{column}"] = _rate(defense, column, base)
    return out


def equal_weight_baseline(history: pd.DataFrame, team: str, column: str, league: float) -> float:
    """Naive baseline: equal-weight team mean shrunk to the league. No opponent."""
    if len(history) == 0:
        return league
    values = pd.to_numeric(
        history.loc[history["team"].astype(str) == str(team), column], errors="coerce",
    ).to_numpy(dtype=float)
    ok = values[np.isfinite(values)]
    if ok.size == 0:
        return league
    return shrink(float(ok.mean()), float(ok.size), league, cfg.SHRINK_K)


def _finite_mean(values: np.ndarray) -> float:
    ok = values[np.isfinite(values)]
    return float(ok.mean()) if ok.size else float("nan")


def _ewma(values: np.ndarray, league: float) -> float:
    """Same shrink as ``_rate``: last value is the most recent game."""
    if values.size == 0:
        return float(league) if np.isfinite(league) else 0.0
    estimate, n_eff = weighted_mean(values, exp_weights(values.size, cfg.HALF_LIFE_GAMES))
    return shrink(estimate, n_eff, league, cfg.SHRINK_K)


def _equal(values: np.ndarray, league: float) -> float:
    ok = values[np.isfinite(values)]
    if ok.size == 0:
        return float(league) if np.isfinite(league) else 0.0
    return shrink(float(ok.mean()), float(ok.size), league, cfg.SHRINK_K)


def with_priors(team_games: pd.DataFrame, qb_games: pd.DataFrame) -> pd.DataFrame:
    """Attach pre-game rates. A row never sees its own kickoff or a later one.

    Rows are sorted by kickoff, so the history of row i is the prefix of
    strictly earlier kickoffs. That is the same cut as ``prior_slice``.
    """
    if team_games is None or len(team_games) == 0:
        return team_games
    ordered = team_games.sort_values(["kickoff_et", "game_id", "team"]).reset_index(drop=True)
    kick_ns = pd.to_datetime(ordered["kickoff_et"], utc=True).astype("int64").to_numpy()
    ends = np.searchsorted(kick_ns, kick_ns, side="left")
    teams = ordered["team"].astype(str).to_numpy()
    opponents = ordered["opponent"].astype(str).to_numpy()
    columns = [column for column in RATE_COLUMNS if column in ordered.columns]
    values = {
        column: pd.to_numeric(ordered[column], errors="coerce").to_numpy(dtype=float)
        for column in columns
    }
    qb_ns, qb_ids, qb_epa, qb_cpoe, qb_drops = _qb_arrays(qb_games)
    qb_codes = ordered["qb_id"].astype(str).to_numpy() if "qb_id" in ordered.columns else np.array(["nan"] * len(ordered))
    attached = []
    for index in range(len(ordered)):
        end = int(ends[index])
        league = {column: _finite_mean(values[column][:end]) for column in columns}
        team = teams[index]
        off = teams[:end] == team
        deff = opponents[:end] == team
        prior = {}
        for column in columns:
            base = league[column]
            prior[f"off_{column}"] = _ewma(values[column][:end][off], base)
            prior[f"def_{column}"] = _ewma(values[column][:end][deff], base)
        for column in RATE_COLUMNS:
            prior.setdefault(f"off_{column}", 0.0)
            prior.setdefault(f"def_{column}", 0.0)
        league_epa = _finite_mean(qb_epa[: np.searchsorted(qb_ns, kick_ns[index], side="left")]) if qb_ns.size else float("nan")
        league_cpoe = _finite_mean(qb_cpoe[: np.searchsorted(qb_ns, kick_ns[index], side="left")]) if qb_ns.size else float("nan")
        prior["qb_epa"], prior["qb_cpoe"] = _qb_lookup(
            qb_ns, qb_ids, qb_epa, qb_cpoe, qb_drops, kick_ns[index], qb_codes[index], league_epa, league_cpoe,
        )
        play_league = league.get("plays", float("nan"))
        drive_league = league.get("drives", float("nan"))
        sec_league = league.get("sec_per_play", float("nan"))
        ppd_league = league.get("plays_per_drive", float("nan"))
        prior["base_plays"] = _equal(values["plays"][:end][off], play_league) if "plays" in values else 0.0
        prior["base_drives"] = _equal(values["drives"][:end][off], drive_league) if "drives" in values else 0.0
        prior["base_sec"] = _equal(values["sec_per_play"][:end][off], sec_league) if "sec_per_play" in values else 25.0
        prior["base_ppd"] = _equal(values["plays_per_drive"][:end][off], ppd_league) if "plays_per_drive" in values else 6.0
        attached.append(prior)
    return pd.concat([ordered, pd.DataFrame(attached)], axis=1)


def _qb_arrays(qb_games: pd.DataFrame):
    empty = np.zeros(0)
    if qb_games is None or len(qb_games) == 0 or "kickoff_et" not in qb_games.columns:
        return empty, empty.astype(str), empty, empty, empty
    ordered = qb_games.sort_values(["kickoff_et", "game_id", "qb_id"]).reset_index(drop=True)
    return (
        pd.to_datetime(ordered["kickoff_et"], utc=True).astype("int64").to_numpy(),
        ordered["qb_id"].astype(str).to_numpy(),
        pd.to_numeric(ordered["epa"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(ordered["cpoe"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(ordered["dropbacks"], errors="coerce").fillna(0).to_numpy(dtype=float),
    )


def _qb_lookup(qb_ns, qb_ids, qb_epa, qb_cpoe, qb_drops, kick_ns, qb_id, league_epa, league_cpoe):
    """Dropback EPA and CPOE shrunk to the prior league mean for that quarterback."""
    if qb_ns.size == 0 or qb_id in {"nan", "None", ""}:
        epa = float(league_epa) if np.isfinite(league_epa) else 0.0
        cpoe = float(league_cpoe) if np.isfinite(league_cpoe) else 0.0
        return epa, cpoe
    end = int(np.searchsorted(qb_ns, kick_ns, side="left"))
    mask = qb_ids[:end] == qb_id
    if not np.any(mask):
        epa = float(league_epa) if np.isfinite(league_epa) else 0.0
        cpoe = float(league_cpoe) if np.isfinite(league_cpoe) else 0.0
        return epa, cpoe
    weights = exp_weights(int(mask.sum()), cfg.HALF_LIFE_GAMES) * np.maximum(qb_drops[:end][mask], 1.0)
    epa, n_eff = weighted_mean(qb_epa[:end][mask], weights)
    cpoe, _ = weighted_mean(qb_cpoe[:end][mask], weights)
    scale = float(np.nansum(qb_drops[:end][mask])) / max(cfg.QB_SHRINK_DROPBACKS, 1.0)
    return (
        shrink(epa, n_eff * scale, 0.0 if not np.isfinite(league_epa) else float(league_epa), 1.0),
        shrink(cpoe, n_eff * scale, 0.0 if not np.isfinite(league_cpoe) else float(league_cpoe), 1.0),
    )


def qb_prior(qb_history: pd.DataFrame, qb_id: str | None, league_epa: float, league_cpoe: float) -> tuple[float, float]:
    """Shrunk dropback EPA and CPOE for the pre-game quarterback id."""
    if qb_id is None or qb_history is None or len(qb_history) == 0:
        return league_epa, league_cpoe
    rows = qb_history[qb_history["qb_id"].astype(str) == str(qb_id)]
    if "kickoff_et" in rows.columns:
        rows = rows.sort_values("kickoff_et")
    if len(rows) == 0:
        return league_epa, league_cpoe
    weights = exp_weights(len(rows), cfg.HALF_LIFE_GAMES)
    dropbacks = pd.to_numeric(rows["dropbacks"], errors="coerce").fillna(0).to_numpy(dtype=float)
    weights = weights * np.maximum(dropbacks, 1.0)
    epa, n_eff = weighted_mean(rows["epa"].to_numpy(dtype=float), weights)
    cpoe, _ = weighted_mean(rows["cpoe"].to_numpy(dtype=float), weights)
    n_drop = float(np.nansum(dropbacks))
    scale = n_drop / max(cfg.QB_SHRINK_DROPBACKS, 1.0)
    return (
        shrink(epa, n_eff * scale, league_epa, 1.0),
        shrink(cpoe, n_eff * scale, league_cpoe, 1.0),
    )
