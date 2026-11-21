"""Allocate a frozen sim's team totals to players.

Shares and efficiencies come from games that have already kicked off.
Player totals in each draw sum to that draw's team totals. No market
column is read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

QUANTILE_LEVELS = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)
POSITIONS = ("QB", "RB", "WR", "TE")
# Stat lines that player-opp-v1 publishes, plus the touchdown counts the
# sim can reconcile. Touchdowns are not a player-opp-v1 quantile stat.
STAT_LINES = {
    "QB": ("attempts", "completions", "passing_yards", "passing_tds"),
    "RB": (
        "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds",
    ),
    "WR": ("targets", "receptions", "receiving_yards", "receiving_tds"),
    "TE": ("targets", "receptions", "receiving_yards", "receiving_tds"),
}
SHARE_HALF_LIFE = {"targets": 4.0, "carries": 2.0, "attempts": 3.0}


def rows_before_kickoff(frame: pd.DataFrame, cutoff) -> pd.DataFrame:
    """Player or team rows strictly before ``cutoff``."""
    kicks = pd.to_datetime(frame["kickoff_et"], utc=True)
    limit = pd.Timestamp(cutoff)
    if limit.tzinfo is None:
        limit = limit.tz_localize("UTC")
    else:
        limit = limit.tz_convert("UTC")
    return frame.loc[kicks < limit].copy()


def largest_remainder(weights: np.ndarray, total: np.ndarray) -> np.ndarray:
    """Integer allocation. Each row sums to ``total`` exactly."""
    weights = np.asarray(weights, dtype=float)
    total = np.asarray(total, dtype=int)
    if weights.ndim == 1:
        weights = np.broadcast_to(weights, (len(total), weights.shape[0])).copy()
    else:
        weights = weights.copy()
    weights = np.clip(weights, 0.0, None)
    denom = weights.sum(axis=1, keepdims=True)
    empty = denom[:, 0] <= 0
    weights = np.divide(weights, np.where(denom > 0, denom, 1.0))
    raw = weights * total[:, None]
    base = np.floor(raw).astype(int)
    frac = np.where(empty[:, None], -1.0, raw - base)
    short = total - base.sum(axis=1)
    short = np.where(empty, 0, short)
    order = np.argsort(-frac, axis=1)
    n, k = base.shape
    cols = np.arange(k)[None, :]
    give = cols < short[:, None]
    base[np.arange(n)[:, None], order] += give.astype(int)
    return base


def _ewma_last(values: np.ndarray, halflife: float) -> float:
    if len(values) == 0:
        return 0.0
    series = pd.Series(values)
    return float(series.ewm(halflife=halflife, min_periods=1).mean().iloc[-1])


def player_shares(history: pd.DataFrame, player_ids: list[str]) -> pd.DataFrame:
    """Pre-week share and efficiency for each player. ``history`` is already before kickoff."""
    rows = []
    if history is None or len(history) == 0:
        return pd.DataFrame()
    hist = history.sort_values(["kickoff_et", "player_id"])
    for pid in player_ids:
        part = hist[hist["player_id"].astype(str).eq(str(pid))]
        if len(part) == 0:
            continue
        team_tgt = part["team_targets"].to_numpy(dtype=float)
        team_car = part["team_carries"].to_numpy(dtype=float)
        team_att = part["team_attempts"].to_numpy(dtype=float)
        tgt_sh = np.divide(part["targets"], np.clip(team_tgt, 1, None))
        car_sh = np.divide(part["carries"], np.clip(team_car, 1, None))
        att_sh = np.divide(part["attempts"], np.clip(team_att, 1, None))
        last = part.tail(24)
        tg = float(last["targets"].sum())
        car = float(last["carries"].sum())
        att = float(last["attempts"].sum())
        rows.append({
            "player_id": str(pid),
            "position": str(part["position"].iloc[-1]),
            "games": len(part),
            "tgt_sh": _ewma_last(tgt_sh.to_numpy(dtype=float), SHARE_HALF_LIFE["targets"]),
            "car_sh": _ewma_last(car_sh.to_numpy(dtype=float), SHARE_HALF_LIFE["carries"]),
            "att_sh": _ewma_last(att_sh.to_numpy(dtype=float), SHARE_HALF_LIFE["attempts"]),
            "ypt": float(last["receiving_yards"].sum()) / tg if tg else 7.6,
            "cr": float(last["receptions"].sum()) / tg if tg else 0.65,
            "ypc": float(last["rushing_yards"].sum()) / car if car else 4.3,
            "ypa": float(last["passing_yards"].sum()) / att if att else 6.9,
            "comp": float(last["completions"].sum()) / att if att else 0.64,
            "ptd": float(last["passing_tds"].sum()) / att if att else 0.04,
            "rutd": float(last["rushing_tds"].sum()) / car if car else 0.03,
            "retd": float(last["receiving_tds"].sum()) / tg if tg else 0.03,
        })
    return pd.DataFrame(rows)


def team_rates(history: pd.DataFrame, team: str) -> dict[str, float]:
    """Pre-week pass rate and sack rate for one team."""
    part = history[history["team"].astype(str).eq(str(team))]
    if len(part) == 0:
        return {"pass_rate": 0.58, "sack_rate": 0.06}
    weeks = part.groupby(["season", "week"], sort=True)[["team_attempts", "team_carries", "team_sacks"]].first()
    att = weeks["team_attempts"].to_numpy(dtype=float)
    car = weeks["team_carries"].to_numpy(dtype=float)
    sacks = weeks["team_sacks"].to_numpy(dtype=float)
    volume = np.clip(att + car, 1, None)
    return {
        "pass_rate": _ewma_last(att / volume, 6.0),
        "sack_rate": _ewma_last(sacks / np.clip(att, 1, None), 6.0),
    }


def _within_cap(raw: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """Integers that respect ``cap`` and sum to the rounded row total."""
    raw = np.clip(np.asarray(raw, dtype=float), 0.0, None)
    cap = np.maximum(np.asarray(cap, dtype=int), 0)
    raw = np.where(cap > 0, raw, 0.0)
    total = np.minimum(np.rint(raw.sum(axis=1)).astype(int), cap.sum(axis=1))
    alloc = np.minimum(np.floor(raw).astype(int), cap)
    n, k = alloc.shape
    rows = np.arange(n)
    for _ in range(k + 1):
        short = total - alloc.sum(axis=1)
        if int(short.max()) == 0:
            break
        room = cap - alloc
        score = np.where(room > 0, raw - alloc, -1.0)
        pick = np.argmax(score, axis=1)
        can = short > 0
        alloc[rows[can], pick[can]] += 1
    return alloc


def allocate_team(
    plays: np.ndarray,
    roster: pd.DataFrame,
    rates: dict[str, float],
) -> dict[str, dict[str, np.ndarray]]:
    """Split one team's simulated plays. Player sums equal the team totals."""
    plays = np.asarray(plays, dtype=int)
    n = len(plays)
    if roster is None or len(roster) == 0 or n == 0:
        return {}
    roster = roster.reset_index(drop=True)
    pass_rate = float(np.clip(rates.get("pass_rate", 0.58), 0.05, 0.95))
    sack_rate = float(np.clip(rates.get("sack_rate", 0.06), 0.0, 0.20))
    attempts = np.clip(np.rint(plays * pass_rate).astype(int), 0, plays)
    carries = plays - attempts
    sacks = np.clip(np.rint(attempts * sack_rate).astype(int), 0, attempts)
    targets = attempts - sacks
    ids = roster["player_id"].astype(str).tolist()
    pos = roster["position"].astype(str).to_numpy()
    att = largest_remainder(np.where(pos == "QB", roster["att_sh"].to_numpy(dtype=float), 0.0), attempts)
    car = largest_remainder(np.where(pos == "RB", roster["car_sh"].to_numpy(dtype=float), 0.0), carries)
    skill = np.isin(pos, ["RB", "WR", "TE"])
    tgt = largest_remainder(np.where(skill, roster["tgt_sh"].to_numpy(dtype=float), 0.0), targets)
    comp_rate = np.clip(roster["comp"].to_numpy(dtype=float), 0.0, 1.0)
    catch_rate = np.clip(roster["cr"].to_numpy(dtype=float), 0.0, 1.0)
    completions = _within_cap(att * comp_rate, att)
    receptions = _within_cap(tgt * catch_rate, tgt)
    pass_yd = att * np.clip(roster["ypa"].to_numpy(dtype=float), 0.0, None)
    rush_yd = car * np.clip(roster["ypc"].to_numpy(dtype=float), 0.0, None)
    rec_yd = tgt * np.clip(roster["ypt"].to_numpy(dtype=float), 0.0, None)
    ptd_rate = np.clip(roster["ptd"].to_numpy(dtype=float), 0.0, None)
    rutd_rate = np.clip(roster["rutd"].to_numpy(dtype=float), 0.0, None)
    retd_rate = np.clip(roster["retd"].to_numpy(dtype=float), 0.0, None)
    ptd = _within_cap(att * ptd_rate, att)
    rutd = _within_cap(car * rutd_rate, car)
    retd = _within_cap(tgt * retd_rate, tgt)
    columns = {
        "attempts": att,
        "carries": car,
        "targets": tgt,
        "completions": completions,
        "receptions": receptions,
        "passing_yards": pass_yd,
        "rushing_yards": rush_yd,
        "receiving_yards": rec_yd,
        "passing_tds": ptd,
        "rushing_tds": rutd,
        "receiving_tds": retd,
    }
    packed: dict[str, dict[str, np.ndarray]] = {}
    for index, pid in enumerate(ids):
        packed[pid] = {name: values[:, index] for name, values in columns.items()}
    packed["_team"] = {name: values.sum(axis=1) for name, values in columns.items()}
    packed["_team"]["plays"] = plays
    packed["_team"]["attempts_from_plays"] = attempts
    packed["_team"]["carries_from_plays"] = carries
    packed["_team"]["targets_from_plays"] = targets
    return packed


def summarize_player(draws: dict[str, np.ndarray]) -> dict[str, float]:
    """Mean and the player-opp-v1 quantile grid for one player's draws."""
    out: dict[str, float] = {}
    for stat, values in draws.items():
        sample = np.asarray(values, dtype=float)
        out[f"{stat}_mean"] = float(sample.mean())
        qs = np.quantile(sample, QUANTILE_LEVELS)
        for level, value in zip(QUANTILE_LEVELS, qs, strict=True):
            out[f"{stat}_q{round(level * 100):02d}"] = float(value)
    return out
