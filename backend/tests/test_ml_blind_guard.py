"""Fit entry points refuse without an external SHA. No model is fit."""
from __future__ import annotations

import urllib.request

import pytest
from fastapi import HTTPException

from app.routers.predictions import train_ml
from app.services.backtest_service import backtest_ml
from app.services.ml_blind_guard import (
    GitState,
    LockRefusal,
    assert_fit_allowed,
    write_results_once,
)
from app.services.ml_predictions_service import fit_market_blind_models
from scripts.eval_nfl_ml_blind import run_locked_eval


def test_missing_sha_refuses_and_does_not_fit():
    with pytest.raises(LockRefusal, match="SHA"):
        fit_market_blind_models(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backtest_and_admin_train_refuse_before_work(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise AssertionError("train was called")

    monkeypatch.setattr("app.routers.predictions.ml_predictions_service.train", boom)
    with pytest.raises(HTTPException) as caught:
        await train_ml(locked_sha=None, db=None)
    assert caught.value.status_code == 409
    body = await backtest_ml(None)
    assert body["available"] is False
    assert body["reason"] == "qa_lock_required"


def test_results_file_is_never_overwritten(tmp_path):
    path = tmp_path / "RESULTS.json"
    path.write_text("keep")
    state = GitState(head="a" * 40, dirty=False)
    with pytest.raises(LockRefusal, match="overwrite"):
        assert_fit_allowed("a" * 40, git_state=state, results_path=path)
    with pytest.raises(LockRefusal, match="overwrite"):
        write_results_once(path, "new")
    assert path.read_text() == "keep"


def test_sha_mismatch_and_dirty_tree_refuse(tmp_path):
    missing = tmp_path / "absent.json"
    with pytest.raises(LockRefusal, match="HEAD"):
        assert_fit_allowed(
            "b" * 40,
            git_state=GitState(head="a" * 40, dirty=False),
            results_path=missing,
        )
    with pytest.raises(LockRefusal, match="clean"):
        assert_fit_allowed(
            "a" * 40,
            git_state=GitState(head="a" * 40, dirty=True),
            results_path=missing,
        )


def test_eval_refuses_before_any_download(monkeypatch):
    monkeypatch.delenv("NFL_ML_BLIND_LOCKED_SHA", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("download")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(LockRefusal, match="SHA"):
        run_locked_eval()
