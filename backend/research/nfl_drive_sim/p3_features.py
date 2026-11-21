"""Pre-week features and cards. This does not score a game.

Ratings and the drive model are fit on kickoffs strictly before the week's
first kickoff. No sim draw and no v5 margin, total, CRPS, MAE, Brier, or d
is computed.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

import pandas as pd

from app.services.team_ratings_v5 import attach_points, team_game_rows

from . import sim_config as cfg
from .calendar import parse_wall_kickoff
from .clock import fit_clock, fit_late_table, fit_scrimmage_lengths
from .drives import build_drive_table
from .models import predict_ridge
from .p2_data import qb_game_table, team_game_table
from .p2_fit import _apply, _matrix, fit_drive_model, fit_pace, fit_ypp
from .p3_sources import DERIVED, LOCKED_GAMES
from .priors import with_priors
from .run_p2 import _attach_opponent_defense, _banks, _card, _rates
from .run_p3 import assign_quarterbacks, merge_manifest, rows_before_cutoff
from .teams import canon
from .v5_replay import ratings_as_of

MANIFEST = Path(__file__).resolve().parent / "outputs" / "p3" / "manifest.sha256"
DEPTH_DIR = Path(__file__).resolve().parent / ".cache" / "depth_charts"


def _schedule() -> pd.DataFrame:
    games = pd.read_parquet(LOCKED_GAMES)
    kicks = []
    for rec in games.itertuples(index=False):
        kick, _filled = parse_wall_kickoff(rec.gameday, rec.gametime)
        kicks.append(kick)
    games = games.copy()
    games["kickoff_et"] = kicks
    games["game_id"] = games["game_id"].astype(str)
    return games.dropna(subset=["kickoff_et"])


def _depth_charts() -> pd.DataFrame:
    frames = []
    for path in sorted(DEPTH_DIR.glob("*.parquet")):
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _v5_rows(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for season, part in plays.groupby(plays["season"].astype(int), sort=False):
        rows = team_game_rows(part, int(season))
        if len(rows) == 0:
            continue
        frames.append(rows)
    rows = pd.concat(frames, ignore_index=True)
    rows = attach_points(rows, games)
    rows["game_id"] = rows["game_id"].astype(str)
    meta = games[["game_id", "kickoff_et", "game_type", "location"]].copy()
    rows = rows.merge(meta, on="game_id", how="left")
    return rows


def _feature_values(ratings, book, home: str, away: str, home_qb, away_qb, neutral: bool) -> dict:
    """Inputs to the no-market refit. Shipped coefficients are not applied."""
    values = {}
    for metric, (offense, defense, intercept, _hfa) in ratings.items():
        home_net = offense.get(home, 0.0) + defense.get(away, 0.0)
        away_net = offense.get(away, 0.0) + defense.get(home, 0.0)
        values[f"{metric}_d"] = home_net - away_net
        values[f"{metric}_s"] = home_net + away_net
        values[f"{metric}_ic"] = intercept
    home_delta = book.delta(home, home_qb if isinstance(home_qb, str) else None)
    away_delta = book.delta(away, away_qb if isinstance(away_qb, str) else None)
    values["qb_d"] = home_delta - away_delta
    values["qb_s"] = home_delta + away_delta
    values["env2"] = 2.0 * float(values.get("pts_ic", 0.0))
    values["hfa"] = 0.0 if neutral else 1.0
    return values


def _ypp_frame(frame: pd.DataFrame, model: dict) -> tuple[pd.Series, pd.Series]:
    hats = []
    for name in ("pass", "run"):
        part = model[name]
        scaled = _apply(_matrix(frame, cfg.YPP_FEATURES, part["columns"]), part["mu"], part["sd"])
        hats.append(pd.Series(predict_ridge(scaled, part["coef"]), index=frame.index))
    return hats[0], hats[1]


def _cover_games() -> pd.DataFrame:
    root = MANIFEST.parent
    primary = pd.read_csv(root / "game_set.csv")
    history = pd.read_csv(root / "history_sets.csv")
    frame = pd.concat([primary, history], ignore_index=True)
    frame["game_id"] = frame["game_id"].astype(str)
    return frame.drop_duplicates("game_id")


def prepare_team_context() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team rows and pre-game quarterbacks. Does not fit or score."""
    print("loading derived plays", flush=True)
    plays = pd.concat(
        [pd.read_parquet(path) for path in sorted((DERIVED / "pbp_model").glob("*.parquet"))],
        ignore_index=True,
    )
    games = _schedule()
    print("building drives", flush=True)
    built = build_drive_table(plays, games)
    print("assigning quarterbacks", flush=True)
    identified = assign_quarterbacks(games, _depth_charts())
    team_rows = team_game_table(plays, built.drives, games)
    home_qb = identified[["game_id", "home_qb"]].rename(columns={"home_qb": "qb_id"})
    home_qb["is_home"] = 1.0
    away_qb = identified[["game_id", "away_qb"]].rename(columns={"away_qb": "qb_id"})
    away_qb["is_home"] = 0.0
    team_rows = team_rows.merge(
        pd.concat([home_qb, away_qb], ignore_index=True), on=["game_id", "is_home"], how="left",
    )
    print("attaching priors", flush=True)
    featured = _attach_opponent_defense(with_priors(team_rows, qb_game_table(plays, games)))
    return featured, identified


