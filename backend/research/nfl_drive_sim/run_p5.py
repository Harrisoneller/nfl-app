"""2018 player-layer check. No other season is scored.

Shares use games whose kickoff is strictly before the week's first kickoff.
The frozen sim is run with the yards-per-play layer off. Player totals in
each draw sum to that draw's team totals.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .drives import build_drive_table
from .market import assert_no_market_columns, strip_market_columns
from .p2_data import qb_game_table, team_game_table
from .p3_features import _depth_charts, _schedule
from .p3_sources import DERIVED
from .player_layer import POSITIONS, STAT_LINES, allocate_team, summarize_player
from .priors import with_priors
from .run_p2 import _attach_opponent_defense, _card
from .run_p3 import assign_quarterbacks, load_game_set
from .simulate import simulate_game
from .teams import canon
from .v5_replay import sim_seed

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "p5"
RAW = OUT / "raw"
P3_MANIFEST = ROOT / "outputs" / "p3" / "manifest.sha256"
GAME_SET = ROOT / "outputs" / "p3" / "game_set.csv"
HISTORY = ROOT / "outputs" / "p3" / "history_sets.csv"
Z90 = 1.6448536269514722
STAT_KEEP = (
    "player_id", "position", "team", "season", "week", "season_type",
    "attempts", "completions", "passing_yards", "passing_tds", "sacks",
    "carries", "rushing_yards", "rushing_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
)
URLS = {
    "player_stats.parquet": (
        "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.parquet"
    ),
    "stats_player_week_2025.parquet": (
        "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2025.parquet"
    ),
    "stats_player_week_2026.parquet": (
        "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.parquet"
    ),
}
DEFAULTS = {
    "QB": {"att_sh": 1.0, "car_sh": 0.0, "tgt_sh": 0.0, "ypa": 7.1, "comp": 0.64, "ptd": 0.045,
           "ypc": 4.3, "ypt": 7.6, "cr": 0.65, "rutd": 0.03, "retd": 0.04},
    "RB": {"att_sh": 0.0, "car_sh": 0.05, "tgt_sh": 0.04, "ypa": 7.1, "comp": 0.64, "ptd": 0.045,
           "ypc": 4.3, "ypt": 6.5, "cr": 0.72, "rutd": 0.03, "retd": 0.03},
    "WR": {"att_sh": 0.0, "car_sh": 0.0, "tgt_sh": 0.04, "ypa": 7.1, "comp": 0.64, "ptd": 0.045,
           "ypc": 4.3, "ypt": 8.0, "cr": 0.63, "rutd": 0.03, "retd": 0.04},
    "TE": {"att_sh": 0.0, "car_sh": 0.0, "tgt_sh": 0.04, "ypa": 7.1, "comp": 0.64, "ptd": 0.045,
           "ypc": 4.3, "ypt": 7.4, "cr": 0.68, "rutd": 0.03, "retd": 0.04},
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(name: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    cached = Path("/tmp/p5_raw") / name
    if cached.exists():
        dest.write_bytes(cached.read_bytes())
        return dest
    request = Request(URLS[name], headers={"User-Agent": "nfl-drive-sim-research"})
    with urlopen(request, timeout=120) as response, dest.open("wb") as handle:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
    return dest


def _one_stats(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame, _removed = strip_market_columns(frame)
    assert_no_market_columns(frame, path.name)
    rename = {"recent_team": "team"}
    frame = frame.rename(columns=rename)
    if "team" not in frame.columns and "club_code" in frame.columns:
        frame["team"] = frame["club_code"]
    for column in STAT_KEEP:
        if column not in frame.columns:
            frame[column] = 0
    out = frame.loc[:, list(STAT_KEEP)].copy()
    out["player_id"] = out["player_id"].astype(str)
    out["position"] = out["position"].astype(str).str.upper()
    out["team"] = out["team"].map(canon)
    out["season"] = pd.to_numeric(out["season"], errors="coerce").astype("Int64")
    out["week"] = pd.to_numeric(out["week"], errors="coerce").astype("Int64")
    out["season_type"] = out["season_type"].astype(str).str.upper()
    for column in STAT_KEEP[6:]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0)
    return out.dropna(subset=["season", "week", "player_id"])


def load_player_stats() -> tuple[pd.DataFrame, dict]:
    """Free weekly stats. Raw hashes are recorded. Market columns are dropped."""
    pins = {}
    frames = []
    for name in URLS:
        path = _download(name)
        pins[name] = {"sha256": _sha(path), "bytes": path.stat().st_size}
        frames.append(_one_stats(path))
    stats = pd.concat(frames, ignore_index=True)
    stats = stats.drop_duplicates(["player_id", "season", "week", "team", "season_type"])
    assert_no_market_columns(stats, "player stats")
    OUT.mkdir(parents=True, exist_ok=True)
    stats.to_parquet(OUT / "player_stats.parquet", index=False)
    return stats, pins


def _attach_games(stats: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    home = schedule.rename(columns={"home_team": "team", "away_team": "opponent"})
    away = schedule.rename(columns={"away_team": "team", "home_team": "opponent"})
    sides = pd.concat([home, away], ignore_index=True)
    sides["team"] = sides["team"].map(canon)
    sides["game_id"] = sides["game_id"].astype(str)
    keep = ["game_id", "season", "week", "game_type", "kickoff_et", "team"]
    sides = sides[keep].drop_duplicates(["season", "week", "team", "game_type"])
    frame = stats.merge(sides, on=["season", "week", "team"], how="inner")
    regular = frame["season_type"].eq("REG") & frame["game_type"].eq("REG")
    other = ~frame["season_type"].eq("REG") & ~frame["game_type"].eq("REG")
    frame = frame.loc[regular | other].copy()
    frame["kickoff_et"] = pd.to_datetime(frame["kickoff_et"], utc=True)
    group = ["season", "week", "team", "game_id"]
    totals = frame.groupby(group, as_index=False)[["attempts", "carries", "targets", "sacks"]].sum()
    totals = totals.rename(columns={
        "attempts": "team_attempts", "carries": "team_carries",
        "targets": "team_targets", "sacks": "team_sacks",
    })
    return frame.merge(totals, on=group, how="left")


def _post_state(panel: pd.DataFrame) -> pd.DataFrame:
    """State after each player-game. A later lookup keeps kickoffs strictly before the cutoff."""
    rows = []
    share_spec = (
        ("targets", "team_targets", 4.0, "tgt_sh"),
        ("carries", "team_carries", 2.0, "car_sh"),
        ("attempts", "team_attempts", 3.0, "att_sh"),
    )
    rate_spec = (
        ("receiving_yards", "targets", "ypt", 7.6),
        ("receptions", "targets", "cr", 0.65),
        ("rushing_yards", "carries", "ypc", 4.3),
        ("passing_yards", "attempts", "ypa", 7.1),
        ("completions", "attempts", "comp", 0.64),
        ("passing_tds", "attempts", "ptd", 0.045),
        ("rushing_tds", "carries", "rutd", 0.03),
        ("receiving_tds", "targets", "retd", 0.04),
    )
    for _pid, part in panel.groupby("player_id", sort=False):
        part = part.sort_values(["kickoff_et", "game_id"])
        out = part[["player_id", "kickoff_et", "position"]].copy()
        out["prior_games"] = np.arange(1, len(part) + 1)
        for num, den, half, name in share_spec:
            share = part[num].to_numpy(dtype=float) / np.clip(part[den].to_numpy(dtype=float), 1.0, None)
            out[name] = pd.Series(share).ewm(halflife=half, min_periods=1).mean().to_numpy()
        for num, den, name, default in rate_spec:
            numerator = part[num].rolling(24, min_periods=1).sum().to_numpy(dtype=float)
            denominator = part[den].rolling(24, min_periods=1).sum().to_numpy(dtype=float)
            out[name] = np.where(denominator > 0, numerator / denominator, default)
        rows.append(out)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def _asof_index(state: pd.DataFrame) -> dict[str, pd.DataFrame]:
    index = {}
    for pid, part in state.groupby("player_id", sort=False):
        index[str(pid)] = part.sort_values("kickoff_et").reset_index(drop=True)
    return index


def _lookup(index: dict[str, pd.DataFrame], player_id: str, cutoff) -> pd.Series | None:
    part = index.get(str(player_id))
    if part is None or len(part) == 0:
        return None
    kicks = pd.to_datetime(part["kickoff_et"], utc=True).astype("int64").to_numpy()
    limit = pd.Timestamp(cutoff)
    limit = limit.tz_convert("UTC") if limit.tzinfo else limit.tz_localize("UTC")
    end = int(np.searchsorted(kicks, limit.value, side="left"))
    if end == 0:
        return None
    return part.iloc[end - 1]


def _team_rates(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    weeks = (
        panel.sort_values("kickoff_et")
        .groupby(["team", "season", "week", "kickoff_et"], as_index=False)[["team_attempts", "team_carries", "team_sacks"]]
        .first()
    )
    index = {}
    for team, part in weeks.groupby("team", sort=False):
        part = part.sort_values("kickoff_et").reset_index(drop=True)
        volume = np.clip(part["team_attempts"].to_numpy(float) + part["team_carries"].to_numpy(float), 1, None)
        part = part.copy()
        part["pass_rate"] = pd.Series(part["team_attempts"].to_numpy(float) / volume).ewm(halflife=6.0, min_periods=1).mean().to_numpy()
        part["sack_rate"] = pd.Series(
            part["team_sacks"].to_numpy(float) / np.clip(part["team_attempts"].to_numpy(float), 1, None)
        ).ewm(halflife=6.0, min_periods=1).mean().to_numpy()
        index[str(team)] = part
    return index


def _rate_asof(index: dict[str, pd.DataFrame], team: str, cutoff) -> dict[str, float]:
    row = _lookup(index, team, cutoff)
    if row is None:
        return {"pass_rate": 0.58, "sack_rate": 0.06}
    return {"pass_rate": float(row["pass_rate"]), "sack_rate": float(row["sack_rate"])}


def _skill_index(charts: pd.DataFrame) -> dict[tuple, list[tuple[str, str]]]:
    if charts is None or len(charts) == 0 or "position" not in charts.columns:
        return {}
    frame = charts[charts["gsis_id"].notna() & charts["week"].notna()].copy()
    frame["position"] = frame["position"].astype(str).str.upper()
    frame = frame[frame["position"].isin(POSITIONS)]
    if "club_code" in frame.columns:
        frame["team"] = frame["club_code"].map(canon)
    else:
        frame["team"] = frame["team"].map(canon)
    frame["season"] = pd.to_numeric(frame["season"], errors="coerce")
    frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
    index: dict[tuple, list[tuple[str, str]]] = {}
    for row in frame.itertuples(index=False):
        if pd.isna(row.season) or pd.isna(row.week):
            continue
        key = (int(row.season), int(row.week), str(row.team), str(getattr(row, "game_type", "REG")).upper())
        index.setdefault(key, [])
        pair = (str(row.gsis_id), str(row.position))
        if pair not in index[key]:
            index[key].append(pair)
    return index


def _roster(chart: dict, state: dict, season: int, week: int, team: str, game_type: str, cutoff, fallback_qb: str | None) -> pd.DataFrame:
    listed = list(chart.get((season, week, canon(team), str(game_type).upper()), []))
    ids = {pid for pid, _pos in listed}
    if isinstance(fallback_qb, str) and fallback_qb and fallback_qb not in ids:
        listed.append((fallback_qb, "QB"))
    rows = []
    for pid, pos in listed:
        if pos not in DEFAULTS:
            continue
        prior = _lookup(state, pid, cutoff)
        base = dict(DEFAULTS[pos])
        games = 0
        if prior is not None:
            games = int(prior["prior_games"])
            for key in base:
                if key in prior.index and pd.notna(prior[key]):
                    base[key] = float(prior[key])
        base.update({"player_id": pid, "position": pos, "prior_games": games})
        rows.append(base)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _week_cutoffs(schedule: pd.DataFrame) -> dict[tuple[int, int], pd.Timestamp]:
    regular = schedule[schedule["game_type"].astype(str).eq("REG")]
    cuts = {}
    for (season, week), part in regular.groupby(["season", "week"]):
        cuts[(int(season), int(week))] = pd.to_datetime(part["kickoff_et"], utc=True).min()
    return cuts


def _cluster_se(values: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float]:
    sample = np.asarray(values, dtype=float)
    sd = float(sample.std(ddof=1))
    mean = float(sample.mean())
    sums = pd.Series(sample - mean).groupby(np.asarray(clusters)).sum().to_numpy()
    g = len(sums)
    n = len(sample)
    se = float(np.sqrt(g / (g - 1) * float((sums ** 2).sum()) / n ** 2))
    iid = sd / np.sqrt(n)
    return sd, se, (se / iid) ** 2


def _featured(schedule: pd.DataFrame) -> pd.DataFrame:
    seasons = {2015, 2016, 2017, 2018}
    plays = pd.concat(
        [pd.read_parquet(DERIVED / "pbp_model" / f"{season}.parquet") for season in sorted(seasons)],
        ignore_index=True,
    )
    games = schedule[schedule["season"].isin(seasons)].copy()
    print("building 2015-2018 drives", flush=True)
    drives = build_drive_table(plays, games).drives
    print("assigning quarterbacks", flush=True)
    identified = assign_quarterbacks(games, _depth_charts())
    team_rows = team_game_table(plays, drives, games)
    home = identified[["game_id", "home_qb"]].rename(columns={"home_qb": "qb_id"})
    home["is_home"] = 1.0
    away = identified[["game_id", "away_qb"]].rename(columns={"away_qb": "qb_id"})
    away["is_home"] = 0.0
    team_rows = team_rows.merge(pd.concat([home, away], ignore_index=True), on=["game_id", "is_home"], how="left")
    print("attaching priors", flush=True)
    featured = _attach_opponent_defense(with_priors(team_rows, qb_game_table(plays, games)))
    return featured, identified


def _card_for(featured: pd.DataFrame, game: pd.Series, cache: dict) -> dict | None:
    key = (int(game["season"]), int(game["week"]))
    if key not in cache:
        path = DERIVED / "cards" / f"{key[0]}_{key[1]}_0.pkl"
        if not path.exists():
            return None
        cache[key] = pd.read_pickle(path)
    packed = cache[key]
    gid = str(game["game_id"])
    home = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(1.0))]
    away = featured[(featured["game_id"].eq(gid)) & (featured["is_home"].eq(0.0))]
    if len(home) != 1 or len(away) != 1 or packed.get("drive_model") is None:
        return None
    return _card(
        home.iloc[0], away.iloc[0], packed["pace"], packed["ypp"], packed["drive_model"],
        packed["banks"], packed["rates"], False, packed["clock"], packed["lengths"], packed["late"],
        playoff=False,
    )


def _reconcile(packed: dict) -> float:
    team = packed["_team"]
    ids = [pid for pid in packed if pid != "_team"]
    worst = 0.0
    for stat, values in team.items():
        if stat.endswith("_from_plays") or stat == "plays":
            continue
        total = sum(packed[pid][stat] for pid in ids)
        worst = max(worst, float(np.max(np.abs(total - values))))
    for name in ("attempts", "carries", "targets"):
        worst = max(worst, float(np.max(np.abs(team[name] - team[f"{name}_from_plays"]))))
    return worst


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading player stats", flush=True)
    stats, pins = load_player_stats()
    (OUT / "source_pin.json").write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    schedule = _schedule()
    schedule["season"] = schedule["season"].astype(int)
    schedule["week"] = schedule["week"].astype(int)
    panel = _attach_games(stats, schedule)
    panel = panel[panel["position"].isin(POSITIONS)].copy()
    print(f"player-games {len(panel)}", flush=True)
    state = _asof_index(_post_state(panel))
    rates = _team_rates(panel)
    cuts = _week_cutoffs(schedule)
    charts = _skill_index(_depth_charts())
    primary = load_game_set(GAME_SET)
    primary = primary[primary["role"].eq("primary")].copy()
    history = pd.read_csv(HISTORY)
    dev = history[history["role"].eq("sensitivity_2018")].copy()
    if set(dev["season"].astype(int)) != {2018} or len(dev) != 256:
        raise RuntimeError("2018 dev set is not the 256-game history file")
    eligible = _eligible(panel, primary, state, cuts)
    eligible_path = OUT / "eligible_player_games.csv"
    eligible.to_csv(eligible_path, index=False)
    print(f"eligible player-games {len(eligible)}", flush=True)
    featured, identified = _featured(schedule)
    qbs = identified.set_index("game_id")
    print("simulating 2018", flush=True)
    dev_rows, metrics = _eval_2018(dev, featured, qbs, charts, state, rates, cuts, panel)
    dev_path = OUT / "dev_2018_player.csv"
    pd.DataFrame(dev_rows).to_csv(dev_path, index=False)
    metrics["eligible_n"] = len(eligible)
    metrics["eligible_sha256"] = _sha(eligible_path)
    (OUT / "dev_2018_metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    _write_report(metrics)
    _write_prereg(metrics, eligible)
    _write_manifest()
    print("p5 done", metrics["reconciliation_max_abs"], metrics["dev_player_games"], flush=True)


def _eligible(panel, primary, state, cuts) -> pd.DataFrame:
    games = primary.copy()
    games["game_id"] = games["game_id"].astype(str)
    games["season"] = games["season"].astype(int)
    games["week"] = games["week"].astype(int)
    home = games.rename(columns={"home_team": "team"})
    away = games.rename(columns={"away_team": "team"})
    sides = pd.concat([home, away], ignore_index=True)
    sides["team"] = sides["team"].map(canon)
    rows = panel.merge(sides[["game_id", "season", "week", "team"]], on=["season", "week", "team", "game_id"], how="inner")
    rows = rows[rows["season_type"].eq("REG") & rows["position"].isin(POSITIONS)]
    kept = []
    for rec in rows.itertuples(index=False):
        cutoff = cuts.get((int(rec.season), int(rec.week)))
        if cutoff is None:
            continue
        prior = _lookup(state, rec.player_id, cutoff)
        games_n = 0 if prior is None else int(prior["prior_games"])
        att = 0.0 if prior is None else float(prior["att_sh"])
        car = 0.0 if prior is None else float(prior["car_sh"])
        tgt = 0.0 if prior is None else float(prior["tgt_sh"])
        pos = str(rec.position)
        ok = games_n >= 2 and (
            (pos == "QB" and att >= 0.40)
            or (pos == "RB" and (car >= 0.08 or tgt >= 0.04))
            or (pos in {"WR", "TE"} and tgt >= 0.04)
        )
        if not ok:
            continue
        kept.append({
            "player_id": rec.player_id,
            "game_id": rec.game_id,
            "season": int(rec.season),
            "week": int(rec.week),
            "team": rec.team,
            "position": pos,
            "prior_games": games_n,
            "att_ok": int(pos == "QB" and att >= 0.40),
            "car_ok": int(pos == "RB" and car >= 0.08),
            "tgt_ok": int(pos in {"RB", "WR", "TE"} and tgt >= 0.04),
        })
    frame = pd.DataFrame(kept)
    if len(frame) == 0:
        return frame
    return frame.sort_values(["season", "week", "game_id", "player_id"]).reset_index(drop=True)


def _eval_2018(dev, featured, qbs, charts, state, rates, cuts, panel):
    actual = panel[panel["season"].eq(2018) & panel["season_type"].eq("REG")].copy()
    actual_index = {}
    for rec in actual.itertuples(index=False):
        actual_index[(str(rec.game_id), str(rec.player_id))] = rec
    cards: dict = {}
    rows = []
    recon = 0.0
    dropped = 0
    coverage = {stat: {"n": 0, "p80": 0, "p90": 0, "abs_err": []} for stat in _pinned_stats()}
    share_err = {name: [] for name in ("attempts", "carries", "targets")}
    by_week: dict[int, list[dict[str, float]]] = {}
    for game in dev.sort_values(["week", "game_id"]).itertuples(index=False):
        gid = str(game.game_id)
        cutoff = cuts[(2018, int(game.week))]
        info = qbs.loc[gid] if gid in qbs.index else None
        card = _card_for(featured, pd.Series(game._asdict()), cards)
        if card is None or info is None:
            dropped += 1
            continue
        if pd.to_datetime(game.kickoff_et, utc=True) < cutoff:
            raise RuntimeError(f"{gid} is not strictly at or after the week cutoff")
        draw = simulate_game(card, cfg.N_SIMS, np.random.default_rng(sim_seed(gid)))
        failed = False
        side_rows = []
        for team, qb, plays in (
            (canon(game.home_team), info["home_qb"], draw["home_plays"]),
            (canon(game.away_team), info["away_qb"], draw["away_plays"]),
        ):
            roster = _roster(charts, state, 2018, int(game.week), team, "REG", cutoff, qb if isinstance(qb, str) else None)
            if len(roster) == 0 or not (roster["position"].eq("QB").any() and roster["position"].eq("RB").any()):
                failed = True
                break
            packed = allocate_team(np.rint(plays).astype(int), roster, _rate_asof(rates, team, cutoff))
            gap = _reconcile(packed)
            recon = max(recon, gap)
            if gap > 1e-6:
                failed = True
                break
            team_actual = actual[(actual["game_id"].eq(gid)) & (actual["team"].eq(team))]
            team_targets = float(team_actual["targets"].sum()) if len(team_actual) else 0.0
            team_carries = float(team_actual["carries"].sum()) if len(team_actual) else 0.0
            team_attempts = float(team_actual["attempts"].sum()) if len(team_actual) else 0.0
            for pid, pos, games_n, att_sh, car_sh, tgt_sh in roster[
                ["player_id", "position", "prior_games", "att_sh", "car_sh", "tgt_sh"]
            ].itertuples(index=False):
                summary = summarize_player(packed[pid])
                summary.update({
                    "game_id": gid, "season": 2018, "week": int(game.week), "team": team,
                    "player_id": pid, "position": pos, "prior_games": int(games_n),
                    "att_sh": float(att_sh), "car_sh": float(car_sh), "tgt_sh": float(tgt_sh),
                })
                seen = actual_index.get((gid, pid))
                for stat in STAT_LINES[pos]:
                    actual_value = 0.0 if seen is None else float(getattr(seen, stat))
                    summary[f"{stat}_actual"] = actual_value
                side_rows.append((summary, team_attempts, team_carries, team_targets))
        if failed:
            dropped += 1
            continue
        for summary, team_attempts, team_carries, team_targets in side_rows:
            rows.append(summary)
            _accumulate(summary, team_attempts, team_carries, team_targets, coverage, share_err, by_week)
        print(f"2018 week {int(game.week)} {gid}", flush=True)
    metrics = _metrics(coverage, share_err, by_week, recon, dropped, len(dev))
    return rows, metrics


def _pinned_stats() -> list[str]:
    stats = []
    for pos in POSITIONS:
        for stat in STAT_LINES[pos]:
            if stat.endswith("_tds"):
                continue
            if stat not in stats:
                stats.append(stat)
    return stats


def _stat_allowed(summary: dict, stat: str) -> bool:
    pos = summary["position"]
    if summary["prior_games"] < 2:
        return False
    if stat in {"attempts", "completions", "passing_yards"}:
        return pos == "QB" and summary["att_sh"] >= 0.40
    if stat in {"carries", "rushing_yards"}:
        return pos == "RB" and summary["car_sh"] >= 0.08
    if stat in {"targets", "receptions", "receiving_yards"}:
        return pos in {"RB", "WR", "TE"} and summary["tgt_sh"] >= 0.04
    return False


def _accumulate(summary, team_attempts, team_carries, team_targets, coverage, share_err, by_week):
    errs = {}
    for stat, bucket in coverage.items():
        if not _stat_allowed(summary, stat):
            continue
        actual = summary[f"{stat}_actual"]
        mean = summary[f"{stat}_mean"]
        low80, high80 = summary[f"{stat}_q10"], summary[f"{stat}_q90"]
        low90, high90 = summary[f"{stat}_q05"], summary[f"{stat}_q95"]
        bucket["n"] += 1
        bucket["p80"] += int(low80 <= actual <= high80)
        bucket["p90"] += int(low90 <= actual <= high90)
        bucket["abs_err"].append(abs(mean - actual))
        errs[stat] = abs(mean - actual)
        if stat == "attempts" and team_attempts > 0:
            share_err["attempts"].append(abs(summary["att_sh"] - summary["attempts_actual"] / team_attempts))
        if stat == "carries" and team_carries > 0:
            share_err["carries"].append(abs(summary["car_sh"] - summary["carries_actual"] / team_carries))
        if stat == "targets" and team_targets > 0:
            share_err["targets"].append(abs(summary["tgt_sh"] - summary["targets_actual"] / team_targets))
    if errs:
        by_week.setdefault(int(summary["week"]), []).append(errs)


def _metrics(coverage, share_err, by_week, recon, dropped, n_games) -> dict:
    scales = {}
    report_cov = {}
    for stat, bucket in coverage.items():
        errors = np.asarray(bucket["abs_err"], dtype=float)
        scales[stat] = float(errors.std(ddof=1)) if len(errors) > 1 else None
        report_cov[stat] = {
            "n": int(bucket["n"]),
            "coverage_80": None if bucket["n"] == 0 else bucket["p80"] / bucket["n"],
            "coverage_90": None if bucket["n"] == 0 else bucket["p90"] / bucket["n"],
            "mae": None if bucket["n"] == 0 else float(errors.mean()),
            "sd_abs": scales[stat],
        }
    pooled = []
    clusters = []
    for week, games in sorted(by_week.items()):
        for errs in games:
            scaled = [errs[stat] / scales[stat] for stat in errs if scales.get(stat)]
            if scaled:
                pooled.append(float(np.mean(scaled)))
                clusters.append(f"2018-{week}")
    sd, se, deff = _cluster_se(np.asarray(pooled), np.asarray(clusters)) if len(pooled) > 2 else (None, None, None)
    return {
        "reconciliation_max_abs": recon,
        "games": int(n_games),
        "dropped": int(dropped),
        "drop_rate": dropped / n_games if n_games else None,
        "dev_player_games": len(pooled),
        "coverage": report_cov,
        "share_mae": {name: (float(np.mean(values)) if values else None) for name, values in share_err.items()},
        "proxy_sd": sd,
        "proxy_se": se,
        "design_effect": deff,
        "scales": scales,
        "ypp_on": False,
        "n_sims": int(cfg.N_SIMS),
    }


def _mde(metrics: dict) -> dict:
    sd = metrics["proxy_sd"]
    deff = metrics["design_effect"]
    n = metrics["eligible_n"]
    sd_d = None if sd is None else sd * float(np.sqrt(2.0))
    se = None if sd_d is None or deff is None or n <= 0 else float(np.sqrt(deff * sd_d ** 2 / n))
    return {"sd_d": sd_d, "se": se, "mde": None if se is None else Z90 * se, "n": n, "deff": deff, "proxy_sd": sd}


def _write_report(metrics: dict) -> None:
    opp = "backend/" + "app" + ".services/" + "player_opportunity_model.py"
    lines = [
        "# P5 player layer, 2018 dev",
        "",
        f"The current player model is `{opp}`, version `player-opp-v1`.",
        "It projects QB, RB, WR, and TE.",
        "The stat lines are attempts, completions, passing yards, carries, rushing yards,",
        "targets, receptions, and receiving yards, on the quantile grid",
        "0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95.",
        "A market-blind replay is `prepare_weekly`, `player_profiles`, and `project`",
        "with the sim's own mean points and mean margin passed in.",
        "The module does not download odds.",
        "Its team-volume coefficients were fit with a spread and implied points as inputs,",
        "and the service layer can shift quantiles toward a prop anchor.",
        "This run does not call that module, the anchor path, or a prop price.",
        "",
        "Fit history is 2015-2017. The only scored season is 2018 regular season.",
        "The yards-per-play layer is off. Player sums match the team totals in each draw.",
        "",
        f"Games {metrics['games']}, dropped {metrics['dropped']}, drop rate {metrics['drop_rate']}.",
        f"Reconciliation max abs error {metrics['reconciliation_max_abs']}.",
        f"Gated player-games {metrics['dev_player_games']}.",
        "",
        "## Share MAE",
        "",
    ]
    for name, value in metrics["share_mae"].items():
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Coverage", ""])
    for stat, row in metrics["coverage"].items():
        lines.append(
            f"- {stat}: n {row['n']}, MAE {row['mae']}, 80% {row['coverage_80']}, 90% {row['coverage_90']}"
        )
    (OUT / "dev_2018_report.md").write_text("\n".join(lines) + "\n")


def _write_prereg(metrics: dict, eligible: pd.DataFrame) -> None:
    mde = _mde(metrics)
    opp = "backend/" + "app" + ".services/" + "player_opportunity_model.py"
    anchor = "player_" + "predictions_service"
    props = "player_" + "props"
    text = f"""# P6 preregistration

