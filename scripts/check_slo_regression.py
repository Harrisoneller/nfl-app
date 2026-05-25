#!/usr/bin/env python3
"""Deterministic smoke benchmark + threshold gate for top endpoints."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_ROLE", "web")
os.environ.setdefault("SECRET_KEY", "test-key")

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import endpoint_slo_service  # noqa: E402


def _install_mocks() -> None:
    from app.services import (
        analytics_service,
        comparison_service,
        h2h_service,
        odds_service,
        players_service,
        predictions_service,
        teams_service,
    )

    async def _sleep_ms(ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def fake_h2h(*_args, **_kwargs):
        await _sleep_ms(120)
        return {"ok": True, "_cache": {"served_stale": False}}

    async def fake_predict_week(*_args, **_kwargs):
        await _sleep_ms(150)
        return {"season": 2026, "week": 1, "games": []}

    def fake_odds(*_args, **_kwargs):
        return []

    def fake_event_odds(*_args, **_kwargs):
        return []

    def fake_teams_seeded(*_args, **_kwargs):
        return 32

    def fake_teams_list(*_args, **_kwargs):
        return []

    async def fake_team_profile(*_args, **_kwargs):
        await _sleep_ms(90)
        return {"team_id": "PHI", "season": 2025, "metrics": {}, "record": {"wins": 0, "losses": 0, "ties": 0}}

    def fake_players_list(*_args, **_kwargs):
        return []

    def fake_player_get(*_args, **_kwargs):
        return {
            "id": "x",
            "full_name": "Player",
            "position": "QB",
            "team_id": None,
            "status": "active",
            "metadata_json": {},
        }

    async def fake_compare_teams(*_args, **_kwargs):
        await _sleep_ms(130)
        return {"season": 2026, "metrics": [], "rows": [], "winners": {}}

    async def fake_compare_players(*_args, **_kwargs):
        await _sleep_ms(130)
        return {"season": 2026, "rows": []}

    h2h_service.head_to_head = fake_h2h
    predictions_service.predict_week = fake_predict_week
    odds_service.list_odds = fake_odds
    odds_service.get_event_odds = fake_event_odds
    teams_service.ensure_seeded = fake_teams_seeded
    teams_service.list_teams = fake_teams_list
    analytics_service.team_profile = fake_team_profile
    players_service.list_players = fake_players_list
    players_service.get_player = fake_player_get
    comparison_service.compare_teams = fake_compare_teams
    comparison_service.compare_players = fake_compare_players


def _exercise(client: TestClient) -> None:
    requests = [
        ("GET", "/h2h/PHI/SF"),
        ("GET", "/predictions/games?include_ml=false"),
        ("GET", "/odds"),
        ("GET", "/odds/event/demo"),
        ("GET", "/teams"),
        ("GET", "/teams/PHI/profile"),
        ("GET", "/players"),
        ("GET", "/players/test-player"),
        ("GET", "/stats/compare/teams?teams=PHI,SF&season=2024"),
        ("GET", "/stats/compare/players?names=Josh%20Allen,Patrick%20Mahomes&season=2024"),
    ]
    for _ in range(4):
        for method, path in requests:
            res = client.request(method, path)
            if res.status_code >= 500:
                raise RuntimeError(f"smoke request failed for {path}: {res.status_code}")


def _load_thresholds() -> dict:
    with (REPO_ROOT / "scripts" / "slo_thresholds.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    get_settings.cache_clear()
    _install_mocks()
    app = create_app()
    with TestClient(app) as client:
        _exercise(client)
    snap = endpoint_slo_service.current_snapshot(window_seconds=60 * 60)
    thresholds = _load_thresholds()["routes"]
    by_endpoint = {}
    for row in snap["rows"]:
        key = row["endpoint"]
        by_endpoint.setdefault(key, []).append(row)

    failures: list[str] = []
    for endpoint, rules in thresholds.items():
        rows = by_endpoint.get(endpoint, [])
        if not rows:
            failures.append(f"{endpoint}: missing metric row")
            continue
        p95 = max(r["p95_ms"] for r in rows)
        p99 = max(r["p99_ms"] for r in rows)
        hit_rate = max(r["cache_hit_rate"] for r in rows)
        if p95 > float(rules["p95_ms_max"]):
            failures.append(f"{endpoint}: p95 {p95} > {rules['p95_ms_max']}")
        if p99 > float(rules["p99_ms_max"]):
            failures.append(f"{endpoint}: p99 {p99} > {rules['p99_ms_max']}")
        if hit_rate < float(rules["cache_hit_rate_min"]):
            failures.append(f"{endpoint}: cache_hit_rate {hit_rate} < {rules['cache_hit_rate_min']}")

    if failures:
        print("SLO regression gate failed:")
        for item in failures:
            print(f"- {item}")
        return 1
    print("SLO regression gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