def build_coverage() -> dict:
    """Count games that have a v5 feature row and both sim cards. No scores."""
    plays = pd.concat(
        [pd.read_parquet(path) for path in sorted((DERIVED / "pbp_model").glob("*.parquet"))],
        ignore_index=True,
    )
    games = _schedule()
    built = build_drive_table(plays, games)
    charts = _depth_charts()
    identified = assign_quarterbacks(games, charts)
    qbs = identified.set_index("game_id")
    team_rows = team_game_table(plays, built.drives, games)
    home_qb = identified[["game_id", "home_qb"]].rename(columns={"home_qb": "qb_id"})
    home_qb["is_home"] = 1.0
    away_qb = identified[["game_id", "away_qb"]].rename(columns={"away_qb": "qb_id"})
    away_qb["is_home"] = 0.0
    team_rows = team_rows.merge(pd.concat([home_qb, away_qb], ignore_index=True), on=["game_id", "is_home"], how="left")
    qb_rows = qb_game_table(plays, games)
    featured = _attach_opponent_defense(with_priors(team_rows, qb_rows))
    v5_rows = _v5_rows(plays, games)
    cover = _cover_games()
    cover["kickoff_et"] = pd.to_datetime(cover["kickoff_et"], utc=True)
    cover = cover.merge(games[["game_id", "location"]], on="game_id", how="left")
    weeks = (
        cover.sort_values("kickoff_et")
        .groupby(["season", "week"], sort=False)
    )
    feature_rows = []
    card_rows = []
    card_dir = DERIVED / "cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    for (season, week), group in weeks:
        cutoff = group["kickoff_et"].min()
        train_teams = rows_before_cutoff(featured, cutoff)
        train_ids = set(train_teams["game_id"].astype(str))
        train_drives = built.drives[built.drives["game_id"].astype(str).isin(train_ids)]
        train_plays = plays[plays["game_id"].astype(str).isin(train_ids)]
        v5_train = rows_before_cutoff(v5_rows, cutoff)
        home_names = [canon(team) for team in group["home_team"]]
        away_names = [canon(team) for team in group["away_team"]]
        ratings, book = ratings_as_of(
            v5_train, int(season), int(week), sorted(set(home_names) | set(away_names)), cutoff,
        )
        pace = fit_pace(train_teams) if len(train_teams) else {}
        clock = fit_clock(train_plays) if len(train_plays) else None
        lengths = fit_scrimmage_lengths(train_drives, train_plays) if len(train_drives) else None
        late = fit_late_table(train_drives) if len(train_drives) else None
        banks = _banks(train_drives) if len(train_drives) else None
        rates = _rates(train_drives) if len(train_drives) else None
        cards = {}
        for ypp_on in (True, False):
            ypp = fit_ypp(train_teams, ypp_on) if len(train_teams) else None
            worked = train_teams.copy()
            if ypp is None:
                worked["ypp_pass_hat"] = 0.0
                worked["ypp_run_hat"] = 0.0
            else:
                worked["ypp_pass_hat"], worked["ypp_run_hat"] = _ypp_frame(worked, ypp)
            drive_model = fit_drive_model(train_drives, worked, ypp_on) if len(train_drives) else None
            cards[ypp_on] = None if drive_model is None or clock is None or lengths is None else {
                "pace": pace, "ypp": ypp, "drive_model": drive_model, "banks": banks,
                "rates": rates, "clock": clock, "lengths": lengths, "late": late, "ypp_on": ypp_on,
            }
            if cards[ypp_on] is not None:
                path = card_dir / f"{int(season)}_{int(week)}_{int(ypp_on)}.pkl"
                path.write_bytes(pickle.dumps(cards[ypp_on]))
        print(f"week {int(season)}-{int(week)} train {len(train_teams)}", flush=True)
        for game in group.itertuples(index=False):
            gid = str(game.game_id)
            info = qbs.loc[gid] if gid in qbs.index else None
            home_qb = None if info is None else info["home_qb"]
            away_qb = None if info is None else info["away_qb"]
            drop = None
            if not isinstance(home_qb, str) or not isinstance(away_qb, str):
                drop = "missing_qb"
            home = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(1.0))]
            away = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(0.0))]
            neutral = str(getattr(game, "location", "")) == "Neutral"
            values = _feature_values(
                ratings, book, canon(game.home_team), canon(game.away_team), home_qb, away_qb, neutral,
            )
            values.update({
                "game_id": gid, "season": int(season), "week": int(week),
                "role": str(game.role), "drop_reason": drop,
            })
            feature_rows.append(values)
            for ypp_on, packed in cards.items():
                built_card = False
                if drop is None and packed is not None and len(home) and len(away):
                    _card(
                        home.iloc[0], away.iloc[0], packed["pace"], packed["ypp"], packed["drive_model"],
                        packed["banks"], packed["rates"], ypp_on, packed["clock"], packed["lengths"],
                        packed["late"], playoff=str(game.game_type) != "REG",
                    )
                    built_card = True
                card_rows.append({
                    "game_id": gid, "ypp_on": ypp_on, "card_built": built_card, "drop_reason": drop,
                    "role": str(game.role),
                })
    features = pd.DataFrame(feature_rows)
    cards_index = pd.DataFrame(card_rows)
    feat_path = DERIVED / "features_v5.parquet"
    card_path = DERIVED / "cards_index.parquet"
    features.to_parquet(feat_path, index=False)
    cards_index.to_parquet(card_path, index=False)
    updates = [
        (hashlib.sha256(feat_path.read_bytes()).hexdigest(), "derived/features_v5.parquet"),
        (hashlib.sha256(card_path.read_bytes()).hexdigest(), "derived/cards_index.parquet"),
    ]
    for path in sorted(card_dir.glob("*.pkl")):
        updates.append((hashlib.sha256(path.read_bytes()).hexdigest(), f"derived/cards/{path.name}"))
    merge_manifest(MANIFEST, updates)
    primary = cards_index[cards_index["role"].eq("primary")]
    both = primary.groupby("game_id")["card_built"].all()
    qb_drop = features[features["role"].eq("primary") & features["drop_reason"].eq("missing_qb")]
    return {
        "primary": int(features["role"].eq("primary").sum()),
        "with_v5_features": int(features["role"].eq("primary").sum()),
        "with_both_cards": int(both.sum()) if len(both) else 0,
        "missing_qb": int(qb_drop["game_id"].nunique()),
        "train_2015": int(features["role"].eq("train_2015").sum()),
        "sensitivity_2018": int(features["role"].eq("sensitivity_2018").sum()),
        "playoff": int(features["role"].eq("playoff").sum()),
    }


if __name__ == "__main__":
    print(build_coverage(), flush=True)
