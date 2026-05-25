from __future__ import annotations

import asyncio

from app.services import request_budget_service


async def _slow():
    await asyncio.sleep(0.05)
    return {"ok": True}


async def _fast():
    await asyncio.sleep(0.001)
    return {"ok": True}


async def test_budget_primary_path():
    payload, meta = await request_budget_service.run_with_budget(
        budget_name="test.primary",
        timeout_ms=100,
        execute=_fast,
    )
    assert payload["ok"] is True
    assert meta["tier"] == "primary"


async def test_budget_summary_fallback_on_timeout():
    payload, meta = await request_budget_service.run_with_budget(
        budget_name="test.timeout",
        timeout_ms=1,
        execute=_slow,
        summary_fallback=lambda: {"partial": True},
    )
    assert payload["partial"] is True
    assert meta["tier"] == "summary_fallback"
