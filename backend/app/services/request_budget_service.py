"""Helpers for timeout budgets + safe fallback tiers."""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable

from ..logging_config import get_logger

log = get_logger(__name__)

FallbackFactory = Callable[[], Any | Awaitable[Any]]


async def run_with_budget(
    *,
    budget_name: str,
    timeout_ms: int,
    execute: Callable[[], Awaitable[Any]],
    stale_fallback: FallbackFactory | None = None,
    summary_fallback: FallbackFactory | None = None,
) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    try:
        payload = await asyncio.wait_for(execute(), timeout=timeout_ms / 1000)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        meta = {
            "name": budget_name,
            "tier": "primary",
            "timeout_ms": timeout_ms,
            "elapsed_ms": elapsed_ms,
            "reason": None,
        }
        log.info("budget_path_primary", **meta)
        return payload, meta
    except asyncio.TimeoutError:
        return await _fallback(
            budget_name=budget_name,
            timeout_ms=timeout_ms,
            started=started,
            reason="timeout",
            stale_fallback=stale_fallback,
            summary_fallback=summary_fallback,
        )
    except Exception as e:  # noqa: BLE001
        return await _fallback(
            budget_name=budget_name,
            timeout_ms=timeout_ms,
            started=started,
            reason=f"error:{type(e).__name__}",
            stale_fallback=stale_fallback,
            summary_fallback=summary_fallback,
        )


async def _fallback(
    *,
    budget_name: str,
    timeout_ms: int,
    started: float,
    reason: str,
    stale_fallback: FallbackFactory | None,
    summary_fallback: FallbackFactory | None,
) -> tuple[Any, dict[str, Any]]:
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if stale_fallback is not None:
        payload = stale_fallback()
        if inspect.isawaitable(payload):
            payload = await payload
        meta = {
            "name": budget_name,
            "tier": "stale_fallback",
            "timeout_ms": timeout_ms,
            "elapsed_ms": elapsed_ms,
            "reason": reason,
        }
        log.warning("budget_path_fallback", **meta)
        return payload, meta
    if summary_fallback is not None:
        payload = summary_fallback()
        if inspect.isawaitable(payload):
            payload = await payload
        meta = {
            "name": budget_name,
            "tier": "summary_fallback",
            "timeout_ms": timeout_ms,
            "elapsed_ms": elapsed_ms,
            "reason": reason,
        }
        log.warning("budget_path_fallback", **meta)
        return payload, meta
    meta = {
        "name": budget_name,
        "tier": "failed",
        "timeout_ms": timeout_ms,
        "elapsed_ms": elapsed_ms,
        "reason": reason,
    }
    log.error("budget_path_failed", **meta)
    return {"error": "budget_exceeded", "_budget": meta}, meta