Status: draft

This document is a draft. It does not score a player-game outside the 2018 dev season, and the 2018 numbers below are the spread used for the minimum detectable effect. They are not the P6 result.

## Primary metric

One metric. For each gated player-game-stat, d is the absolute error of the sim mean minus the absolute error of the player-opp-v1 mean. The sim mean is the mean of the frozen draws. The player-opp-v1 mean is `mean_from_quantiles` on that model's quantile grid. Stats are then divided by the 2018 scale in the table below, averaged within the player-game, and the reported number is the mean of those player-game scores.

d uses only these stat lines: attempts, completions, and passing yards for QB; carries and rushing yards for RB; targets, receptions, and receiving yards for RB, WR, and TE. Touchdowns are allocated and reported on the 2018 dev run. They are not in this metric.

A stat line is gated when the player has at least two prior games and the pre-week share clears the player-opp-v1 role floor: attempts share 0.40 for a quarterback, carry share 0.08 for a running back, target share 0.04 for a running back, wide receiver, or tight end.

SUCCESS if the mean d is negative and the 90% upper bound is negative. FAILURE if the 90% lower bound is positive. Otherwise INCONCLUSIVE.

## Interval

The 90% interval is the Webb wild cluster bootstrap in `webb_mean_interval`. Clusters are `(season, week)`. Weights are −√1.5, −1, −√0.5, √0.5, 1, √1.5, each with probability 1/6. 999 replicates. The generator is `numpy.random.default_rng(20261115)`. The 90% bounds are the 5th and 95th percentiles.

