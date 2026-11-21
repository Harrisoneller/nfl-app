"""Frozen drive-sim panel: hash check, null when missing, no market fields.

The v5 headline is the existing prediction dict. These tests do not call the
drive simulator and do not download a schedule.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import predictions_service as ps
from app.services import sim_panel_store as store

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "import_sim_freeze.py"
COMMIT = "0a3212dbf5c48125e2f6ac6be4476bef774acebe"
FROZEN_AT = "2026-11-20T18:00:00Z"
MARKET_WORDS = (
    "odds",
    "edge",
    "pick",
    "moneyline",
    "recommendation",
    "vegas",
    "spread_line",
    "total_line",
    "market",
)


def _v5(margin: float = 6.0, total: float = 44.0) -> dict:
    return {
        "model_version": "qb-ridge-v5",
        "margin": margin,
        "total": total,
        "margin_parts": {"epa_d": margin - 1.8, "hfa": 1.8},
        "total_parts": {},
        "features": {"qb_h": 0.0, "qb_a": 0.0},
        "uses_market_prior": True,
        "qb_change": {"home": False, "away": False},
    }


def _week_json() -> dict:
    return {
        "season": 2026,
        "week": 12,
        "frozen_at": FROZEN_AT,
        "source_commit": COMMIT,
        "games": [
            {
                "game_id": "2026_12_BUF_KC",
                "sim_total_mean": 47.2,
                "sim_margin_mean": 3.5,
                "sim_p_home": 0.61,
                "v5_total": 44.0,
                "v5_margin": -6.0,
                "v5_p_home": 0.64,
            }
        ],
    }


def _write(path: Path, payload: dict | str) -> str:
    if isinstance(payload, str):
        raw = payload.encode()
    else:
        raw = (json.dumps(payload) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _import(path: Path, digest: str, store_path: Path, **kwargs):
    return store.import_week(
        path,
        digest,
        source_commit=kwargs.get("source_commit", COMMIT),
        frozen_at=kwargs.get("frozen_at", FROZEN_AT),
        store_path=store_path,
    )


def test_hash_mismatch_writes_nothing(tmp_path: Path):
    source = tmp_path / "week.json"
    _write(source, _week_json())
    destination = tmp_path / "games.json"
    destination.write_text('{"games": {}}\n', encoding="utf-8")
    before = destination.read_bytes()
    with pytest.raises(store.HashMismatch):
        _import(source, "0" * 64, destination)
    assert destination.read_bytes() == before
    assert store.public_record("2026_12_BUF_KC", destination) is None


def test_market_column_is_rejected(tmp_path: Path):
    source = tmp_path / "week.csv"
    source.write_text(
        "game_id,season,week,sim_total_mean,sim_margin_mean,sim_p_home,spread_line\n"
        "2026_12_BUF_KC,2026,12,47.2,3.5,0.61,-3.5\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "games.json"
    with pytest.raises(store.MarketFieldRejected):
        _import(source, digest, destination)
    assert not destination.exists()


def test_import_keeps_sim_columns_and_locks_them(tmp_path: Path):
    source = tmp_path / "week.csv"
    source.write_text(
        "game_id,season,week,sim_margin_mean,v5_margin,sim_total_mean,v5_total,"
        "sim_p_home,v5_p_home,actual_total\n"
        "2026_12_BUF_KC,2026,12,3.5,-6.0,47.2,44.0,0.61,0.64,\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "games.json"
    result = _import(source, digest, destination)
    assert result.written is True
    assert result.season == 2026 and result.week == 12
    record = store.public_record("2026_12_BUF_KC", destination)
    assert record is not None
    assert set(record) == set(store.PUBLIC_FIELDS)
    assert record["sim_total"] == 47.2
    assert record["sim_margin_home"] == 3.5
    assert record["sim_home_wp"] == 0.61
    assert record["sha256"] == digest
    assert record["source_commit"] == COMMIT
    blob = json.dumps(record).lower()
    for word in MARKET_WORDS:
        assert word not in blob
    assert "v5_total" not in record
    assert "actual_total" not in record

    frozen_bytes = destination.read_bytes()
    again = _import(source, digest, destination)
    assert again.written is False
    assert destination.read_bytes() == frozen_bytes

    changed = tmp_path / "changed.csv"
    changed.write_text(
        "game_id,season,week,sim_total_mean,sim_margin_mean,sim_p_home\n"
        "2026_12_BUF_KC,2026,12,51.0,3.5,0.61\n",
        encoding="utf-8",
    )
    changed_digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    frozen = destination.read_bytes()
    with pytest.raises(store.FrozenSimLocked):
        _import(changed, changed_digest, destination)
    assert destination.read_bytes() == frozen
    assert store.public_record("2026_12_BUF_KC", destination)["sim_total"] == 47.2


def test_null_when_no_frozen_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    destination = tmp_path / "games.json"
    destination.write_text('{"games": {}}\n', encoding="utf-8")
    monkeypatch.setattr(store, "STORE_PATH", destination)
    assert store.public_record("2026_12_BUF_KC") is None
    with TestClient(create_app()) as client:
        missing = client.get("/predictions/sim/2026_12_BUF_KC")
        assert missing.status_code == 200
        assert missing.json() == {"sim": None}

        pred = ps.predict_game(1500, 1500, v5=_v5())
        headline = {
            "predicted_spread": pred["predicted_spread"],
            "predicted_total": pred["predicted_total"],
            "predicted_home_score": pred["predicted_home_score"],
            "predicted_away_score": pred["predicted_away_score"],
            "home_win_prob": pred["home_win_prob"],
            "model_version": pred["model_version"],
        }

        async def _fake(_db, _season, _week):
            return {
                "season": 2026,
                "week": 12,
                "games": [{
                    "id": "2026_12_BUF_KC",
                    "season": 2026,
                    "week": 12,
                    "home_team_id": "KC",
                    "away_team_id": "BUF",
                    "prediction": pred,
                }],
            }

        monkeypatch.setattr(ps, "predict_week", _fake)
        empty = client.get("/predictions/games", params={"season": 2026, "week": 12})
        assert empty.status_code == 200
        body = empty.json()
        assert body["games"][0]["sim"] is None
        got = body["games"][0]["prediction"]
        for key, value in headline.items():
            assert got[key] == value
        assert "sim" not in got

        source = tmp_path / "week.json"
        digest = _write(source, _week_json())
        _import(source, digest, destination)
        found = client.get("/predictions/sim/2026_12_BUF_KC")
        sim = found.json()["sim"]
        assert set(sim) == set(store.PUBLIC_FIELDS)
        assert sim["sim_total"] == 47.2
        assert sim["sim_margin_home"] == 3.5
        assert sim["sim_home_wp"] == 0.61
        blob = json.dumps(sim).lower()
        for word in MARKET_WORDS:
            assert word not in blob

        loaded = client.get("/predictions/games", params={"season": 2026, "week": 12})
        game = loaded.json()["games"][0]
        assert game["prediction"]["predicted_spread"] == headline["predicted_spread"]
        assert game["prediction"]["predicted_total"] == headline["predicted_total"]
        assert game["prediction"]["home_win_prob"] == headline["home_win_prob"]
        assert game["prediction"]["model_version"] == "qb-ridge-v5"
        assert game["sim"]["sim_total"] == 47.2
        assert "sim_total" not in game["prediction"]


def test_attach_leaves_the_v5_dict_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    destination = tmp_path / "games.json"
    source = tmp_path / "week.json"
    digest = _write(source, _week_json())
    _import(source, digest, destination)
    monkeypatch.setattr(store, "STORE_PATH", destination)
    pred = ps.predict_game(1500, 1500, v5=_v5(margin=4.0, total=45.0))
    frozen = copy.deepcopy(pred)
    payload = store.attach_games({
        "games": [{
            "id": "2026_12_BUF_KC",
            "prediction": pred,
        }],
    })
    game = payload["games"][0]
    assert game["prediction"] is pred
    assert pred == frozen
    assert game["sim"]["sim_margin_home"] == 3.5
    assert set(game["sim"]) == set(store.PUBLIC_FIELDS)

    hypothetical = {
        "predicted_matchup": {
            "hypothetical": True,
            "game_id": "2026_12_BUF_KC",
            "prediction": pred,
        },
    }
    hidden = store.attach_h2h(hypothetical)
    assert hidden["predicted_matchup"]["sim"] is None
    assert hidden["predicted_matchup"]["prediction"] is pred
    assert hypothetical["predicted_matchup"]["prediction"] is pred


def test_import_does_not_call_v5_or_the_sim(tmp_path: Path):
    source_text = Path(store.__file__).read_text(encoding="utf-8").lower()
    assert "team_ratings_v5" not in source_text
    assert "nfl_drive_sim" not in source_text
    assert "sasser" not in source_text
    before = ps.predict_game(1500, 1500, v5=_v5())
    snap = copy.deepcopy(before)
    source = tmp_path / "week.json"
    digest = _write(source, _week_json())
    _import(source, digest, tmp_path / "games.json")
    after = ps.predict_game(1500, 1500, v5=_v5())
    assert after["predicted_spread"] == snap["predicted_spread"]
    assert after["predicted_total"] == snap["predicted_total"]
    assert after["home_win_prob"] == snap["home_win_prob"]
    assert after["model_version"] == "qb-ridge-v5"


def test_cli_rejects_a_bad_hash(tmp_path: Path):
    source = tmp_path / "week.json"
    _write(source, _week_json())
    destination = tmp_path / "games.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--file",
            str(source),
            "--sha256",
            "11" * 32,
            "--source-commit",
            COMMIT,
            "--frozen-at",
            FROZEN_AT,
            "--store",
            str(destination),
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert not destination.exists()
    assert "sha256" in proc.stderr.lower()


def test_one_week_and_one_row(tmp_path: Path):
    mixed = tmp_path / "mixed.json"
    payload = _week_json()
    payload["games"].append({
        "game_id": "2026_13_BUF_KC",
        "season": 2026,
        "week": 13,
        "sim_total": 40.0,
        "sim_margin_home": 1.0,
        "sim_home_wp": 0.55,
    })
    digest = _write(mixed, payload)
    with pytest.raises(store.SimFreezeError):
        _import(mixed, digest, tmp_path / "games.json")

    dup = tmp_path / "dup.json"
    payload = _week_json()
    payload["games"].append(dict(payload["games"][0]))
    digest = _write(dup, payload)
    with pytest.raises(store.SimFreezeError, match="duplicate"):
        _import(dup, digest, tmp_path / "games.json")
