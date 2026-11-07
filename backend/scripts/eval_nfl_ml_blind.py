"""One walk-forward look for the market-blind NFL ML layer.

Do not run this until QA locks research/nfl-ml-blind/PREREG.md.
Importing the module does not download data, fit a model, or score a game.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.adapters.data.nflverse_stats import RELEASE
from app.services import team_ratings_v5 as v5
from app.services.ml_blind_features import build_market_blind_frame, team_game_epa
from app.services.ml_blind_protocol import grade_predictions, require_qa_lock
from app.services.ml_blind_spec import (
    GRADED_GAME_TYPE,
    SIGMA,
    TEST_SEASON_FIRST,
    TRAIN_FIRST_SEASON,
    test_seasons,
)
from app.services.ml_predictions_service import fit_market_blind_models, predict_market_blind
from app.services.prediction_dist import win_prob
from app.utils.teams import canonical_team

PBP_COLS = [
    "game_id", "week", "season_type", "posteam", "defteam", "home_team", "away_team",
    "epa", "play_type", "pass", "rush", "qb_kneel", "qb_spike", "wp", "qtr",
    "score_differential", "special_teams_play", "qb_dropback", "passer_player_id",
]


def run_locked_eval() -> dict:
    """Fit each prior-season window once and grade 2016–2025. QA lock required."""
    require_qa_lock()
    first = TRAIN_FIRST_SEASON - 1
    sched = pd.read_csv(f"{RELEASE}/schedules/games.csv")
    sched = sched[sched["season"] >= first].copy()
    for col in ("home_team", "away_team"):
        sched[col] = sched[col].map(lambda v: canonical_team(v) if isinstance(v, str) else v)
    last = TEST_SEASON_FIRST + len(test_seasons()) - 1
    epa_frames: list[pd.DataFrame] = []
    v5_frames: list[pd.DataFrame] = []
    for season in range(first, last + 1):
        pbp = _read_pbp(season)
        pbp["season"] = season
        for col in ("posteam", "defteam", "home_team", "away_team"):
            if col in pbp.columns:
                pbp[col] = pbp[col].map(lambda v: canonical_team(v) if isinstance(v, str) else v)
        epa_frames.append(team_game_epa(pbp, sched[sched["season"] == season]))
        v5_frames.append(v5.team_game_rows(pbp, season, team_map=canonical_team))
    features = build_market_blind_frame(sched, pd.concat(epa_frames, ignore_index=True))
    rows = v5.attach_points(pd.concat(v5_frames, ignore_index=True), sched)
    teams = sorted(set(rows["posteam"].dropna()))
    incumbent = _incumbent_margins(rows, sched, teams)
    graded: list[dict] = []
    for season in test_seasons():
        train = features[
            (features["game_type"] == GRADED_GAME_TYPE)
            & (features["season"] >= TRAIN_FIRST_SEASON)
            & (features["season"] < season)
        ]
        test = features[
            (features["game_type"] == GRADED_GAME_TYPE) & (features["season"] == season)
        ].dropna(subset=["home_margin"])
        fitted = fit_market_blind_models(train)
        if not fitted.get("trained"):
            raise RuntimeError(f"no fit for {season}: {fitted.get('reason')}")
        margins, probs, _raw = predict_market_blind(fitted, test)
        for idx, game in enumerate(test.itertuples(index=False)):
            key = str(game.game_id)
            if key not in incumbent:
                continue
            graded.append({
                "actual_margin": float(game.home_margin),
                "candidate_margin": margins[idx],
                "candidate_prob": probs[idx],
                "incumbent_margin": incumbent[key]["margin"],
                "incumbent_prob": win_prob(incumbent[key]["margin"], SIGMA),
                "close": incumbent[key]["close"],
            })
    summary = grade_predictions(graded)
    out = Path(__file__).resolve().parents[2] / "research" / "nfl-ml-blind" / "RESULTS.json"
    out.write_text(json.dumps(summary, indent=2, default=_json))
    return summary


def _read_pbp(season: int) -> pd.DataFrame:
    """Older releases do not have every current column. Missing ones are omitted."""
    url = f"{RELEASE}/pbp/play_by_play_{season}.parquet"
    try:
        return pd.read_parquet(url, columns=PBP_COLS)
    except Exception:  # noqa: BLE001 — column set, not a silent empty season
        frame = pd.read_parquet(url)
        keep = [col for col in PBP_COLS if col in frame.columns]
        return frame[keep]


def _incumbent_margins(rows: pd.DataFrame, sched: pd.DataFrame, teams: list[str]) -> dict[str, dict]:
    """Deployed qb-ridge-v5 margin. Past-line prior stays. The current close does not."""
    out: dict[str, dict] = {}
    reg = sched[sched["game_type"].astype(str).str.upper() == GRADED_GAME_TYPE]
    reg = reg[reg["season"].between(TEST_SEASON_FIRST, test_seasons()[-1])]
    for season, weeks in reg.groupby("season"):
        for week in sorted(weeks["week"].dropna().unique()):
            model = v5.build_week_model(rows, sched, int(season), int(week), teams)
            slate = weeks[weeks["week"] == week]
            for game in slate.itertuples(index=False):
                if not (model.has_team(game.home_team) and model.has_team(game.away_team)):
                    continue
                pred = model.predict(
                    game.home_team,
                    game.away_team,
                    neutral=str(getattr(game, "location", "") or "") == "Neutral",
                    home_qb=game.home_qb_id if isinstance(getattr(game, "home_qb_id", None), str) else None,
                    away_qb=game.away_qb_id if isinstance(getattr(game, "away_qb_id", None), str) else None,
                )
                close = getattr(game, "spread_line", None)
                out[str(game.game_id)] = {
                    "margin": float(pred["margin"]),
                    "close": None if close is None or (isinstance(close, float) and np.isnan(close)) else float(close),
                }
    return out


def _json(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(type(value))


def main() -> None:
    run_locked_eval()


if __name__ == "__main__":
    main()
