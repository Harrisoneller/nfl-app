"""One real P3 run. QA cleared the runner at 475a90c.

A failure or a void stops the process. This file does not retune.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

import pandas as pd

from .p3_features import DERIVED, prepare_team_context
from .run_p2 import _card
from .run_p3 import (
    bootstrap_rng,
    load_game_set,
    merge_manifest,
    score_slate,
    webb_mean_interval,
    write_results,
)
from .simulate import simulate_game

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "outputs" / "p3" / "manifest.sha256"
DEPTH = ROOT / ".cache" / "depth_charts"
OUT = ROOT / "outputs" / "p3"


def hash_depth_charts() -> None:
    updates = []
    for path in sorted(DEPTH.glob("*.parquet")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        updates.append((digest, f".cache/depth_charts/{path.name}"))
    merge_manifest(MANIFEST, updates)
    print(f"hashed {len(updates)} depth charts", flush=True)


def _games_and_features(identified: pd.DataFrame, featured: pd.DataFrame):
    primary = load_game_set(OUT / "game_set.csv")
    history = pd.read_csv(OUT / "history_sets.csv")
    features = pd.read_parquet(DERIVED / "features_v5.parquet")
    labels = pd.concat([primary, history], ignore_index=True)
    labels["game_id"] = labels["game_id"].astype(str)
    labels["margin"] = labels["home_score"] - labels["away_score"]
    labels["total"] = labels["home_score"] + labels["away_score"]
    keep = ["game_id", "kickoff_et", "game_type", "margin", "total", "home_score", "away_score", "role"]
    features = features.drop(columns=[c for c in ("role",) if c in features.columns])
    features = features.merge(labels[keep], on="game_id", how="left")
    score_roles = {"primary", "sensitivity_2018", "playoff"}
    games = labels[labels["role"].isin(score_roles)].copy()
    qbs = identified.set_index("game_id")[["home_qb", "away_qb"]]
    games = games.merge(qbs, left_on="game_id", right_index=True, how="left")
    actual = []
    for game_id, part in featured.groupby("game_id", sort=False):
        plays = part["plays"].sum()
        seconds = (part["sec_per_play"] * part["plays"]).sum()
        actual.append((str(game_id), float(seconds / plays) if plays else float("nan")))
    games = games.merge(pd.DataFrame(actual, columns=["game_id", "actual_seconds_per_play"]), on="game_id", how="left")
    return games, features, featured


def _card_lookup(featured: pd.DataFrame):
    cache: dict[tuple, dict] = {}
    home = featured[featured["is_home"].eq(1.0)].set_index("game_id")
    away = featured[featured["is_home"].eq(0.0)].set_index("game_id")

    def card_for(series: pd.Series, ypp_on: bool) -> dict:
        key = (int(series["season"]), int(series["week"]), bool(ypp_on))
        if key not in cache:
            path = DERIVED / "cards" / f"{key[0]}_{key[1]}_{int(ypp_on)}.pkl"
            cache[key] = pickle.loads(path.read_bytes())
        packed = cache[key]
        gid = str(series["game_id"])
        return _card(
            home.loc[gid], away.loc[gid], packed["pace"], packed["ypp"], packed["drive_model"],
            packed["banks"], packed["rates"], ypp_on, packed["clock"], packed["lengths"], packed["late"],
            playoff=str(series["game_type"]) != "REG",
        )

    return card_for


def _notes(result: dict) -> list[str]:
    games = result["games"]
    kept = games[games["role"].eq("primary") & games["drop_reason"].isna() & games["ypp_on"].eq(result["ypp_keep"])]
    clusters = kept["season"].astype(str) + "-" + kept["week"].astype(str)
    interval = webb_mean_interval(kept["d_margin_crps"].to_numpy(), clusters.to_numpy(), bootstrap_rng())
    notes = []
    if interval is not None:
        mean, low, high = interval
        notes.append(f"Primary mean margin-CRPS d {mean:.6f}, 90% CI [{low:.6f}, {high:.6f}].")
        result["interval"] = interval
    sens = games[games["role"].isin(["primary", "sensitivity_2018"]) & games["drop_reason"].isna()]
    sens = sens[sens["ypp_on"].eq(result["ypp_keep"])]
    if len(sens):
        s_clusters = sens["season"].astype(str) + "-" + sens["week"].astype(str)
        s_iv = webb_mean_interval(sens["d_margin_crps"].to_numpy(), s_clusters.to_numpy(), bootstrap_rng())
        if s_iv is not None:
            notes.append(
                f"With-2018 sensitivity mean margin-CRPS d {s_iv[0]:.6f}, "
                f"90% CI [{s_iv[1]:.6f}, {s_iv[2]:.6f}]. It does not change the primary call."
            )
            result["sensitivity_2018"] = s_iv
    playoff = games[games["role"].eq("playoff") & games["drop_reason"].isna() & games["ypp_on"].eq(result["ypp_keep"])]
    if len(playoff):
        notes.append(
            f"Playoffs are descriptive only: {len(playoff)} games, "
            f"mean margin-CRPS d {float(playoff['d_margin_crps'].mean()):.6f}."
        )
    if len(kept) and "sim_p_margin_3" in kept.columns:
        actual_3 = float((kept["actual_margin"].abs() == 3).mean())
        sim_3 = float(kept["sim_p_margin_3"].mean())
        notes.append(
            f"Tracked margin-3 share: actual {actual_3:.3f}, sim {sim_3:.3f}, gap {sim_3 - actual_3:.3f}."
        )
        actual_7 = float((kept["actual_margin"].abs() == 7).mean())
        sim_7 = float(kept["sim_p_margin_7"].mean())
        notes.append(f"Tracked margin-7 share: actual {actual_7:.3f}, sim {sim_7:.3f}.")
    if len(kept) and "sec_per_play_bias" in kept.columns:
        notes.append(f"Tracked seconds-per-play bias: {float(kept['sec_per_play_bias'].mean()):.3f}.")
    if len(kept):
        notes.append(f"Tracked regulation-tie rate: {float(kept['actual_margin'].eq(0).mean()):.3f}.")
        notes.append(f"Tracked overtime-entry rate: {float(kept['sim_ot'].mean()):.3f}.")
        notes.append(f"Tracked margin at 5:00: {float(kept['margin_at_5min'].mean()):.3f}.")
    if len(kept) and "sim_margin_crps_sample" in kept.columns:
        gap_m = float((kept["sim_margin_crps_sample"] - kept["v5_margin_crps_normal"]).mean())
        gap_t = float((kept["sim_total_crps_sample"] - kept["v5_total_crps_normal"]).mean())
        notes.append(
            f"Continuous-CRPS sensitivity, sim sample minus v5 normal: "
            f"margin {gap_m:.3f}, total {gap_t:.3f}. It does not change the call."
        )
    notes.append(
        "Per-game sim context is the hashed week card pickle plus the pre-game "
        "team row. It is reproducible from those pickles, the derived play extract, "
        "and the hashed depth charts. It is not a separate fitted artifact."
    )
    notes.append(f"Yards-per-play layer kept: {result['ypp_keep']}.")
    return notes


def main() -> dict:
    hash_depth_charts()
    featured, identified = prepare_team_context()
    games, features, featured = _games_and_features(identified, featured)
    print(f"scoring {len(games)} games, features {len(features)}", flush=True)
    seen = {"n": 0}

    def simulate(card, n_sims, rng):
        seen["n"] += 1
        if seen["n"] % 25 == 1:
            print(f"sim {seen['n']}", flush=True)
        return simulate_game(card, n_sims, rng)

    result = score_slate(games, features, simulate, card_for=_card_lookup(featured))
    result["notes"] = [] if result["decision"] == "VOID" else _notes(result)
    if result["decision"] == "VOID":
        result["notes"] = [(
            f"VOID. Primary games {result['n_primary']}, dropped {result['n_dropped']}, "
            f"reasons {result['drops']}. No patch and no rerun."
        )]
    write_results(OUT, result)
    print(result["decision"], result["n_primary"], result["n_dropped"], result["drops"], flush=True)
    return result


if __name__ == "__main__":
    main()
