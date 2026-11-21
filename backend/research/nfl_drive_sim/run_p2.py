"""2018 dev run. Fit on 2015-2017 and earlier 2018 kickoffs. Do not score any other season."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .cache import NflverseCache
from .calendar import prepare_schedule
from .clock import fit_clock, fit_late_table, fit_scrimmage_lengths
from .diagnostics import actual_diagnostic, sim_diagnostic, write_diagnostic
from .drives import build_drive_table
from .market import assert_no_market_columns
from .metrics_p2 import pace_report, sim_report
from .models import crps_ensemble
from .p2_data import qb_game_table, team_game_table
from .p2_fit import fit_drive_model, fit_pace, fit_ypp, predict_pace, predict_ypp
from .pregame import pregame_quarterback_table
from .priors import with_priors
from .simulate import new_tally, simulate_game, summarize_sims

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


def _rate(row, name: str, fallback: float) -> float:
    value = row.get(name, fallback) if hasattr(row, "get") else fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if np.isfinite(number) else fallback


def _card(home_row, away_row, pace_models, ypp_model, drive_model, banks, rates, ypp_on: bool,
          clock: dict | None = None, lengths: dict | None = None, late: dict | None = None,
          playoff: bool = False) -> dict:
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
        "clock": clock,
        "lengths": lengths,
        "late_table": late,
        "playoff": playoff,
        "home_pass": _rate(home_row, "off_pass_rate", 0.55),
        "away_pass": _rate(away_row, "off_pass_rate", 0.55),
        "home_inc": _rate(home_row, "off_incomplete", 0.35),
        "away_inc": _rate(away_row, "off_incomplete", 0.35),
        "home_sack": _rate(home_row, "off_sack", 0.06),
        "away_sack": _rate(away_row, "off_sack", 0.06),
        **rates,
    }


def _actual_side(team_games: pd.DataFrame, game_id: str, home: bool) -> pd.Series:
    rows = team_games[team_games["game_id"].astype(str) == str(game_id)]
    side = rows[rows["is_home"].eq(1.0 if home else 0.0)]
    return side.iloc[0]


def _attach_opponent_defense(featured: pd.DataFrame) -> pd.DataFrame:
    """Put the other team's allowed pace on this row.

    ``def_*`` on a row is what that team allows when it plays defense.
    The pace model needs the opponent's version of that, not its own.
    """
    defense = [column for column in featured.columns if column.startswith("def_")]
    if not defense:
        return featured
    side = featured[["game_id", "team", *defense]].copy()
    side = side.rename(columns={"team": "opponent", **{column: f"opp_{column}" for column in defense}})
    return featured.merge(side, on=["game_id", "opponent"], how="left")


def _load_dev_frames(store: NflverseCache):
    schedule = prepare_schedule(store.load_schedules()).games
    schedule = schedule[schedule["season"].isin([*cfg.DEV_FIT_SEASONS, cfg.DEV_EVAL_SEASON])].copy()
    assert_no_market_columns(schedule, "p2 schedule")
    pieces = []
    play_pieces = []
    for season in (*cfg.DEV_FIT_SEASONS, cfg.DEV_EVAL_SEASON):
        season_plays = store.load_pbp_model(int(season))
        season_plays = season_plays[season_plays["game_id"].astype(str).isin(set(schedule["game_id"]))].copy()
        built = build_drive_table(season_plays, schedule)
        pieces.append(built.drives)
        play_pieces.append(season_plays)
        print(f"season {season} drives {len(built.drives)}", flush=True)
    drives = pd.concat(pieces, ignore_index=True)
    plays = pd.concat(play_pieces, ignore_index=True)
    games = schedule[schedule["game_id"].isin(set(drives["game_id"]))].copy()
    return schedule, drives, plays, games


def run(
    cache: NflverseCache | None = None,
    *,
    n_sims: int | None = None,
    with_oracle: bool = True,
    ypp_layer: bool | None = None,
    diagnostic_label: str | None = None,
    output_suffix: str = "",
) -> dict:
    assert_dev_season(cfg.DEV_EVAL_SEASON)
    store = cache or NflverseCache()
    bundle_path = Path("/tmp/p2_prepared.pkl")
    if os.environ.get("P2_REUSE") == "1" and bundle_path.exists():
        print("reusing prepared 2015-2018 plays and drives", flush=True)
        bundle = pickle.load(bundle_path.open("rb"))
        schedule = bundle["schedule"]
        drives = bundle["drives"]
        plays = bundle["plays"]
        games = bundle["games"]
    else:
        schedule, drives, plays, games = _load_dev_frames(store)
        pickle.dump(
            {"schedule": schedule, "drives": drives, "plays": plays, "games": games},
            bundle_path.open("wb"),
        )
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
    featured = _attach_opponent_defense(featured)
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
    sims = cfg.N_SIMS if n_sims is None else int(n_sims)
    use_ypp = cfg.YPP_LAYER if ypp_layer is None else bool(ypp_layer)
    tally = new_tally() if diagnostic_label else None
    pace_coefs = None
    for cutoff, week, group in weeks:
        train_teams = featured[featured["kickoff_et"] < cutoff]
        train_drives = drives.merge(
            train_teams[["game_id"]].drop_duplicates(), on="game_id", how="inner",
        )
        # Priors on the training drives come from the featured rows of those games.
        train_teams = train_teams.copy()
        pace = fit_pace(train_teams)
        if pace_coefs is None:
            pace_coefs = {
                f"{state}_{target}": None if model is None else [float(v) for v in model["coef"]]
                for (state, target), model in pace.items()
            }
        ypp = fit_ypp(train_teams, use_ypp)
        train_teams["ypp_pass_hat"] = [
            predict_ypp(row, ypp, use_ypp)[0] for _, row in train_teams.iterrows()
        ]
        train_teams["ypp_run_hat"] = [
            predict_ypp(row, ypp, use_ypp)[1] for _, row in train_teams.iterrows()
        ]
        drive_model = fit_drive_model(train_drives, train_teams, use_ypp)
        if drive_model is None:
            print(f"week {week} skipped, no drive model", flush=True)
            continue
        rates = _rates(train_drives)
        banks = _banks(train_drives)
        train_ids = set(train_teams["game_id"].astype(str))
        train_plays = plays[plays["game_id"].astype(str).isin(train_ids)]
        clock = fit_clock(train_plays)
        lengths = fit_scrimmage_lengths(train_drives, train_plays)
        late = fit_late_table(train_drives)
        banks_cache[week] = (pace, ypp, drive_model, banks, rates)
        print(f"week {week} train drives {len(train_drives)}", flush=True)
        for game in group.itertuples(index=False):
            gid = str(game.game_id)
            home = featured[(featured["game_id"] == gid) & (featured["is_home"].eq(1.0))]
            away = featured[(featured["game_id"] == gid) & (featured["is_home"].eq(0.0))]
            if len(home) == 0 or len(away) == 0:
                continue
            home_row, away_row = home.iloc[0], away.iloc[0]
            card = _card(
                home_row, away_row, pace, ypp, drive_model, banks, rates, use_ypp, clock, lengths, late,
                playoff=str(getattr(game, "game_type", "REG")) not in {"REG", "nan", "None"},
            )
            if tally is not None:
                card["diagnose"] = tally
            seed = cfg.SEED + int(hashlib.sha256(gid.encode()).hexdigest()[:6], 16)
            rng = np.random.default_rng(seed)
            started = time.perf_counter()
            result = simulate_game(card, sims, rng)
            runtime.append(time.perf_counter() - started)
            summary = summarize_sims(result)
            summary["pred_home_sec"] = card["home_pace"]["neutral"]["sec"]
            summary["pred_away_sec"] = card["away_pace"]["neutral"]["sec"]
            summary["pred_home_ppd"] = card["home_pace"]["neutral"]["plays"]
            summary["pred_away_ppd"] = card["away_pace"]["neutral"]["plays"]
            summary["sim_home_sec"] = float(result["home_scr_sec"].mean() / max(result["home_plays"].mean(), 1e-6))
            summary["sim_away_sec"] = float(result["away_scr_sec"].mean() / max(result["away_plays"].mean(), 1e-6))
            summary["sim_home_ppd"] = float(result["home_plays"].mean() / max(result["home_drives"].mean(), 1e-6))
            summary["sim_away_ppd"] = float(result["away_plays"].mean() / max(result["away_drives"].mean(), 1e-6))
            summary["sim_p_margin_3"] = float(np.mean(np.isin(result["margin"], [-3.0, 3.0])))
            summary["sim_p_margin_7"] = float(np.mean(np.isin(result["margin"], [-7.0, 7.0])))
            summary["game_id"] = gid
            summary["week"] = int(week)
            summary["game_type"] = str(getattr(game, "game_type", "REG"))
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
            if with_oracle:
                card["oracle"] = True
                card["oracle_home_plays"] = max(actual_home / max(float(home_row["drives"]), 1.0), 1.0)
                card["oracle_away_plays"] = max(actual_away / max(float(away_row["drives"]), 1.0), 1.0)
                card["oracle_home_sec"] = _positive(home_row["sec_per_play"], 25.0)
                card["oracle_away_sec"] = _positive(away_row["sec_per_play"], 25.0)
                card.pop("diagnose", None)
                oracle = simulate_game(card, sims, np.random.default_rng(seed))
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
        "n_reg": int(table["game_type"].eq("REG").sum()) if "game_type" in table.columns else len(table),
        "n_playoff": int((~table["game_type"].eq("REG")).sum()) if "game_type" in table.columns else 0,
        "n_sims": sims,
        "ypp_layer": use_ypp,
        "runtime_sec_per_game": float(np.mean(runtime)),
        "pace": pace_report(table),
        "sim": sim_report(table),
        "crps_margin": float(table["crps_margin"].mean()),
        "crps_total": float(table["crps_total"].mean()),
        "brier_home_win": float(np.mean((table["p_home_win"] - table["actual_home_win"]) ** 2)),
    }
    if "oracle_crps_total" in table.columns:
        report["crps_total_oracle_pace"] = float(table["oracle_crps_total"].mean())
        report["crps_total_pace_gap"] = float((table["crps_total"] - table["oracle_crps_total"]).mean())
    if pace_coefs is not None:
        report["pace_coefficients_first_week"] = pace_coefs
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if diagnostic_label and tally is not None:
        eval_ids = set(table["game_id"].astype(str))
        actual = actual_diagnostic(
            drives[drives["game_id"].astype(str).isin(eval_ids)].copy(),
            plays[plays["game_id"].astype(str).isin(eval_ids)].copy(),
        )
        simulated = sim_diagnostic(tally, n_games=len(table), n_sims=sims)
        write_diagnostic(OUTPUT / f"diagnostic_{diagnostic_label}.csv", actual, simulated)
    game_path = OUTPUT / f"dev_2018_games{output_suffix}.csv"
    table.to_csv(game_path, index=False)
    report_path = OUTPUT / f"dev_2018_report{output_suffix}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    (OUTPUT / f"dev_2018_report{output_suffix}.md").write_text(_report_md(report))
    config_path = Path(cfg.__file__)
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    (OUTPUT / "sim_config.sha256").write_text(f"{config_hash}  sim_config.py\n")
    _write_manifest(OUTPUT, config_hash)
    print(json.dumps({k: v for k, v in report.items() if k != "pace_coefficients_first_week"}, indent=2), flush=True)
    return report


def _cell(block: dict) -> str:
    return f"{block['mae']:.3f} | {block['bias']:.3f}"


def _report_md(report: dict) -> str:
    pace = report["pace"]
    sim = report["sim"]
    note = (
        "Seconds per play and plays per drive are the simulated scrimmage means. "
        "Plays per team and drives per team are the mean of the simulations. "
        "The baseline is an equal-weight team mean shrunk to the league mean, with no opponent."
    )
    lines = [
        "# P2 dev report, 2018 only",
        "",
        f"Fit: {report['fit']}",
        "",
        (
            f"Games: {report['n_games']} "
            f"({report.get('n_reg', report['n_games'])} regular season + "
            f"{report.get('n_playoff', 0)} playoffs). "
            f"Sims per game: {report['n_sims']}. YPP layer: {report['ypp_layer']}."
        ),
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
        *(
            [
                f"Total CRPS with the game-average pace oracle: {report['crps_total_oracle_pace']:.3f}.",
                f"Gap, predicted pace minus that oracle: {report['crps_total_pace_gap']:.3f}.",
            ]
            if "crps_total_oracle_pace" in report else []
        ),
        "The oracle is a handicapped benchmark: it uses this game's average",
        "scrimmage pace, scaled across outcomes, and not each drive's own length.",
        "On the previous run the gap was -0.253 with a week-cluster 95% interval",
        "[-0.644, +0.112], so it is not evidence that the predicted pace is better.",
        "",
        "These scores are for the 2018 sample only. There is no comparison to the served rating model.",
        "",
        *_before_after_lines(report),
        "Deviations:",
        "- Coefficients are fit once per week on kickoffs before that week's first game.",
        "- 2018 overtime is 10 minutes. A first-possession touchdown or safety ends it. After both teams have possessed, the next score that breaks the tie wins. A field goal that only ties the game does not. A regular-season tie stands. A tied playoff starts another period.",
        "- An end-of-half outcome expires the rest of that half. The second half then starts with a kickoff. In the fourth quarter or overtime it ends the game, or overtime if the score is tied.",
        "- Two-minute endings come from a training table of score, yardline, time left, and timeouts, shrunk toward coarser cells.",
        "- Timeouts are a drive-model feature. The sim does not spend them.",
        "- Pressure is qb_hit from play-by-play. Next Gen Stats are not used.",
        "- Scrimmage plays are passes, runs, kneels, and spikes. Punts, kicks, field goals, and extra points take their own runoff.",
        "- Incompletions, spikes, and out-of-bounds plays use the stopped-clock mean. Running-clock plays are shifted so the mixture matches the pace model's seconds per scrimmage play.",
        "- Plays per drive follow the training length of that outcome, plus the team's pace-model offset. The offset uses the opponent's allowed pace, not the offense's own defense.",
        "- The drive-outcome fit is gradient ascent. The previous one-class Newton step, with a penalty of 1, could not reproduce the 2015-17 two-minute end-of-half rate.",
        "- The ridge and softmax fits are numpy.",
        "- Schedule abbreviations are canonicalized to the play-by-play codes (OAK to LV, STL to LA, SD to LAC) for the team join. The drive table keeps the abbreviation on the play.",
        "- The quarterback prior is the pre-game depth-chart quarterback's dropback EPA and CPOE, shrunk to the mean of earlier dropbacks.",
        "",
    ]
    return "\n".join(lines)


def _before_after_lines(report: dict) -> list[str]:
    """36884a2 versus this run. The earlier numbers are that commit's yards-on report."""
    previous = {
        "margin-3 share": 0.076249,
        "margin-7 share": 0.076437,
        "overtime rate": 0.036936,
        "total sd": 13.304,
        "drives per game": 23.457,
        "plays per team MAE": 6.858,
        "plays per game MAE": 6.828,
        "drives per team MAE": 1.353,
        "seconds per play bias": 0.763,
        "coverage 80": 0.854,
        "coverage 90": 0.925,
        "margin CRPS": 7.335,
        "total CRPS": 7.925,
        "Brier": 0.218,
    }
    sim = report["sim"]
    pace = report["pace"]
    current = {
        "margin-3 share": sim["sim_share_margin_3"],
        "margin-7 share": sim["sim_share_margin_7"],
        "overtime rate": sim["sim_ot_rate"],
        "total sd": sim["sim_total_sd_mean"],
        "drives per game": sim["sim_drives_mean"],
        "plays per team MAE": pace["plays_per_team_model"]["mae"],
        "plays per game MAE": pace["plays_per_game_model"]["mae"],
        "drives per team MAE": pace["drives_per_team_model"]["mae"],
        "seconds per play bias": pace["seconds_per_play_model"]["bias"],
        "coverage 80": pace["game_plays_interval_80"],
        "coverage 90": pace["game_plays_interval_90"],
        "margin CRPS": report["crps_margin"],
        "total CRPS": report["crps_total"],
        "Brier": report["brier_home_win"],
    }
    lines = [
        "Before (36884a2) versus this run, yards layer as in this file:",
        "",
        "| metric | 36884a2 | this run |",
        "| --- | ---: | ---: |",
    ]
    for name, old in previous.items():
        lines.append(f"| {name} | {old:.3f} | {current[name]:.3f} |")
    lines.extend([
        "",
        (
            f"From yards-on 8ac7801 to this file: drives per game {current['drives per game']:.3f} "
            f"(was 23.328), overtime rate {current['overtime rate']:.3f} (was 0.040)."
        ),
        "",
        "## Disclosures",
        "",
        "- 2018 is dev-touched.",
        "- Margin-3 and margin-7 key-number mass is low. There is no fourth-quarter endgame model yet. Later work covers clock-killing on one-score leads, playing for a field goal before the two-minute mark, score-dependent late outcomes, and spending timeouts. That work is accepted on a later season against pre-registered checks.",
        "- Safeties are about 0.87% of drives against 0.17% actual.",
        "- Two-minute drives are short. The causes are truncation, a slow runoff, and timeouts that are never spent.",
        "- Seconds per play bias is about +1.07.",
        "- The overtime gap comes from how often regulation ends tied.",
        "- Predicted pace versus the game-average oracle is not significant. The oracle is a handicapped benchmark.",
        "- P3 must not be used as a fair ATS or key-number comparison against v5 until an endgame model is accepted.",
        "",
        "## Note for P3",
        "",
        "P3 is not started. When it is, pre-register margin-3 share, margin-7 share, the regulation-tie rate, the overtime-entry rate, and seconds-per-play bias as tracked metrics, and commit a margin-at-5:00-left series in the P3 outputs.",
        "",
    ])
    return lines


def _write_manifest(directory: Path, config_hash: str) -> None:
    lines = [f"{config_hash}  sim_config.py"]
    for path in sorted(directory.glob("*")):
        if path.name == "manifest.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (directory / "manifest.sha256").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "diagnose-before":
        run(n_sims=1500, with_oracle=False, diagnostic_label="before")
    else:
        run(diagnostic_label="after")
        run(
            ypp_layer=False, with_oracle=False, diagnostic_label="ypp_off", output_suffix="_ypp_off",
        )
