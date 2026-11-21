"""Read-only store for frozen drive-sim projections.

The weekly file is static JSON written by ``scripts/import_sim_freeze.py``.
Request handlers only read it. This module does not import the drive
simulator, the v5 rating model, or any market feature.

Storage is a JSON document rather than a table. The freeze is one hashed
file per week, checked before kickoff. A table would need a migration and a
production write, and a later job could replace the row. The API never
writes this file, so serving the panel cannot move the frozen numbers.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "sim_freeze" / "games.json"

PUBLIC_FIELDS = (
    "game_id",
    "season",
    "week",
    "sim_total",
    "sim_margin_home",
    "sim_home_wp",
    "frozen_at",
    "source_commit",
    "sha256",
)

# Current research output (run_p3 per-game rows) and the published names.
_TOTAL_KEYS = ("sim_total", "sim_total_mean")
_MARGIN_KEYS = ("sim_margin_home", "sim_margin_mean")
_WP_KEYS = ("sim_home_wp", "sim_p_home")

_GAME_ID = re.compile(r"^(\d{4})_(\d{1,2})_([A-Z]{2,3})_([A-Z]{2,3})$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")

_MARKET_EXACT = {
    "spread_line",
    "total_line",
    "home_spread_odds",
    "away_spread_odds",
    "home_moneyline",
    "away_moneyline",
    "moneyline",
    "vegas_wp",
    "home_vegas_wp",
    "away_vegas_wp",
    "vegas_home_wp",
    "odds",
    "closing_spread",
    "closing_total",
    "market_spread",
    "market_total",
    "market_home_win_prob",
    "spread_home",
    "edge",
    "pick",
    "recommendation",
    "mkt_d",
    "mkt_s",
    "home_line",
    "away_line",
}

_cache: dict[str, tuple[int, dict[str, dict]]] = {}


class SimFreezeError(Exception):
    """The frozen file cannot be published."""


class HashMismatch(SimFreezeError):
    """The file bytes do not match the sha256 supplied with the freeze."""


class MarketFieldRejected(SimFreezeError):
    """The file carries a line, odds, edge, pick, or other market field."""


class FrozenSimLocked(SimFreezeError):
    """A game is already stored and the new record is not identical."""


@dataclass(frozen=True)
class ImportResult:
    season: int
    week: int
    games: int
    written: bool
    store_path: str


def public_record(game_id: str, store_path: Path | None = None) -> dict | None:
    """The frozen record for one game, or None when this store has no row."""
    gid = (game_id or "").strip()
    if not gid:
        return None
    row = load_games(store_path).get(gid)
    if row is None:
        return None
    return {key: row[key] for key in PUBLIC_FIELDS}


def load_games(store_path: Path | None = None) -> dict[str, dict]:
    path = Path(store_path) if store_path is not None else STORE_PATH
    if not path.is_file():
        return {}
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(path)
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    games = payload.get("games") if isinstance(payload, dict) else None
    clean: dict[str, dict] = {}
    if isinstance(games, dict):
        for gid, row in games.items():
            if isinstance(row, dict) and _row_is_public(row):
                clean[str(gid)] = {field: row[field] for field in PUBLIC_FIELDS}
    _cache[key] = (mtime, clean)
    return clean


def attach_games(payload: dict) -> dict:
    """Copy a games payload and set each game's ``sim`` sibling.

    The prediction dict is left as the model returned it.
    """
    if not isinstance(payload, dict):
        return payload
    games = payload.get("games")
    if not isinstance(games, list):
        return payload
    out = dict(payload)
    out["games"] = [_with_sim(game) for game in games]
    return out


def attach_h2h(payload: dict) -> dict:
    """Copy an H2H payload and set ``predicted_matchup.sim``.

    A hypothetical matchup has no frozen game, so ``sim`` is null.
    """
    if not isinstance(payload, dict):
        return payload
    matchup = payload.get("predicted_matchup")
    if not isinstance(matchup, dict):
        return payload
    out = dict(payload)
    match = dict(matchup)
    if match.get("hypothetical"):
        match["sim"] = None
    else:
        gid = str(match.get("game_id") or "")
        match["sim"] = public_record(gid) if gid else None
    out["predicted_matchup"] = match
    return out


def import_week(
    file_path: Path | str,
    sha256: str,
    *,
    source_commit: str | None = None,
    frozen_at: str | None = None,
    store_path: Path | str | None = None,
) -> ImportResult:
    """Verify one week's frozen file and merge its sim rows into the store.

    A hash mismatch, a market column, or a conflict with a row already stored
    leaves the store untouched.
    """
    path = Path(file_path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = _normalize_sha256(sha256)
    if digest != expected:
        raise HashMismatch(
            f"sha256 mismatch for {path.name}: file is {digest}, argument is {expected}"
        )

    file_meta, rows = _parse_frozen_file(raw, path)
    commit = _agree(
        _normalize_commit(source_commit) if source_commit else None,
        _normalize_commit(file_meta.get("source_commit")) if file_meta.get("source_commit") else None,
        "source_commit",
    )
    row_commit = _unique_optional(rows, "source_commit", _normalize_commit)
    commit = _agree(commit, row_commit, "source_commit")
    stamp = _agree(
        _normalize_timestamp(frozen_at) if frozen_at else None,
        _normalize_timestamp(file_meta.get("frozen_at")) if file_meta.get("frozen_at") else None,
        "frozen_at",
    )
    row_stamp = _unique_optional(rows, "frozen_at", _normalize_timestamp)
    stamp = _agree(stamp, row_stamp, "frozen_at")
    if not commit or not stamp:
        raise SimFreezeError("source_commit and frozen_at are required")

    records = _records(rows, file_meta, commit=commit, frozen_at=stamp, sha256=digest)
    destination = Path(store_path) if store_path is not None else STORE_PATH
    written = _merge(destination, records)
    season = records[0]["season"]
    week = records[0]["week"]
    return ImportResult(
        season=season,
        week=week,
        games=len(records),
        written=written,
        store_path=str(destination),
    )


def _with_sim(game: object) -> object:
    if not isinstance(game, dict):
        return game
    copied = dict(game)
    gid = str(copied.get("id") or copied.get("game_id") or "")
    copied["sim"] = public_record(gid) if gid else None
    return copied


def _row_is_public(row: dict) -> bool:
    if any(field not in row for field in PUBLIC_FIELDS):
        return False
    try:
        season = int(row["season"])
        week = int(row["week"])
        total = float(row["sim_total"])
        margin = float(row["sim_margin_home"])
        wp = float(row["sim_home_wp"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(row["game_id"], str)
        and row["game_id"]
        and season > 0
        and week > 0
        and math.isfinite(total)
        and math.isfinite(margin)
        and math.isfinite(wp)
        and isinstance(row["frozen_at"], str)
        and isinstance(row["source_commit"], str)
        and isinstance(row["sha256"], str)
        and len(row["sha256"]) == 64
    )


def _merge(destination: Path, records: list[dict]) -> bool:
    existing = _read_store(destination)
    changed = False
    for record in records:
        current = existing.get(record["game_id"])
        if current is None:
            existing[record["game_id"]] = record
            changed = True
            continue
        if current != record:
            raise FrozenSimLocked(
                f"{record['game_id']} is already frozen and this file differs"
            )
    if not changed:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = {"games": {gid: existing[gid] for gid in sorted(existing)}}
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, destination)
    _cache.pop(str(destination), None)
    return True


def _read_store(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SimFreezeError(f"store is not valid JSON: {path}") from exc
    games = payload.get("games") if isinstance(payload, dict) else None
    if not isinstance(games, dict):
        raise SimFreezeError(f"store has no games object: {path}")
    clean: dict[str, dict] = {}
    for gid, row in games.items():
        if not isinstance(row, dict) or not _row_is_public(row):
            raise SimFreezeError(f"store row {gid} is not a frozen sim record")
        clean[str(gid)] = {field: row[field] for field in PUBLIC_FIELDS}
    return clean


def _records(
    rows: list[dict],
    file_meta: dict,
    *,
    commit: str,
    frozen_at: str,
    sha256: str,
) -> list[dict]:
    if not rows:
        raise SimFreezeError("frozen file has no games")
    seen: set[str] = set()
    records: list[dict] = []
    seasons: set[int] = set()
    weeks: set[int] = set()
    for row in rows:
        game_id = str(row.get("game_id") or "").strip()
        match = _GAME_ID.fullmatch(game_id)
        if match is None:
            raise SimFreezeError(f"game_id {game_id!r} is not a season_week_away_home id")
        if game_id in seen:
            raise SimFreezeError(f"duplicate game_id {game_id}")
        seen.add(game_id)
        season = _require_int(_first(row, file_meta, "season"), "season")
        week = _require_int(_first(row, file_meta, "week"), "week")
        if int(match.group(1)) != season or int(match.group(2)) != week:
            raise SimFreezeError(f"{game_id} does not match season {season} week {week}")
        total = _one_number(row, _TOTAL_KEYS, "sim total")
        margin = _one_number(row, _MARGIN_KEYS, "sim margin")
        wp = _one_number(row, _WP_KEYS, "sim home win probability")
        if not 0.0 <= wp <= 1.0:
            raise SimFreezeError(f"{game_id} sim home win probability is outside 0 to 1")
        if not 0.0 < total <= 150.0:
            raise SimFreezeError(f"{game_id} sim total is outside 0 to 150")
        if not -80.0 <= margin <= 80.0:
            raise SimFreezeError(f"{game_id} sim margin is outside -80 to 80")
        seasons.add(season)
        weeks.add(week)
        records.append({
            "game_id": game_id,
            "season": season,
            "week": week,
            "sim_total": total,
            "sim_margin_home": margin,
            "sim_home_wp": wp,
            "frozen_at": frozen_at,
            "source_commit": commit,
            "sha256": sha256,
        })
    if len(seasons) != 1 or len(weeks) != 1:
        raise SimFreezeError("a frozen file must be exactly one season and one week")
    return records


def _parse_frozen_file(raw: bytes, path: Path) -> tuple[dict, list[dict]]:
    text = raw.decode("utf-8-sig")
    if path.suffix.lower() == ".csv" or _looks_like_csv(text, path):
        return {}, _parse_csv(text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SimFreezeError(f"{path.name} is not JSON or CSV") from exc
    if isinstance(payload, list):
        _reject_market_keys([payload])
        return {}, [_as_row(item) for item in payload]
    if isinstance(payload, dict):
        _reject_market_keys([payload])
        games = payload.get("games", payload.get("rows"))
        if not isinstance(games, list):
            raise SimFreezeError("JSON freeze needs a games list")
        return payload, [_as_row(item) for item in games]
    raise SimFreezeError(f"{path.name} is not a freeze document")


def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SimFreezeError("CSV freeze has no header")
    _reject_market_keys([list(reader.fieldnames)])
    rows = []
    for raw in reader:
        if raw is None or not any((value or "").strip() for value in raw.values()):
            continue
        rows.append({key: value for key, value in raw.items() if key is not None})
    return rows


def _looks_like_csv(text: str, path: Path) -> bool:
    if path.suffix.lower() == ".json":
        return False
    head = text.lstrip()
    return head.startswith(("game_id,", '"game_id"'))


def _as_row(item: object) -> dict:
    if not isinstance(item, dict):
        raise SimFreezeError("each frozen game must be an object")
    _reject_market_keys([item])
    return item


def _reject_market_keys(nodes: list) -> None:
    pending = list(nodes)
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if _is_market_key(str(key)):
                    raise MarketFieldRejected(f"market field {key} is not publishable")
                if isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str) and _is_market_key(item):
                    raise MarketFieldRejected(f"market field {item} is not publishable")
                if isinstance(item, (dict, list)):
                    pending.append(item)


def _is_market_key(key: str) -> bool:
    name = key.strip().lower()
    if not name:
        return False
    if name in _MARKET_EXACT:
        return True
    if name.startswith(("mkt_", "market_")):
        return True
    return "moneyline" in name or "vegas" in name or name.endswith("_odds")


def _first(row: dict, file_meta: dict, key: str):
    if key in row and str(row.get(key) or "").strip() != "":
        return row[key]
    return file_meta.get(key)


def _one_number(row: dict, keys: tuple[str, ...], label: str) -> float:
    found: list[float] = []
    for key in keys:
        if key not in row:
            continue
        raw = row[key]
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            continue
        found.append(_require_float(raw, label))
    if not found:
        game_id = row.get("game_id")
        raise SimFreezeError(f"{game_id} is missing {label}")
    if any(abs(value - found[0]) > 1e-9 for value in found[1:]):
        raise SimFreezeError(f"{row.get('game_id')} has conflicting {label} columns")
    return found[0]


def _unique_optional(rows: list[dict], key: str, normalize):
    values = []
    for row in rows:
        raw = row.get(key)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            continue
        values.append(normalize(raw))
    if not values:
        return None
    unique = set(values)
    if len(unique) != 1:
        raise SimFreezeError(f"{key} differs across rows")
    return values[0]


def _agree(left: str | None, right: str | None, label: str) -> str | None:
    if left and right and left != right:
        raise SimFreezeError(f"{label} on the file does not match the import argument")
    return left or right


def _require_int(value, label: str) -> int:
    if isinstance(value, bool) or value is None or (isinstance(value, str) and value.strip() == ""):
        raise SimFreezeError(f"{label} is required")
    if isinstance(value, float):
        if not value.is_integer():
            raise SimFreezeError(f"{label} must be a whole number")
        return int(value)
    try:
        text = str(value).strip().removeprefix("+")
        return int(text)
    except (TypeError, ValueError) as exc:
        raise SimFreezeError(f"{label} must be a whole number") from exc


def _require_float(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SimFreezeError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise SimFreezeError(f"{label} must be finite")
    return number


def _normalize_sha256(value: str) -> str:
    token = (value or "").strip().lower()
    if token.startswith("sha256:"):
        token = token.split(":", 1)[1].strip()
    token = token.split()[0] if token else ""
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise SimFreezeError("sha256 must be 64 hex characters")
    return token


def _normalize_commit(value) -> str:
    token = str(value or "").strip().lower()
    if not _COMMIT.fullmatch(token):
        raise SimFreezeError("source_commit must be a 7 to 40 character hex sha")
    return token


def _normalize_timestamp(value) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SimFreezeError("frozen_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise SimFreezeError("frozen_at must include a timezone")
    return parsed.isoformat()