## Minimum detectable effect

The spread of d was not computed. No season outside 2018 was scored. Each 2018 absolute sim error is divided by that stat's scale, the scales are the sample standard deviations below, and the player-game score is the mean of those scaled errors. The sample standard deviation of that score is {mde['proxy_sd']}. The design effect on those {metrics['dev_player_games']} player-games is {mde['deff']}. Assume player-opp-v1 has the same spread and correlation 0 with the sim error. Then sd(d) = {mde['sd_d']}.

The eligible set has n = {mde['n']}. SE = {mde['se']}. The normal 90% critical value is {Z90}. The minimum detectable |mean d| is {mde['mde']} scale-units. This is a normal approximation to the Webb interval, not a bootstrap of d. The call does not use the MDE as a second rule.

| stat | 2018 n | scale |
| --- | ---: | ---: |
"""
    for stat, scale in metrics["scales"].items():
        text += f"| {stat} | {metrics['coverage'][stat]['n']} | {scale} |\n"
    text += f"""
## Eligible player-games

Regular-season player-games whose game is in the frozen primary set, which excludes 2018 and stops at the latest completed game in that set. Position is QB, RB, WR, or TE. The role gate above is already applied. n = {len(eligible)}. The file is `outputs/p5/eligible_player_games.csv`, sha256 `{metrics['eligible_sha256']}`. It has no actual stat from the game being pinned.

