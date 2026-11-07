"""Walk-forward look for the market-blind NFL ML layer.

Do not run this until QA re-audits the lock commit and supplies its SHA.
Importing the module does not download data, fit a model, or score a game.
The final seasons are not scored unless the selection rule opens them.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.request

import numpy as np
import pandas as pd

from app.adapters.data.nflverse_stats import RELEASE
from app.services import team_ratings_v5 as v5
from app.services.ml_blind_features import (
    assert_prefit_coverage,
    build_market_blind_frame,
    team_game_epa,
)
from app.services.ml_blind_guard import RESULTS_PATH, assert_fit_allowed, write_results_once
from app.services.ml_blind_incumbent import STAGE2_COLUMNS, apply_stage2, refit_stage2_margin
from app.services.ml_blind_protocol import (
    cluster_se_of_mean,
    grade_predictions,
    projected_se,
    selection_opens_final,
)
from app.services.ml_blind_spec import (
    FINAL_SEASON_FIRST,
    GRADED_GAME_TYPE,
    HISTORY_SEASON,
    LOCKED_SHA_ENV,
    SELECTION_SEASONS,
    SIGMA,
    TRAIN_FIRST_SEASON,
    final_seasons,
)
from app.services.ml_predictions_service import fit_market_blind_models, predict_market_blind
from app.services.prediction_dist import crps_normal, win_prob
from app.utils.teams import canonical_team

PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team", "away_team",
    "epa", "play_type", "pass", "rush", "qb_kneel", "qb_spike", "wp", "qtr",
    "score_differential", "special_teams_play", "qb_dropback", "passer_player_id",
]
NO_MKT_COLUMNS = tuple(col for col in STAGE2_COLUMNS if col != "mkt_d")


def run_locked_eval() -> dict:
    """Selection first. Final seasons stay unopened when z or power fails."""
    snapshot = assert_fit_allowed(os.environ.get(LOCKED_SHA_ENV))
    sched, games_hash = _read_hashed_csv(f"{RELEASE}/schedules/games.csv")
    sched = sched[sched["season"] >= HISTORY_SEASON].copy()
    for col in ("home_team", "away_team"):
        sched[col] = sched[col].map(lambda v: canonical_team(v) if isinstance(v, str) else v)
    last = final_seasons()[-1]
    epa_frames: list[pd.DataFrame] = []
    v5_frames: list[pd.DataFrame] = []
    pbp_hashes: dict[str, str] = {}
    for season in range(HISTORY_SEASON, last + 1):
        raw, digest = _fetch(f"{RELEASE}/pbp/play_by_play_{season}.parquet")
        pbp_hashes[str(season)] = digest
        pbp = _parquet(raw)
        pbp["season"] = season
        for col in ("posteam", "defteam", "home_team", "away_team"):
            if col in pbp.columns:
                pbp[col] = pbp[col].map(lambda v: canonical_team(v) if isinstance(v, str) else v)
        epa_frames.append(team_game_epa(pbp, sched[sched["season"] == season]))
        v5_frames.append(v5.team_game_rows(pbp, season, team_map=canonical_team))
    features = build_market_blind_frame(sched, pd.concat(epa_frames, ignore_index=True))
    coverage = assert_prefit_coverage(sched, pd.concat(epa_frames, ignore_index=True))
    rows = v5.attach_points(pd.concat(v5_frames, ignore_index=True), sched)
    incumbent_rows, dropped = _incumbent_feature_rows(rows, sched)
    selection_rows = _score_seasons(
        features, incumbent_rows, SELECTION_SEASONS, snapshot["locked_sha"],
    )
    if int(max(r["season"] for r in selection_rows)) >= FINAL_SEASON_FIRST:
        raise RuntimeError("selection scored a final-evaluation season")
    decision = _selection_decision(selection_rows, sched)
    if not decision["open_final"]:
        raise RuntimeError("selection did not open the final evaluation; 2016-2025 was not scored")
    final_rows = _score_seasons(features, incumbent_rows, final_seasons(), snapshot["locked_sha"])
    if int(min(r["season"] for r in final_rows)) < FINAL_SEASON_FIRST:
        raise RuntimeError("final evaluation scored a selection season")
    summary = grade_predictions(final_rows)
    summary["input_hashes"] = {"games": games_hash, "pbp": pbp_hashes, "code": snapshot}
    summary["coverage"] = coverage
    summary["dropped_incumbent"] = dropped
    summary["selection"] = decision
    summary["frozen_prod_replay"] = "descriptive_only_not_the_gate"
    write_results_once(RESULTS_PATH, json.dumps(summary, indent=2, default=_json))
    return summary


def _score_seasons(features, incumbent_rows, seasons, locked_sha: str) -> list[dict]:
    graded: list[dict] = []
    for season in seasons:
        train = features[
            (features["game_type"] == GRADED_GAME_TYPE)
            & (features["season"] >= TRAIN_FIRST_SEASON)
            & (features["season"] < season)
        ]
        test = features[
            (features["game_type"] == GRADED_GAME_TYPE) & (features["season"] == season)
        ].dropna(subset=["home_margin"])
        fitted = fit_market_blind_models(train, locked_sha)
        if not fitted.get("trained"):
            raise RuntimeError(f"no fit for {season}: {fitted.get('reason')}")
        margins, probs, _raw = predict_market_blind(fitted, test)
        coefs = refit_stage2_margin(incumbent_rows, season, STAGE2_COLUMNS)
        coefs_no_mkt = refit_stage2_margin(incumbent_rows, season, NO_MKT_COLUMNS)
        inc = incumbent_rows[incumbent_rows["season"] == season].set_index("game_id")
        for idx, game in enumerate(test.itertuples(index=False)):
            key = str(game.game_id)
            if key not in inc.index:
                continue
            row = inc.loc[key]
            vector = coefs if bool(row["has_mkt"]) else coefs_no_mkt
            margin = apply_stage2(row.to_dict(), vector)
            graded.append({
                "season": int(season),
                "cluster": f"{int(season)}-{int(game.week_number)}",
                "actual_margin": float(game.home_margin),
                "candidate_margin": margins[idx],
                "candidate_prob": probs[idx],
                "incumbent_margin": margin,
                "incumbent_prob": win_prob(margin, SIGMA),
                "close": None if pd.isna(row["close"]) else float(row["close"]),
            })
    return graded


def _selection_decision(rows: list[dict], sched: pd.DataFrame) -> dict:
    diffs = []
    clusters = []
    for row in rows:
        if row["close"] is None:
            continue
        diffs.append(crps_normal(row["candidate_margin"], SIGMA, row["actual_margin"])
                     - crps_normal(row["close"], SIGMA, row["actual_margin"]))
        clusters.append(row["cluster"])
    se = cluster_se_of_mean(diffs, clusters)
    final = sched[
        sched["game_type"].astype(str).str.upper().eq(GRADED_GAME_TYPE)
        & sched["season"].between(FINAL_SEASON_FIRST, final_seasons()[-1])
    ]
    n_final = int(final.groupby(["season", "week"]).ngroups)
    n_sel = len(set(clusters))
    se_final = projected_se(se, n_sel, n_final)
    decision = selection_opens_final(float(np.mean(diffs)), se, se_final)
    decision["n_clusters_selection"] = n_sel
    decision["n_clusters_final"] = n_final
    return decision


def _incumbent_feature_rows(rows: pd.DataFrame, sched: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Point-in-time v5 features. Stage-2 coefficients are not the shipped constants."""
    teams = sorted(set(rows["posteam"].dropna()))
    reg = sched[sched["game_type"].astype(str).str.upper().eq(GRADED_GAME_TYPE)]
    reg = reg[reg["season"].between(TRAIN_FIRST_SEASON, final_seasons()[-1])]
    built = []
    dropped = 0
    for season, weeks in reg.groupby("season"):
        for week in sorted(weeks["week"].dropna().unique()):
            model = v5.build_week_model(rows, sched, int(season), int(week), teams)
            for game in weeks[weeks["week"] == week].itertuples(index=False):
                if not (model.has_team(game.home_team) and model.has_team(game.away_team)):
                    dropped += 1
                    continue
                feats = model.features(
                    game.home_team,
                    game.away_team,
                    game.home_qb_id if isinstance(getattr(game, "home_qb_id", None), str) else None,
                    game.away_qb_id if isinstance(getattr(game, "away_qb_id", None), str) else None,
                )
                neutral = str(getattr(game, "location", "") or "") == "Neutral"
                record = {col: float(feats.get(col, np.nan)) for col in STAGE2_COLUMNS if col != "hfa"}
                record["hfa"] = 0.0 if neutral else 1.0
                record["has_mkt"] = "mkt_d" in feats
                record["season"] = int(season)
                record["game_id"] = str(game.game_id)
                home = getattr(game, "home_score", None)
                away = getattr(game, "away_score", None)
                record["result"] = np.nan if pd.isna(home) or pd.isna(away) else float(home) - float(away)
                close = getattr(game, "spread_line", None)
                record["close"] = np.nan if close is None or pd.isna(close) else float(close)
                # Played starter ids are hindsight. They are not a T-24h chart.
                built.append(record)
    return pd.DataFrame(built), {"n_dropped_missing_team": dropped}


def _fetch(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=120) as response:
        raw = response.read()
    return raw, hashlib.sha256(raw).hexdigest()


def _read_hashed_csv(url: str) -> tuple[pd.DataFrame, str]:
    raw, digest = _fetch(url)
    return pd.read_csv(io.BytesIO(raw)), digest


def _parquet(raw: bytes) -> pd.DataFrame:
    try:
        return pd.read_parquet(io.BytesIO(raw), columns=PBP_COLS)
    except Exception:  # noqa: BLE001 — older seasons omit some columns
        frame = pd.read_parquet(io.BytesIO(raw))
        keep = [col for col in PBP_COLS if col in frame.columns]
        return frame[keep]


def _json(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(type(value))


def main() -> None:
    run_locked_eval()


if __name__ == "__main__":
    main()
