"""2018 dev run. Fit on 2015-2017 and earlier 2018 kickoffs. Do not score any other season."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .cache import NflverseCache
from .calendar import prepare_schedule
from .drives import build_drive_table
from .market import assert_no_market_columns
from .metrics_p2 import pace_report, sim_report
from .models import crps_ensemble
from .p2_data import qb_game_table, team_game_table
from .p2_fit import fit_drive_model, fit_pace, fit_ypp, predict_pace, predict_ypp
from .pregame import pregame_quarterback_table
from .priors import with_priors
from .simulate import simulate_game, summarize_sims

OUTPUT = Path(__file__).resolve().parent / "outputs" / "p2"


def _positive(value: object, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not np.isfinite(number) or number <= 0:
        return fallback
    return number


def assert_dev_season(season: int) -> None:
    if int(season) != cfg.DEV_EVAL_SEASON:
        raise ValueError(f"P2 scores only {cfg.DEV_EVAL_SEASON}")


def _banks(drives: pd.DataFrame) -> dict[str, np.ndarray]:
    ordered = drives.sort_values(["game_id", "drive"])
    nxt = ordered.groupby("game_id")["start_yardline_100"].shift(-1)
    banks = {}
    for label in cfg.DRIVE_OUTCOMES:
        values = pd.to_numeric(nxt[ordered["outcome"].eq(label)], errors="coerce").dropna().to_numpy(dtype=float)
        banks[label] = values if values.size else np.array([75.0])
    kick = pd.to_numeric(ordered.loc[ordered["drive"].eq(1), "start_yardline_100"], errors="coerce").dropna()
    banks["KICKOFF"] = kick.to_numpy(dtype=float) if len(kick) else np.array([75.0])
    return banks


def _rates(drives: pd.DataFrame) -> dict[str, float]:
    td = drives[drives["outcome"].eq("TD")]
    punt = drives[drives["outcome"].eq("PUNT")]
    turn = drives[drives["outcome"].eq("TURNOVER")]
    return {
        "pat_rate": float((td["posteam_points"] >= 7).mean()) if len(td) else 0.94,
        "p_punt_return_td": float((punt["defteam_points"] >= 6).mean()) if len(punt) else 0.01,
        "p_to_return_td": float((turn["defteam_points"] >= 6).mean()) if len(turn) else 0.02,
    }


def _card(home_row, away_row, pace_models, ypp_model, drive_model, banks, rates, ypp_on: bool) -> dict:
    def pace_for(row):
        return predict_pace(row, pace_models)

    def num(row, name: str) -> float:
        value = row.get(name, 0.0)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if np.isfinite(number) else 0.0

    def context(row, home: bool) -> dict:
        ypp_pass, ypp_run = predict_ypp(row, ypp_model, ypp_on)
        return {
            "off_td": num(row, "off_td_rate"),
            "off_fg": num(row, "off_fg_rate"),
            "off_punt": num(row, "off_punt_rate"),
            "off_to": num(row, "off_to_rate"),
            "def_td": num(row, "def_td_rate"),
            "def_to": num(row, "def_to_rate"),
            "qb_epa": num(row, "qb_epa"),
            "ypp_pass": ypp_pass,
            "ypp_run": ypp_run,
            "home": 1.0 if home else 0.0,
        }

    return {
        "home_pace": pace_for(home_row),
        "away_pace": pace_for(away_row),
        "home_context": context(home_row, True),
        "away_context": context(away_row, False),
        "drive_model": drive_model,
        "banks": banks,
        "dome": float(home_row.get("dome", 0.0) or 0.0),
        "ypp_layer": ypp_on,
        **rates,
    }


def _actual_side(team_games: pd.DataFrame, game_id: str, home: bool) -> pd.Series:
    rows = team_games[team_games["game_id"].astype(str) == str(game_id)]
    side = rows[rows["is_home"].eq(1.0 if home else 0.0)]
    return side.iloc[0]


def run(cache: NflverseCache | None = None) -> dict:
    assert_dev_season(cfg.DEV_EVAL_SEASON)
    store = cache or NflverseCache()
    schedule = prepare_schedule(store.load_schedules()).games
    schedule = schedule[schedule["season"].isin([*cfg.DEV_FIT_SEASONS, cfg.DEV_EVAL_SEASON])].copy()
    assert_no_market_columns(schedule, "p2 schedule")
    pieces = []
    play_pieces = []
    for season in (*cfg.DEV_FIT_SEASONS, cfg.DEV_EVAL_SEASON):
        plays = store.load_pbp_model(int(season))
        plays = plays[plays["game_id"].astype(str).isin(set(schedule["game_id"]))].copy()
        built = build_drive_table(plays, schedule)
        pieces.append(built.drives)
        play_pieces.append(plays)
        print(f"season {season} drives {len(built.drives)}", flush=True)
    drives = pd.concat(pieces, ignore_index=True)
    plays = pd.concat(play_pieces, ignore_index=True)
    games = schedule[schedule["game_id"].isin(set(drives["game_id"]))].copy()
    charts = pd.concat(
        [store.load_table("depth_charts", int(season)) for season in (*cfg.DEV_FIT_SEASONS, cfg.DEV_EVAL_SEASON)],
        ignore_index=True,
    )
    qb_ids = pregame_quarterback_table(charts, games)
    assert_no_market_columns(qb_ids, "pregame qb")
    if "drive_first_passer_id" in qb_ids.columns:
        raise RuntimeError("pre-game quarterbacks read the in-game passer")
    team_rows = team_game_table(plays, drives, games)
    home_qb = qb_ids[qb_ids["side"].eq("home")][["game_id", "qb_id"]].rename(columns={"qb_id": "qb_id"})
    away_qb = qb_ids[qb_ids["side"].eq("away")][["game_id", "qb_id"]]
    team_rows = team_rows.merge(
        home_qb.rename(columns={"qb_id": "home_qb"}), on="game_id", how="left",
    )
    team_rows = team_rows.merge(
        away_qb.rename(columns={"qb_id": "away_qb"}), on="game_id", how="left",
    )
    team_rows["qb_id"] = np.where(team_rows["is_home"].eq(1), team_rows["home_qb"], team_rows["away_qb"])
    qb_rows = qb_game_table(plays, games)
    print("attaching priors", flush=True)
    featured = with_priors(team_rows, qb_rows)
    assert_no_market_columns(featured, "team games")
    eval_games = games[games["season"].astype(int).eq(cfg.DEV_EVAL_SEASON)].copy()
    eval_games = eval_games.sort_values(["kickoff_et", "game_id"])
    weeks = []
    for week, group in eval_games.groupby("week", sort=False):
        weeks.append((group["kickoff_et"].min(), int(week), group))
    weeks.sort(key=lambda item: item[0])

    per_game = []
    runtime = []
    banks_cache = {}
    for cutoff, week, group in weeks:
        train_teams = featured[featured["kickoff_et"] < cutoff]
        train_drives = drives.merge(
            train_teams[["game_id"]].drop_duplicates(), on="game_id", how="inner",
        )
        # Priors on the training drives come from the featured rows of those games.
        train_teams = train_teams.copy()
        pace = fit_pace(train_teams)
        ypp = fit_ypp(train_teams, cfg.YPP_LAYER)
        train_teams["ypp_pass_hat"] = [
            predict_ypp(row, ypp, cfg.YPP_LAYER)[0] for _, row in train_teams.iterrows()
        ]
        train_teams["ypp_run_hat"] = [
            predict_ypp(row, ypp, cfg.YPP_LAYER)[1] for _, row in train_teams.iterrows()
        ]
        drive_model = fit_drive_model(train_drives, train_teams, cfg.YPP_LAYER)
        if drive_model is None:
            print(f"week {week} skipped, no drive model", flush=True)
            continue
        rates = _rates(train_drives)
        banks = _banks(train_drives)
        banks_cache[week] = (pace, ypp, drive_model, banks, rates)
        print(f"week {week} train drives {len(train_drives)}", flush=True)
        for game in group.itertuples(index=False):
            gid = str(game.game_id)
            home = featured[(featured["game_id"] == gid) & (featured["is_home"].eq(1.0))]
            away = featured[(featured["game_id"] == gid) & (featured["is_home"].eq(0.0))]
            if len(home) == 0 or len(away) == 0:
                continue
            home_row, away_row = home.iloc[0], away.iloc[0]
            card = _card(home_row, away_row, pace, ypp, drive_model, banks, rates, cfg.YPP_LAYER)
            seed = cfg.SEED + int(hashlib.sha256(gid.encode()).hexdigest()[:6], 16)
            rng = np.random.default_rng(seed)
            started = time.perf_counter()
            result = simulate_game(card, cfg.N_SIMS, rng)
            runtime.append(time.perf_counter() - started)
            summary = summarize_sims(result)
            summary["pred_home_sec"] = card["home_pace"]["neutral"]["sec"]
            summary["pred_away_sec"] = card["away_pace"]["neutral"]["sec"]
            summary["pred_home_ppd"] = card["home_pace"]["neutral"]["plays"]
            summary["pred_away_ppd"] = card["away_pace"]["neutral"]["plays"]
            summary["sim_p_margin_3"] = float(np.mean(np.isin(result["margin"], [-3.0, 3.0])))
            summary["sim_p_margin_7"] = float(np.mean(np.isin(result["margin"], [-7.0, 7.0])))
            summary["game_id"] = gid
            summary["week"] = int(week)
            summary["home_team"] = game.home_team
            summary["away_team"] = game.away_team
            actual_home = float(home_row["plays"])
            actual_away = float(away_row["plays"])
            summary["actual_home_plays"] = actual_home
            summary["actual_away_plays"] = actual_away
            summary["actual_plays"] = actual_home + actual_away
            summary["actual_home_drives"] = float(home_row["drives"])
            summary["actual_away_drives"] = float(away_row["drives"])
            summary["actual_home_ppd"] = actual_home / max(float(home_row["drives"]), 1.0)
            summary["actual_away_ppd"] = actual_away / max(float(away_row["drives"]), 1.0)
            summary["actual_home_sec"] = float(home_row["sec_per_play"])
            summary["actual_away_sec"] = float(away_row["sec_per_play"])
            summary["base_home_plays"] = float(home_row["base_plays"])
            summary["base_away_plays"] = float(away_row["base_plays"])
            summary["base_home_drives"] = float(home_row["base_drives"])
            summary["base_away_drives"] = float(away_row["base_drives"])
            summary["base_home_sec"] = float(home_row["base_sec"])
            summary["base_away_sec"] = float(away_row["base_sec"])
            summary["base_home_ppd"] = float(home_row["base_ppd"])
            summary["base_away_ppd"] = float(away_row["base_ppd"])
            home_score = float(game.home_score)
            away_score = float(game.away_score)
            summary["actual_margin"] = home_score - away_score
            summary["actual_total"] = home_score + away_score
            summary["actual_home_win"] = float(home_score > away_score)
            summary["actual_ot"] = float(game.overtime) if pd.notna(game.overtime) else 0.0
            summary["crps_margin"] = crps_ensemble(result["margin"], summary["actual_margin"])
            summary["crps_total"] = crps_ensemble(result["total"], summary["actual_total"])
            card["oracle"] = True
            card["oracle_home_plays"] = max(actual_home / max(float(home_row["drives"]), 1.0), 1.0)
            card["oracle_away_plays"] = max(actual_away / max(float(away_row["drives"]), 1.0), 1.0)
            card["oracle_home_sec"] = _positive(home_row["sec_per_play"], 25.0)
            card["oracle_away_sec"] = _positive(away_row["sec_per_play"], 25.0)
            oracle_rng = np.random.default_rng(seed)
            oracle = simulate_game(card, cfg.N_SIMS, oracle_rng)
            summary["oracle_crps_total"] = crps_ensemble(oracle["total"], summary["actual_total"])
            per_game.append(summary)
            print(f"sim {gid} {runtime[-1]:.2f}s", flush=True)

    table = pd.DataFrame(per_game)
    if len(table) == 0:
        raise RuntimeError("no 2018 games were simulated")
    if not table["week"].ge(1).all() or table["game_id"].astype(str).str.startswith("2019").any():
        raise RuntimeError("P2 eval left 2018")
    report = {
        "fit": "walk-forward by week. Coefficients use kickoffs before the week's first game. "
               "A later game that week still sees earlier kickoffs from that week in its features.",
        "eval_season": cfg.DEV_EVAL_SEASON,
        "n_games": len(table),
        "n_sims": cfg.N_SIMS,
        "ypp_layer": cfg.YPP_LAYER,
        "runtime_sec_per_game": float(np.mean(runtime)),
        "pace": pace_report(table),
        "sim": sim_report(table),
        "crps_margin": float(table["crps_margin"].mean()),
        "crps_total": float(table["crps_total"].mean()),
        "crps_total_oracle_pace": float(table["oracle_crps_total"].mean()),
        "crps_total_pace_gap": float((table["crps_total"] - table["oracle_crps_total"]).mean()),
        "brier_home_win": float(np.mean((table["p_home_win"] - table["actual_home_win"]) ** 2)),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    game_path = OUTPUT / "dev_2018_games.csv"
    table.to_csv(game_path, index=False)
    report_path = OUTPUT / "dev_2018_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    (OUTPUT / "dev_2018_report.md").write_text(_report_md(report))
    config_path = Path(cfg.__file__)
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    (OUTPUT / "sim_config.sha256").write_text(f"{config_hash}  sim_config.py\n")
    _write_manifest(OUTPUT, config_hash)
    print(json.dumps(report, indent=2), flush=True)
    return report


def _cell(block: dict) -> str:
    return f"{block['mae']:.3f} | {block['bias']:.3f}"


def _report_md(report: dict) -> str:
    pace = report["pace"]
    sim = report["sim"]
    note = (
        "Seconds per play and plays per drive are the neutral-situation forecast. "
        "Plays per team and drives per team are the mean of the simulations. "
        "The baseline is an equal-weight team mean shrunk to the league mean, with no opponent."
    )
    lines = [
        "# P2 dev report, 2018 only",
        "",
        f"Fit: {report['fit']}",
        "",
        f"Games: {report['n_games']}. Sims per game: {report['n_sims']}. YPP layer: {report['ypp_layer']}.",
        f"Runtime per game: {report['runtime_sec_per_game']:.3f} seconds (predicted-pace sim only).",
        "",
        note,
        "",
        "| metric | model MAE | model bias | baseline MAE | baseline bias |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| plays per team | {_cell(pace['plays_per_team_model'])} | {_cell(pace['plays_per_team_baseline'])} |",
        f"| plays per game | {_cell(pace['plays_per_game_model'])} | {_cell(pace['plays_per_game_baseline'])} |",
        f"| drives per team | {_cell(pace['drives_per_team_model'])} | {_cell(pace['drives_per_team_baseline'])} |",
        f"| seconds per play | {_cell(pace['seconds_per_play_model'])} | {_cell(pace['seconds_per_play_baseline'])} |",
        f"| plays per drive | {_cell(pace['plays_per_drive_model'])} | {_cell(pace['plays_per_drive_baseline'])} |",
        "",
        (
            f"Game-play interval coverage: 80% {pace['game_plays_interval_80']:.3f}, "
            f"90% {pace['game_plays_interval_90']:.3f}."
        ),
        "",
        "| check | actual | simulated |",
        "| --- | ---: | ---: |",
        f"| total mean | {sim['actual_total_mean']:.3f} | {sim['sim_total_mean']:.3f} |",
        f"| total sd | {sim['actual_total_sd']:.3f} | {sim['sim_total_sd_mean']:.3f} |",
        f"| margin mean | {sim['actual_margin_mean']:.3f} | {sim['sim_margin_mean']:.3f} |",
        f"| margin sd | {sim['actual_margin_sd']:.3f} | {sim['sim_margin_sd_mean']:.3f} |",
        f"| home win rate | {sim['actual_home_win_rate']:.3f} | {sim['sim_home_win_rate']:.3f} |",
        f"| overtime rate | {sim['actual_ot_rate']:.3f} | {sim['sim_ot_rate']:.3f} |",
        f"| drives per game | {sim['actual_drives_mean']:.3f} | {sim['sim_drives_mean']:.3f} |",
        f"| drives sd | {sim['actual_drives_sd']:.3f} | {sim['sim_drives_sd_mean']:.3f} |",
        f"| share of margin 3 | {sim['actual_share_margin_3']:.3f} | {sim['sim_share_margin_3']:.3f} |",
        f"| share of margin 7 | {sim['actual_share_margin_7']:.3f} | {sim['sim_share_margin_7']:.3f} |",
        "",
        f"Margin CRPS: {report['crps_margin']:.3f}.",
        f"Total CRPS: {report['crps_total']:.3f}.",
        f"Home-win Brier: {report['brier_home_win']:.3f}.",
        f"Total CRPS with oracle play counts: {report['crps_total_oracle_pace']:.3f}.",
        f"Total CRPS difference (predicted pace minus oracle pace): {report['crps_total_pace_gap']:.3f}.",
        "",
        "These scores are for 2018 only. There is no comparison to the served rating model.",
        "",
        "Deviations:",
        "- Coefficients are fit once per week on kickoffs before that week's first game.",
        "- 2018 overtime is one 10-minute period. A touchdown on the first possession wins immediately. Otherwise both teams possess, and the next score wins. A tie stands if the clock expires. A playoff game that is still tied gets one more sudden-death period and then stops.",
        "- Timeouts are a drive-model feature. The sim does not spend them.",
        "- Pressure is qb_hit from play-by-play. Next Gen Stats are not used.",
        "- The ridge and softmax fits are numpy. Seconds per play is the game-clock span of a drive's scrimmage plays divided by plays minus one, then applied to every play.",
        "- Schedule abbreviations are canonicalized to the play-by-play codes (OAK to LV, STL to LA, SD to LAC) for the team join. The drive table keeps the abbreviation on the play.",
        "- The quarterback prior is the pre-game depth-chart quarterback's dropback EPA and CPOE, shrunk to the mean of earlier dropbacks.",
        "",
    ]
    return "\n".join(lines)


def _write_manifest(directory: Path, config_hash: str) -> None:
    lines = [f"{config_hash}  sim_config.py"]
    for path in sorted(directory.glob("*")):
        if path.name == "manifest.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (directory / "manifest.sha256").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run()
