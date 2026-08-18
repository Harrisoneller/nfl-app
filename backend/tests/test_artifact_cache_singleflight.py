"""Regression tests for artifact_cache single-flight cancellation safety.

Background (2026-08-18 production incident): `/predictions/games` returned a
hard 500 on every request, so the home page rendered its "Week 1 schedule
coming soon" empty state instead of the matchup previews.

Root cause: `get_or_compute` caught only `Exception` around `await compute()`.
`asyncio.CancelledError` derives from `BaseException`, so when the elected
leader's task was cancelled (a request budget expiring, or the in-process
scheduler job being cancelled) the cleanup never ran and the shared future was
left in `_inflight`, unresolved. The next waiter awaited that orphan; when its
own budget expired, asyncio cancelled the future it was awaiting — the *shared*
one. From then on every caller awaited an already-cancelled future and got an
instant CancelledError, which escaped `run_with_budget`'s `except Exception`
and surfaced as `RuntimeError: No response returned` → HTTP 500, permanently,
until the process restarted.

These tests pin the three properties that prevent a recurrence.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import artifact_cache


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch):
    """Bypass L1/L2 so each test exercises the single-flight path directly."""
    artifact_cache._inflight.clear()
    monkeypatch.setattr(artifact_cache.l1_cache, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(artifact_cache.l1_cache, "set", lambda *_a, **_k: None)

    def _no_db(*_a, **_k):
        raise RuntimeError("DB intentionally unavailable in this test")

    monkeypatch.setattr(artifact_cache, "SessionLocal", _no_db)
    yield
    artifact_cache._inflight.clear()


def _counting_compute(delay: float = 0.10):
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        await asyncio.sleep(delay)
        return {"value": "computed"}

    return compute, calls


async def test_cancelled_leader_does_not_poison_later_callers():
    """The exact production sequence must not wedge the cache."""
    compute, calls = _counting_compute()

    # 1. Leader starts computing, then is cancelled mid-flight.
    leader = asyncio.create_task(artifact_cache.get_or_compute("k", "a", compute))
    await asyncio.sleep(0.01)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    # 2. A waiter joins and its own budget expires while awaiting the orphan.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            artifact_cache.get_or_compute("k", "a", compute), timeout=0.02
        )

    # 3. Every subsequent caller must still succeed.
    for _ in range(3):
        result = await asyncio.wait_for(
            artifact_cache.get_or_compute("k", "a", compute), timeout=3
        )
        assert result == {"value": "computed"}

    assert not artifact_cache._inflight, "left a dead future in the in-flight map"
    assert calls["n"] >= 2, "should have recomputed after the leader died"


async def test_waiter_cancellation_does_not_cancel_the_shared_future():
    """One caller giving up must not break the compute for everyone else.

    Waiters `asyncio.shield` the shared future precisely so a short per-request
    budget can't cancel work another caller still needs.
    """
    compute, calls = _counting_compute(delay=0.15)

    leader = asyncio.create_task(artifact_cache.get_or_compute("k", "b", compute))
    await asyncio.sleep(0.01)

    # Waiter gives up early.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            artifact_cache.get_or_compute("k", "b", compute), timeout=0.02
        )

    # The leader must still complete normally.
    assert await leader == {"value": "computed"}
    assert calls["n"] == 1, "the leader's compute should not have been restarted"
    assert not artifact_cache._inflight


async def test_failing_leader_propagates_but_clears_the_slot():
    """A genuine exception must not become sticky either."""
    boom_calls = {"n": 0}

    async def boom():
        boom_calls["n"] += 1
        raise ValueError("compute failed")

    with pytest.raises(ValueError):
        await artifact_cache.get_or_compute("k", "c", boom)

    assert not artifact_cache._inflight

    # The next caller gets a fresh attempt rather than the cached failure.
    with pytest.raises(ValueError):
        await artifact_cache.get_or_compute("k", "c", boom)
    assert boom_calls["n"] == 2
