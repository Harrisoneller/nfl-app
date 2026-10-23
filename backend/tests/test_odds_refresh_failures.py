"""Odds refresh must fail closed on HTTP 401/402, and HTTP logs must not leak keys."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.executors.base import run_coroutine_job
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

API_KEY = "odds-test-key-do-not-leak-9f3a"
API_TOKEN = "odds-test-token-do-not-leak-44c1"

_QUOTA = {"x-requests-remaining": "12", "x-requests-used": "488"}


def test_redact_secrets_masks_key_and_token_query_values():
    from app.logging_config import redact_secrets

    raw = (
        "https://api.the-odds-api.com/v4/odds?apiKey=super-secret"
        "&regions=us&api_key=other-secret&token=tok-1&access_token=tok-2"
        "&key=bare-key&markets=h2h"
    )
    masked = redact_secrets(raw)
    for secret in ("super-secret", "other-secret", "tok-1", "tok-2", "bare-key"):
        assert secret not in masked
    assert "apiKey=REDACTED" in masked
    assert "api_key=REDACTED" in masked
    assert "token=REDACTED" in masked
    assert "access_token=REDACTED" in masked
    assert "key=REDACTED" in masked
    assert "regions=us" in masked
    assert "markets=h2h" in masked
    # Not a query credential — must survive.
    assert redact_secrets("error_code=INVALID_KEY") == "error_code=INVALID_KEY"
    assert redact_secrets(masked) == masked


async def test_httpx_and_httpcore_logs_do_not_contain_api_key(caplog):
    """The real httpx INFO line includes the full URL. The key must not survive it."""
    from app.logging_config import configure_logging, install_http_secret_redaction

    configure_logging()
    install_http_secret_redaction()
    caplog.set_level(logging.DEBUG)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await client.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
            params={
                "apiKey": API_KEY,
                "regions": "us",
                "token": API_TOKEN,
                "key": API_KEY,
            },
        )

    logging.getLogger("httpx").info(
        'HTTP Request: %s %s "%s %d %s"',
        "GET",
        httpx.URL(
            "https://api.the-odds-api.com/v4/odds"
            f"?apiKey={API_KEY}&access_token={API_TOKEN}&regions=us"
        ),
        "HTTP/1.1",
        200,
        "OK",
    )
    logging.getLogger("httpcore.http11").info(
        "send_request_headers.started target=%r",
        f"/v4/odds?apiKey={API_KEY}&token={API_TOKEN}&regions=us",
    )
    logging.getLogger("httpcore").info(
        "HTTP Request: GET https://example.test/path?api_key=%s&regions=us",
        API_KEY,
    )
    logging.getLogger("httpx").info("error_code=INVALID_KEY")

    text = caplog.text
    assert API_KEY not in text
    assert API_TOKEN not in text
    assert "apiKey=REDACTED" in text
    assert "regions=us" in text
    assert "INVALID_KEY" in text
    assert "executed successfully" not in text


def _memory_session():
    engine = create_engine("sqlite://")
    from app.models.data_sync_run import DataSyncRun
    from app.models.odds import OddsLine
    from app.models.player_prop_snapshot import PlayerPropSnapshot

    for model in (DataSyncRun, OddsLine, PlayerPropSnapshot):
        model.__table__.create(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _patch_odds_job(monkeypatch, handler):
    from app.adapters.data.odds_api import TheOddsApiAdapter
    from app.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "odds_api_key", API_KEY)
    monkeypatch.setattr(cfg, "player_props_enabled", True)
    monkeypatch.setattr("app.services.odds_service._has_game_within", lambda _db, _days: True)

    async def _slate(_db):
        return {"count": 0}

    monkeypatch.setattr("app.jobs.scheduler.sparky_service.build_slate", _slate)

    def _init(self):
        self.api_key = API_KEY
        self.base = "https://api.the-odds-api.com/v4"
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(TheOddsApiAdapter, "__init__", _init)

    factory = _memory_session()
    monkeypatch.setattr("app.db.SessionLocal", factory)
    return factory


def _auth_handler(status_code: int, error_code: str, calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        # The request itself carries the key; the response must not echo it.
        assert API_KEY in str(request.url)
        return httpx.Response(
            status_code,
            json={"message": "rejected", "error_code": error_code},
            headers=_QUOTA,
        )

    return handler


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(401, "INVALID_KEY"), (402, "OUT_OF_USAGE_CREDITS")],
)
async def test_job_refresh_odds_auth_failure_marks_run_failed(
    monkeypatch, caplog, capsys, status_code, error_code,
):
    from app.jobs.scheduler import _job_refresh_odds
    from app.logging_config import configure_logging, install_http_secret_redaction
    from app.models.data_sync_run import DataSyncRun

    configure_logging()
    install_http_secret_redaction()
    caplog.set_level(logging.DEBUG)

    calls: list[str] = []
    factory = _patch_odds_job(monkeypatch, _auth_handler(status_code, error_code, calls))

    job = SimpleNamespace(
        id="odds",
        func=_job_refresh_odds,
        args=(),
        kwargs={},
        misfire_grace_time=None,
    )
    events = await run_coroutine_job(
        job, "default", [datetime.now(UTC)], "apscheduler.scheduler",
    )
    text = _ANSI.sub("", capsys.readouterr().out + caplog.text)

    assert len(calls) == 1, "401/402 must not be retried"
    assert events and events[0].code == EVENT_JOB_ERROR
    assert "executed successfully" not in text
    assert "raised an exception" in text
    assert API_KEY not in text
    assert "odds_api_request_failed" in text
    assert "[error" in text
    assert f"error_code={error_code}" in text
    assert f"status_code={status_code}" in text
    assert "x-requests-remaining=12" in text
    assert "x-requests-used=488" in text

    db = factory()
    try:
        run = db.query(DataSyncRun).filter(DataSyncRun.domain == "odds").one()
        assert run.status == "error"
        assert error_code in (run.message or "")
        assert API_KEY not in (run.message or "")
    finally:
        db.close()


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(401, "INVALID_KEY"), (402, "OUT_OF_USAGE_CREDITS")],
)
async def test_props_per_event_auth_failure_marks_job_failed(
    monkeypatch, caplog, capsys, status_code, error_code,
):
    """Game odds can succeed while a per-event props call returns 401/402."""
    from app.jobs.scheduler import _job_refresh_odds
    from app.logging_config import configure_logging, install_http_secret_redaction
    from app.models.data_sync_run import DataSyncRun

    configure_logging()
    install_http_secret_redaction()
    caplog.set_level(logging.DEBUG)

    soon = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert API_KEY in str(request.url)
        path = request.url.path
        if path.endswith("/events") and "/events/" not in path:
            return httpx.Response(
                200,
                json=[{
                    "id": "evt-1",
                    "commence_time": soon,
                    "home_team": "Kansas City Chiefs",
                    "away_team": "Buffalo Bills",
                }],
                headers={"x-requests-remaining": "40", "x-requests-used": "10"},
            )
        if "/events/" in path:
            return httpx.Response(
                status_code,
                json={"message": "rejected", "error_code": error_code},
                headers=_QUOTA,
            )
        # Non-empty game odds so an empty 200 is not what fails this job.
        return httpx.Response(
            200,
            json=[{
                "id": "evt-1",
                "commence_time": soon,
                "home_team": "Kansas City Chiefs",
                "away_team": "Buffalo Bills",
                "bookmakers": [],
            }],
            headers={"x-requests-remaining": "41", "x-requests-used": "9"},
        )

    factory = _patch_odds_job(monkeypatch, handler)

    job = SimpleNamespace(
        id="odds",
        func=_job_refresh_odds,
        args=(),
        kwargs={},
        misfire_grace_time=None,
    )
    events = await run_coroutine_job(
        job, "default", [datetime.now(UTC)], "apscheduler.scheduler",
    )
    text = _ANSI.sub("", capsys.readouterr().out + caplog.text)

    assert any("/events/" in path for path in calls)
    assert sum("/events/" in path for path in calls) == 1
    assert events and events[0].code == EVENT_JOB_ERROR
    assert "executed successfully" not in text
    assert API_KEY not in text
    assert text.count("odds_api_request_failed") == 1
    assert "[error" in text
    assert f"error_code={error_code}" in text
    assert "/events/evt-1/odds" in text
    assert "x-requests-remaining=12" in text
    assert "x-requests-used=488" in text
    # Quota headers are logged for the successful calls too, not only the failure.
    assert text.count("odds_api_response") >= 3
    assert "status_code=200" in text
    assert f"status_code={status_code}" in text

    db = factory()
    try:
        run = db.query(DataSyncRun).filter(DataSyncRun.domain == "odds").one()
        assert run.status == "error"
        assert error_code in (run.message or "")
        assert API_KEY not in (run.message or "")
    finally:
        db.close()


async def _run_odds_job(monkeypatch, handler):
    from app.jobs.scheduler import _job_refresh_odds

    factory = _patch_odds_job(monkeypatch, handler)
    job = SimpleNamespace(
        id="odds",
        func=_job_refresh_odds,
        args=(),
        kwargs={},
        misfire_grace_time=None,
    )
    events = await run_coroutine_job(
        job, "default", [datetime.now(UTC)], "apscheduler.scheduler",
    )
    return factory, events


async def _assert_upstream_failure(
    monkeypatch, caplog, capsys, handler, needle: str, *, expect_quota: bool = True,
):
    from app.logging_config import configure_logging, install_http_secret_redaction
    from app.models.data_sync_run import DataSyncRun

    configure_logging()
    install_http_secret_redaction()
    caplog.set_level(logging.DEBUG)
    factory, events = await _run_odds_job(monkeypatch, handler)
    text = _ANSI.sub("", capsys.readouterr().out + caplog.text)

    assert events and events[0].code == EVENT_JOB_ERROR
    assert "executed successfully" not in text
    assert API_KEY not in text
    assert needle in text
    if expect_quota:
        assert "x-requests-remaining" in text
        assert "x-requests-used" in text
        assert "/sports/americanfootball_nfl/odds" in text
        assert "api.the-odds-api.com" not in text.split("odds_api_response", 1)[-1].split("\n", 1)[0]

    db = factory()
    try:
        run = db.query(DataSyncRun).filter(DataSyncRun.domain == "odds").one()
        assert run.status == "error"
        assert API_KEY not in (run.message or "")
        assert needle in (run.message or "")
    finally:
        db.close()
    return text


@pytest.mark.parametrize(
    ("status_code", "needle"),
    [(429, "rate_limited"), (500, "HTTP 500"), (503, "HTTP 503")],
)
async def test_job_refresh_odds_upstream_http_is_error(
    monkeypatch, caplog, capsys, status_code, needle,
):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(status_code, json={"message": "nope"}, headers=_QUOTA)

    await _assert_upstream_failure(monkeypatch, caplog, capsys, handler, needle)
    assert len(calls) == 1


async def test_job_refresh_odds_empty_200_is_degraded(monkeypatch, caplog, capsys):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[], headers=_QUOTA)

    text = await _assert_upstream_failure(monkeypatch, caplog, capsys, handler, "degraded")
    assert len(calls) == 1
    assert "odds_api_empty" in text
    assert API_KEY not in text


async def test_job_refresh_odds_timeout_is_error(monkeypatch, caplog, capsys):
    from app.adapters.data.odds_api import TheOddsApiAdapter

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(TheOddsApiAdapter._get.retry, "sleep", _no_sleep)
    monkeypatch.setattr(TheOddsApiAdapter._get_object.retry, "sleep", _no_sleep)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("timed out")

    await _assert_upstream_failure(
        monkeypatch, caplog, capsys, handler, "RetryError", expect_quota=False,
    )
    assert len(calls) == 2


async def test_empty_events_list_does_not_fail_the_job(monkeypatch, caplog, capsys):
    """An empty /events payload is a normal offseason result, not a degraded odds pull."""
    from apscheduler.events import EVENT_JOB_EXECUTED

    from app.logging_config import configure_logging
    from app.models.data_sync_run import DataSyncRun

    configure_logging()
    caplog.set_level(logging.INFO)
    soon = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/events") and "/events/" not in path:
            return httpx.Response(200, json=[], headers=_QUOTA)
        return httpx.Response(
            200,
            json=[{
                "id": "evt-1",
                "commence_time": soon,
                "home_team": "Kansas City Chiefs",
                "away_team": "Buffalo Bills",
                "bookmakers": [],
            }],
            headers=_QUOTA,
        )

    factory, events = await _run_odds_job(monkeypatch, handler)
    text = _ANSI.sub("", capsys.readouterr().out + caplog.text)
    assert events and events[0].code == EVENT_JOB_EXECUTED
    assert "executed successfully" in text
    assert API_KEY not in text
    db = factory()
    try:
        run = db.query(DataSyncRun).filter(DataSyncRun.domain == "odds").one()
        assert run.status == "ok"
    finally:
        db.close()
