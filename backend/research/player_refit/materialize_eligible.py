"""Materialize the pre-kickoff eligible player-game set.

This does not fit a model and does not score a stat. It reads public nflverse
schedules and depth charts only. The Odds API is not called.

The roster rule is the quarterback rule, applied to every skill-position
player the chart lists:

- A weekly depth chart is treated as known one hour before kickoff. It has
  no publication timestamp. That is the same assumption as
  ``pregame_quarterback`` on ``research/nfl-drive-sim``.
- A snapshot chart (2025) uses the latest ``dt`` strictly before kickoff.
- If neither chart exists, the roster is the resolved roster of that team's
  most recent completed game whose kickoff is strictly earlier. That prior
  start is the charted player, not the player who took the snap.
- A chart that has skill players but no QB1 still takes the prior charted
  QB1, which is ``resolve_qb`` in ``v5_replay.py``.

2018 games are walked so 2019 has a prior start. They are not written out.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
SCHEDULE_URL = RELEASE + "/schedules/games.csv"
DEPTH_URL = RELEASE + "/depth_charts/depth_charts_{season}.parquet"
EASTERN = ZoneInfo("America/New_York")
ASOF_LEAD = timedelta(hours=1)
SKILL = ("QB", "RB", "WR", "TE")
SKILL_SET = set(SKILL)
POSITION_RANK = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
# Franchise codes, so a prior start survives OAK -> LV and SD -> LAC.
ALIASES = {"JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
CHAIN_SEASONS = range(2015, 2026)
SCORED_SEASONS = {2016, 2017, 2019, 2020, 2021, 2022, 2023, 2024, 2025}
COMPLETED_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
HERE = Path(__file__).resolve().parent
OUT = HERE / "eligible"


def canon(team: object) -> str:
    text = "" if team is None or (isinstance(team, float) and pd.isna(team)) else str(team).strip().upper()
    return ALIASES.get(text, text)


def kickoff_et(gameday: object, gametime: object) -> datetime | None:
    """nflverse date and clock as an America/New_York instant. A missing clock is 13:00."""
    if gameday is None or (isinstance(gameday, float) and pd.isna(gameday)):
        return None
    day = str(gameday)[:10]
    clock = "13:00"
    if gametime is not None and not (isinstance(gametime, float) and pd.isna(gametime)):
        text = str(gametime).strip()
        if text and text.lower() not in {"nan", "nat", "none"}:
            clock = text[:5]
    hour, minute = (int(part) for part in clock.split(":")[:2])
    year, month, dom = (int(part) for part in day.split("-"))
    return datetime(year, month, dom, hour, minute, tzinfo=EASTERN)


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _download(url: str, dest: Path) -> str:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        urllib.request.urlretrieve(url, dest)  # noqa: S310 — public nflverse release, no secrets
    return _sha_file(dest)


def _depth_rank(value: object) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return int(number)


def _collapse(rows: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """One row per player. The lowest depth rank wins. A tie keeps QB, then RB, WR, TE."""
    best: dict[str, tuple[str, int]] = {}
    for player_id, position, depth in rows:
        current = best.get(player_id)
        rank = (depth, POSITION_RANK[position])
        if current is None or rank < (current[1], POSITION_RANK[current[0]]):
            best[player_id] = (position, depth)
    return [(player_id, position, depth) for player_id, (position, depth) in best.items()]


def _weekly_index(frame: pd.DataFrame) -> dict[tuple, list[tuple[str, str, int]]]:
    if frame is None or len(frame) == 0 or "week" not in frame.columns:
        return {}
    work = frame[frame["gsis_id"].notna() & frame["week"].notna()].copy()
    work["position"] = work["position"].astype(str).str.upper().str.strip()
    work = work[work["position"].isin(SKILL_SET)]
    depth_position = work["depth_position"].astype(str).str.strip().str.upper()
    # A kick returner row is not a second roster spot. A blank depth position
    # keeps the position column.
    keep = depth_position.eq("") | depth_position.eq("NAN") | depth_position.isin(SKILL_SET)
    work = work[keep]
    index: dict[tuple, list[tuple[str, str, int]]] = {}
    for row in work.itertuples(index=False):
        depth = _depth_rank(row.depth_team)
        if depth is None or pd.isna(row.season) or pd.isna(row.week):
            continue
        team = canon(row.club_code)
        week_key = (int(row.season), int(row.week), team)
        typed = week_key + (str(row.game_type).upper(),)
        player = (str(row.gsis_id), str(row.position).upper(), depth)
        index.setdefault(typed, []).append(player)
        index.setdefault(week_key, []).append(player)
    return {key: _collapse(rows) for key, rows in index.items()}


def _snapshot_index(frame: pd.DataFrame) -> dict[str, list[tuple[datetime, list[tuple[str, str, int]]]]]:
    if frame is None or len(frame) == 0 or "dt" not in frame.columns:
        return {}
    work = frame[frame["gsis_id"].notna() & frame["dt"].notna() & frame["team"].notna()].copy()
    work["pos_abb"] = work["pos_abb"].astype(str).str.upper().str.strip()
    work = work[work["pos_abb"].isin(SKILL_SET)]
    grouped: dict[tuple[str, datetime], list[tuple[str, str, int]]] = {}
    for row in work.itertuples(index=False):
        stamp = pd.Timestamp(row.dt)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        when = stamp.tz_convert(EASTERN).to_pydatetime()
        depth = _depth_rank(row.pos_rank)
        if depth is None:
            continue
        grouped.setdefault((canon(row.team), when), []).append((str(row.gsis_id), str(row.pos_abb), depth))
    index: dict[str, list[tuple[datetime, list[tuple[str, str, int]]]]] = {}
    for (team, when), rows in grouped.items():
        index.setdefault(team, []).append((when, _collapse(rows)))
    for snaps in index.values():
        snaps.sort(key=lambda item: item[0])
    return index


def _lookup_weekly(index: dict, season: int, week: int, team: str, game_type: str):
    typed = (season, week, team, game_type)
    if typed in index:
        return index[typed]
    return index.get((season, week, team))


def _lookup_snapshot(index: dict, team: str, kickoff: datetime):
    snaps = index.get(team) or []
    chosen = None
    for when, rows in snaps:
        if when < kickoff:
            chosen = rows
        else:
            break
    return chosen


def _qb1(rows: list[tuple[str, str, int]]) -> str | None:
    starters = [player_id for player_id, position, depth in rows if position == "QB" and depth == 1]
    if not starters:
        return None
    return sorted(starters)[0]


def _with_prior_qb(
    rows: list[tuple[str, str, int]],
    prior_qb: str | None,
    source: str,
) -> list[tuple[str, str, int, str]]:
    tagged = [(player_id, position, depth, source) for player_id, position, depth in rows]
    if _qb1(rows) is not None or not prior_qb:
        return tagged
    if any(player_id == prior_qb for player_id, *_rest in tagged):
        return tagged
    tagged.append((prior_qb, "QB", 1, "prior_start"))
    return tagged


def build(schedule: pd.DataFrame, weekly: dict, snapshots: dict) -> tuple[pd.DataFrame, dict]:
    games = schedule[schedule["game_type"].astype(str).isin(COMPLETED_TYPES)].copy()
    games["home_team"] = games["home_team"].map(canon)
    games["away_team"] = games["away_team"].map(canon)
    games["kickoff_et"] = [kickoff_et(day, clock) for day, clock in zip(games["gameday"], games["gametime"])]
    games = games[games["kickoff_et"].notna()]
    games = games[games["season"].isin(list(CHAIN_SEASONS))]
    scores = games["home_score"].map(pd.to_numeric, errors="coerce")
    away = games["away_score"].map(pd.to_numeric, errors="coerce")
    games = games[scores.notna() & away.notna()].copy()
    games = games.sort_values(["kickoff_et", "game_id"], kind="mergesort")

    last_rows: dict[str, list[tuple[str, str, int]]] = {}
    last_qb: dict[str, list[tuple[datetime, str]]] = {}
    emitted: list[dict] = []
    empty_team_games = 0
    scored_team_games = 0

    for game in games.itertuples(index=False):
        kick = game.kickoff_et
        season = int(game.season)
        week = int(game.week)
        game_type = str(game.game_type).upper()
        scored = game_type == "REG" and season in SCORED_SEASONS
        for team in (str(game.home_team), str(game.away_team)):
            chart = _lookup_weekly(weekly, season, week, team, game_type)
            source = "depth_chart"
            if chart is None:
                chart = _lookup_snapshot(snapshots, team, kick)
                source = "snapshot"
            prior = [(stamp, qb) for stamp, qb in last_qb.get(team, []) if stamp < kick]
            prior_qb = max(prior, key=lambda item: item[0])[1] if prior else None
            if chart:
                resolved = _with_prior_qb(chart, prior_qb, source)
            else:
                previous = last_rows.get(team) or []
                resolved = [(player_id, position, depth, "prior_start") for player_id, position, depth in previous]
                source = "prior_start"
            if scored:
                scored_team_games += 1
                if not resolved:
                    empty_team_games += 1
                for player_id, position, depth, row_source in resolved:
                    emitted.append({
                        "season": season,
                        "week": week,
                        "game_id": str(game.game_id),
                        "team": team,
                        "player_id": player_id,
                        "position": position,
                        "depth_team": depth,
                        "source": row_source,
                    })
            stored = [(player_id, position, depth) for player_id, position, depth, _source in resolved]
            if stored:
                last_rows[team] = stored
            qb = _qb1(stored)
            if qb:
                last_qb.setdefault(team, []).append((kick, qb))

    frame = pd.DataFrame(emitted)
    if len(frame):
        frame = frame.drop_duplicates(["game_id", "team", "player_id"]).sort_values(
            ["season", "week", "game_id", "team", "player_id"],
        )
        frame = frame.reset_index(drop=True)
    summary = {
        "n": int(len(frame)),
        "scored_team_games": scored_team_games,
        "empty_team_games": empty_team_games,
        "by_source": frame["source"].value_counts().to_dict() if len(frame) else {},
        "by_position": frame["position"].value_counts().to_dict() if len(frame) else {},
        "seasons": sorted(int(s) for s in frame["season"].unique()) if len(frame) else [],
    }
    return frame, summary


def write_outputs(frame: pd.DataFrame, summary: dict, pins: dict) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "eligible_player_games.csv"
    frame.to_csv(csv_path, index=False)
    keys = [
        f"{int(row.season)}|{int(row.week)}|{row.game_id}|{row.team}|{row.player_id}|{row.position}"
        for row in frame.itertuples(index=False)
    ]
    keys = sorted(set(keys))
    key_path = OUT / "eligible_keys.txt"
    key_path.write_text("".join(f"{key}\n" for key in keys))
    digest = _sha_file(key_path)
    summary = {
        **summary,
        "n": len(keys),
        "sha256": digest,
        "key_format": "season|week|game_id|team|player_id|position",
        "sources": pins,
    }
    (OUT / "eligible_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main() -> None:
    cache = Path("/tmp/nflverse-elig")
    cache.mkdir(parents=True, exist_ok=True)
    schedule_path = cache / "games.csv"
    pins = {"schedule": {"url": SCHEDULE_URL, "sha256": _download(SCHEDULE_URL, schedule_path)}}
    frames = []
    depth_pins = []
    for season in CHAIN_SEASONS:
        url = DEPTH_URL.format(season=season)
        path = cache / f"depth_{season}.parquet"
        depth_pins.append({"season": season, "url": url, "sha256": _download(url, path)})
        frames.append(pd.read_parquet(path))
    pins["depth_charts"] = depth_pins
    weekly_frames = [frame for frame in frames if "week" in frame.columns]
    snapshot_frames = [frame for frame in frames if "dt" in frame.columns]
    weekly = _weekly_index(pd.concat(weekly_frames, ignore_index=True) if weekly_frames else pd.DataFrame())
    snapshots = _snapshot_index(pd.concat(snapshot_frames, ignore_index=True) if snapshot_frames else pd.DataFrame())
    schedule = pd.read_csv(schedule_path)
    frame, summary = build(schedule, weekly, snapshots)
    written = write_outputs(frame, summary, pins)
    print(json.dumps({k: written[k] for k in ("n", "sha256", "empty_team_games", "by_source", "by_position")}, indent=2))


if __name__ == "__main__":
    main()