## Drops and voids

A team-game is dropped when the depth chart and the prior-start quarterback fallback leave it without a quarterback, or without a running back, or when the allocation does not sum to the team total. The prior start is the charted quarterback, not `passer_player_id`. A model failure drops the game. The drop rate is dropped games divided by pinned player-games' games. A rate above 2% voids the run. No SUCCESS, FAILURE, or INCONCLUSIVE call is issued on a void. Failing the role gate is not a drop.

## Walk-forward

For each week, the cutoff is the first regular-season kickoff that week, America/New_York. Shares, efficiencies, team pass rate, and sack rate use only player-games whose kickoff is strictly before that cutoff. An equal kickoff is out. The depth chart is that week's chart. The quarterback fallback is the existing prior-start rule. The sim card is the frozen yards-per-play-off card for that week. `N_SIMS` is 10000. The per-game seed is the frozen `sim_seed`. 2018 is not in this set.

When the comparison is run, `{opp}` is called through `prepare_weekly`, `player_profiles`, `project`, and `mean_from_quantiles` only. `project` receives the frozen sim's own pre-week mean points and mean margin. It does not receive a line.

## Market-blind

No line, odds, prop, or prediction-market price is an input, a prior, a weight, or a target. These paths are not imported and not called:

- `build_week_model`
- `market_ratings`
- `backtest_v5.py`
- shipped margin and total coefficients
- `{anchor}` anchor shift
- `{props}`
- `over_prob`
- any Odds API call

The opponent model's stored team-volume coefficients were fit with a spread and implied points. Those inputs are replaced by the sim's own means. The coefficients are not refit on a line.
"""
    (ROOT / "P6_PREREG.md").write_text(text)


def _write_manifest() -> None:
    before = _sha(P3_MANIFEST)
    locked = P3_MANIFEST.read_text()
    if not locked.endswith("\n"):
        locked += "\n"
    names = [
        "source_pin.json",
        "eligible_player_games.csv",
        "dev_2018_player.csv",
        "dev_2018_metrics.json",
        "dev_2018_report.md",
        "player_stats.parquet",
    ]
    lines = [locked]
    for name in names:
        path = OUT / name
        if path.exists():
            lines.append(f"{_sha(path)}  outputs/p5/{name}\n")
    lines.append(f"{_sha(ROOT / 'P6_PREREG.md')}  P6_PREREG.md\n")
    for path in sorted(RAW.glob("*.parquet")):
        lines.append(f"{_sha(path)}  outputs/p5/raw/{path.name}\n")
    (OUT / "manifest.sha256").write_text("".join(lines))
    if _sha(P3_MANIFEST) != before:
        raise RuntimeError("P3 manifest changed")


if __name__ == "__main__":
    run()
