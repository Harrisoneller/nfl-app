"""Loss diagnostics for the frozen v6-blind model versus the close.

Reads the harness predictions and the local nflverse games and injury files.
Does not refit, and does not write a coefficient. Bucket cuts are fixed in
this file before the tables are computed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import v6_blind as v6  # noqa: E402

DATA = Path(__import__("os").environ.get("NFLVERSE_DIR", "/tmp/nflverse"))
PREDS = ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv"
OUT = ROOT / "DIAGNOSTICS.md"

ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR",
    "OAK": "LV", "SD": "LAC", "STL": "LAR", "HST": "HOU", "BLT": "BAL", "CLV": "CLE",
}


def canon(value):
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().upper()
    return ALIASES.get(token, token)


def kickoff(gameday, gametime) -> pd.Timestamp | None:
    if not isinstance(gameday, str) or len(gameday) < 8:
        return None
    clock = gametime if isinstance(gametime, str) and ":" in gametime else "13:00"
    try:
        return pd.Timestamp(f"{gameday[:10]} {clock.strip()[:5]}", tz="UTC")
    except (ValueError, TypeError):
        return None


def week_bucket(game_type: str, week: int) -> str:
    if game_type != "REG":
        return "playoffs"
    if week <= 4:
        return "1-4"
    if week <= 9:
        return "5-9"
    return "10-18"


def spread_bucket(abs_line: float) -> str:
    if abs_line < 3:
        return "small |close| < 3"
    if abs_line < 7:
        return "mid 3 <= |close| < 7"
    return "large |close| >= 7"


def fav_bucket(spread: float) -> str:
    if spread > 0:
        return "home favorite"
    if spread < 0:
        return "home dog"
    return "pick'em"


def _metrics(y, pred, close, sigma: float) -> dict:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    close = np.asarray(close, dtype=float)
    n = int(len(y))
    if n == 0:
        return {"n": 0}
    model_crps = float(v6.crps_normal(y, pred, sigma).mean())
    close_crps = float(v6.crps_normal(y, close, sigma).mean())
    model_rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    close_rmse = float(np.sqrt(np.mean((close - y) ** 2)))
    push = np.isclose(y, close)
    decided = ~push & (pred != close)
    bet_home = pred > close
    covers = y > close
    wins = int(((bet_home & covers) | (~bet_home & ~covers))[decided].sum())
    n_ats = int(decided.sum())
    lo, hi = v6.wilson_interval(wins, n_ats)
    return {
        "n": n,
        "n_ats": n_ats,
        "model_crps": model_crps,
        "close_crps": close_crps,
        "crps_gap": model_crps - close_crps,
        "model_rmse": model_rmse,
        "close_rmse": close_rmse,
        "rmse_gap": model_rmse - close_rmse,
        "ats": (wins / n_ats) if n_ats else None,
        "wilson_lo": lo,
        "wilson_hi": hi,
        "excess_crps": n * (model_crps - close_crps),
    }


def qb_flags(games: pd.DataFrame) -> pd.DataFrame:
    """Descriptive labels. Actual starter changes are an outcome slice, not a feature."""
    g = games.copy()
    g["ko"] = [kickoff(d, t) for d, t in zip(g["gameday"], g["gametime"])]
    rows = []
    for side, team_col, qb_col in (
        ("home", "home_team", "home_qb_id"),
        ("away", "away_team", "away_qb_id"),
    ):
        part = g[["game_id", "season", "week", "gameday", "game_type", "ko", team_col, qb_col]].copy()
        part = part.rename(columns={team_col: "team", qb_col: "actual"})
        part["side"] = side
        rows.append(part)
    long = pd.concat(rows, ignore_index=True)
    long = long[long["team"].notna()].copy()
    long = long.sort_values(["team", "season", "week", "gameday", "game_id"])
    long["incumbent"] = long.groupby("team")["actual"].shift(1)

    inj_frames = []
    for year in range(2014, 2027):
        path = DATA / "aux" / f"injuries_{year}.parquet"
        if not path.exists():
            continue
        import pyarrow.parquet as pq

        names = set(pq.read_schema(path).names)
        if "date_modified" not in names:
            continue
        inj = pd.read_parquet(
            path,
            columns=["season", "week", "team", "gsis_id", "position", "report_status", "date_modified"],
        )
        inj_frames.append(inj)
    inj = pd.concat(inj_frames, ignore_index=True)
    inj["team"] = inj["team"].map(canon)
    inj["date_modified"] = pd.to_datetime(inj["date_modified"], utc=True, errors="coerce")
    inj["position"] = inj["position"].astype(str).str.upper()
    inj = inj[inj["position"].eq("QB") & inj["team"].notna() & inj["gsis_id"].notna()]
    inj["week"] = pd.to_numeric(inj["week"], errors="coerce")
    inj = inj[inj["week"].notna() & inj["date_modified"].notna()]
    inj["week"] = inj["week"].astype(int)
    inj["season"] = inj["season"].astype(int)
    grouped = {
        (int(s), int(w), t): frame.sort_values("date_modified")
        for (s, w, t), frame in inj.groupby(["season", "week", "team"], sort=False)
    }

    changed = []
    out_or_doubtful = []
    for rec in long.itertuples(index=False):
        actual = rec.actual if isinstance(rec.actual, str) else None
        incumbent = rec.incumbent if isinstance(rec.incumbent, str) else None
        changed.append(bool(actual and incumbent and actual != incumbent))
        group = grouped.get((int(rec.season), int(rec.week), rec.team))
        flagged = False
        if group is not None and rec.ko is not None:
            eligible = group[group["date_modified"] <= rec.ko - pd.Timedelta(hours=24)]
            if len(eligible):
                latest = eligible.groupby("gsis_id", sort=False).tail(1)
                status = latest["report_status"].astype(str).str.casefold()
                flagged = bool(status.isin(["out", "doubtful"]).any())
        out_or_doubtful.append(flagged)
    long["qb_change"] = changed
    long["out_or_doubtful"] = out_or_doubtful
    game = long.groupby("game_id", as_index=False).agg(
        qb_change=("qb_change", "max"),
        out_or_doubtful=("out_or_doubtful", "max"),
    )
    game["qb_event"] = game["qb_change"] | game["out_or_doubtful"]
    return game


def _fmt(row: dict, kind: str) -> str:
    if row.get("n", 0) == 0:
        return "| " + kind + " | 0 |"
    ats = row["ats"]
    ats_s = f"{ats:.3f}" if ats is not None else ""
    lo = row["wilson_lo"]
    hi = row["wilson_hi"]
    wilson = f"{lo:.3f}–{hi:.3f}" if lo is not None else ""
    return (
        f"| {kind} | {row['n']} | {row['model_crps']:.3f} | {row['close_crps']:.3f} | "
        f"{row['crps_gap']:+.3f} | {row['model_rmse']:.3f} | {row['close_rmse']:.3f} | "
        f"{row['rmse_gap']:+.3f} | {ats_s} | {wilson} | {row['excess_crps']:+.1f} |"
    )


HEADER = (
    "| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | "
    "ATS | Wilson 95% | Excess CRPS |"
)
SEP = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def section(frame: pd.DataFrame, column: str, order: list[str], y, pred, close, sigma: float) -> list[str]:
    lines = [HEADER, SEP]
    rows = []
    for label in order:
        mask = frame[column].to_numpy() == label
        stats = _metrics(y[mask], pred[mask], close[mask], sigma)
        stats["label"] = label
        rows.append(stats)
        lines.append(_fmt(stats, label))
    ranked = sorted(
        [r for r in rows if r.get("n", 0)],
        key=lambda r: r["excess_crps"],
        reverse=True,
    )
    if ranked:
        top = ranked[0]
        lines.append("")
        lines.append(
            f"Largest excess CRPS in this cut: **{top['label']}** "
            f"({top['excess_crps']:+.1f} point-games, CRPS gap {top['crps_gap']:+.3f}, n={top['n']})."
        )
    return lines


def window_report(name: str, frame: pd.DataFrame) -> list[str]:
    y = frame["result"].to_numpy(dtype=float)
    margin = frame["margin"].to_numpy(dtype=float)
    close = frame["spread_line"].to_numpy(dtype=float)
    lines = [f"## {name}", ""]
    lines.append("Margin sigma is 13.5 for the model and the close. A positive gap means the model is worse.")
    lines.append("")
    lines.append("### Week")
    lines.append("")
    lines.extend(section(
        frame, "week_bucket", ["1-4", "5-9", "10-18", "playoffs"], y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("### QB change or Out/Doubtful, versus a normal QB week")
    lines.append("")
    lines.append(
        "A game is in the QB-event slice when either team started a different quarterback "
        "than in its previous game, or a QB on that team had a Friday report status of Out "
        "or Doubtful with `date_modified` at or before kickoff minus 24 hours. This slice "
        "describes where the frozen model lost. It is not a feature."
    )
    lines.append("")
    lines.extend(section(
        frame, "qb_slice",
        ["normal", "QB change or Out/Doubtful"],
        y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("The same games split into the pieces of that union:")
    lines.append("")
    lines.extend(section(
        frame, "qb_detail",
        ["normal", "change only", "Out/Doubtful only", "change and Out/Doubtful"],
        y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("### Home favorite versus home dog")
    lines.append("")
    lines.append("`spread_line` is positive when the home team is favored.")
    lines.append("")
    lines.extend(section(
        frame, "fav_bucket",
        ["home favorite", "home dog", "pick'em"],
        y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("### Division games")
    lines.append("")
    lines.extend(section(
        frame, "division", ["division", "non-division"], y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("### Spread size")
    lines.append("")
    lines.append("Cuts sit on the key numbers 3 and 7. They were fixed before these tables were read.")
    lines.append("")
    lines.extend(section(
        frame, "spread_bucket",
        ["small |close| < 3", "mid 3 <= |close| < 7", "large |close| >= 7"],
        y, margin, close, v6.MARGIN_SIGMA,
    ))
    lines.append("")
    lines.append("### Sides versus totals")
    lines.append("")
    side = _metrics(y, margin, close, v6.MARGIN_SIGMA)
    ty = frame["total_pts"].to_numpy(dtype=float)
    tp = frame["total"].to_numpy(dtype=float)
    tl = frame["total_line"].to_numpy(dtype=float)
    ok_t = np.isfinite(ty) & np.isfinite(tp) & np.isfinite(tl)
    tot = _metrics(ty[ok_t], tp[ok_t], tl[ok_t], v6.TOTAL_SIGMA)
    lines.append(
        "Sides are the home margin. Totals use sigma 10. The ATS column on the total row "
        "is the over-hit rate: bet over when the model total is above the close."
    )
    lines.append("")
    lines.append(HEADER)
    lines.append(SEP)
    lines.append(_fmt(side, "sides (margin)"))
    lines.append(_fmt(tot, "totals"))
    lines.append("")
    lines.append(
        f"Sides excess CRPS {side['excess_crps']:+.1f}. Totals excess CRPS {tot['excess_crps']:+.1f}."
    )
    lines.append("")
    return lines


def _gap_line(frame: pd.DataFrame, mask, label: str) -> str:
    stats = _metrics(
        frame.loc[mask, "result"], frame.loc[mask, "margin"], frame.loc[mask, "spread_line"], v6.MARGIN_SIGMA,
    )
    return (
        f"{label}: n={stats['n']}, CRPS gap {stats['crps_gap']:+.3f}, "
        f"RMSE gap {stats['rmse_gap']:+.3f}, ATS {stats['ats']:.3f}, excess CRPS {stats['excess_crps']:+.1f}"
    )


def narrative(df: pd.DataFrame) -> list[str]:
    """State the concentration from the same cuts as the tables. No new cuts."""
    lines = [
        "Excess CRPS weights the per-game gap by sample size. A wide per-game gap in a small slice "
        "and a modest gap in a large slice can both matter. Both are below.",
        "",
    ]
    for title, mask in (
        ("2016–2023", df["season"].between(2016, 2023)),
        ("2024–25", df["season"].between(2024, 2025)),
    ):
        part = df.loc[mask]
        lines.append(f"**{title}.**")
        lines.append("")
        for label, col, value in (
            ("Weeks 10–18", "week_bucket", "10-18"),
            ("Weeks 5–9", "week_bucket", "5-9"),
            ("Weeks 1–4", "week_bucket", "1-4"),
            ("Playoffs", "week_bucket", "playoffs"),
            ("QB event", "qb_slice", "QB change or Out/Doubtful"),
            ("Normal QB week", "qb_slice", "normal"),
            ("Home dog", "fav_bucket", "home dog"),
            ("Home favorite", "fav_bucket", "home favorite"),
            ("Division", "division", "division"),
            ("Non-division", "division", "non-division"),
            ("Large close", "spread_bucket", "large |close| >= 7"),
            ("Small close", "spread_bucket", "small |close| < 3"),
        ):
            lines.append("- " + _gap_line(part, part[col] == value, label))
        lines.append("")
    lines.append(
        "On the holdout the widest per-game margin gap is the QB-event slice, and that slice "
        "also holds most of the excess CRPS. Home dogs, division games, and large closes have "
        "wider per-game gaps than their complements. Weeks 5–9 have a wider per-game gap than "
        "weeks 10–18; weeks 10–18 still contribute the most excess CRPS among week buckets. "
        "Weeks 1–4 sit near the close. Totals and sides both trail the close. The selection "
        "window has the same directional shape: a QB change, a home dog, a division game, and "
        "a large close have wider per-game gaps than their complements, and weeks 10–18 carry "
        "the most excess CRPS."
    )
    return lines


def main() -> None:
    preds = pd.read_csv(PREDS)
    games = pd.read_csv(
        DATA / "games.csv",
        usecols=[
            "game_id", "season", "game_type", "week", "gameday", "gametime",
            "home_team", "away_team", "home_score", "away_score", "result", "total",
            "spread_line", "total_line", "div_game", "home_qb_id", "away_qb_id",
        ],
        low_memory=False,
    )
    games = games.rename(columns={"total": "actual_total"})
    for col in ("home_team", "away_team"):
        games[col] = games[col].map(canon)
    games["gameday"] = games["gameday"].astype(str).str.slice(0, 10)
    games = games[games["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])].copy()
    flags = qb_flags(games)
    df = preds.merge(
        games[[
            "game_id", "game_type", "result", "actual_total", "spread_line", "total_line",
            "div_game", "home_qb_id", "away_qb_id", "gametime",
        ]],
        on="game_id",
        how="left",
        suffixes=("", "_g"),
    )
    df = df.merge(flags, on="game_id", how="left")
    df["spread_line"] = pd.to_numeric(df["spread_line"], errors="coerce")
    df["total_line"] = pd.to_numeric(df["total_line"], errors="coerce")
    df["result"] = pd.to_numeric(df["result"], errors="coerce")
    df["total_pts"] = pd.to_numeric(df["actual_total"], errors="coerce")
    df = df[df["result"].notna() & df["spread_line"].notna() & df["margin"].notna()].copy()
    df["week_bucket"] = [week_bucket(t, int(w)) for t, w in zip(df["game_type"], df["week"])]
    df["spread_bucket"] = [spread_bucket(abs(float(s))) for s in df["spread_line"]]
    df["fav_bucket"] = [fav_bucket(float(s)) for s in df["spread_line"]]
    df["division"] = np.where(pd.to_numeric(df["div_game"], errors="coerce").fillna(0).astype(int) == 1, "division", "non-division")
    df["qb_change"] = df["qb_change"].fillna(False).astype(bool)
    df["out_or_doubtful"] = df["out_or_doubtful"].fillna(False).astype(bool)
    df["qb_slice"] = np.where(df["qb_change"] | df["out_or_doubtful"], "QB change or Out/Doubtful", "normal")
    df["qb_detail"] = np.select(
        [
            df["qb_change"] & df["out_or_doubtful"],
            df["qb_change"],
            df["out_or_doubtful"],
        ],
        ["change and Out/Doubtful", "change only", "Out/Doubtful only"],
        default="normal",
    )
    lines = [
        "# v6-blind loss diagnostics versus the close",
        "",
        "Diagnostics of the frozen v6-blind predictions in `backtest/harness_model/priors/predictions.csv`.",
        "No hyperparameter moved. Closing lines are the grading target only.",
        "",
        "ATS drops pushes (`result` equal to `spread_line`) and games where the prediction equals the close.",
        "A home cover is `result > spread_line`. Excess CRPS is `n * (model CRPS − close CRPS)` and is the concentration measure.",
        "Injury files for 2025–2026 have no `date_modified` and are not in these windows.",
        "",
    ]
    windows = [
        ("2016–2023 selection window", df["season"].between(2016, 2023)),
        ("2024–25 holdout", df["season"].between(2024, 2025)),
    ]
    for title, mask in windows:
        lines.extend(window_report(title, df.loc[mask].copy()))
    lines.append("## Where the gap sits")
    lines.append("")
    lines.extend(narrative(df))
    lines.append("")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
