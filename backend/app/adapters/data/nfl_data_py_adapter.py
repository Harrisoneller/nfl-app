"""nfl-data-py adapter.

Wraps the synchronous nfl-data-py library. Calls are run in a thread because
the library is sync and can be slow on first hit (it caches parquet files
locally under ~/.nfl_data_py_cache).

Robustness:
- **Upstream NameError patch**: nfl_data_py 0.3.x has a bug where some
  except clauses reference an undefined `Error` symbol. When the protected
  call fails (e.g. asking for a season that nflverse hasn't published),
  the except clause itself raises `NameError: name 'Error' is not defined`
  instead of the expected error. We inject `Error = Exception` into the
  module namespace at import time so their `except Error:` clauses
  resolve correctly.
- **Per-call timeout** via `asyncio.wait_for` so a stuck download can't
  pin a request thread forever.
- **Circuit breaker** per (fn_name, season) — after a small number of
  failures within a window we fast-fail subsequent calls for a cool-off
  period, sparing the user, our logs, and the upstream from a cascade.
- **Failure log demotion** — known upstream issues (NameError from the
  monkey-patched module, missing-season errors) log at debug instead
  of warning so the access log isn't drowned in noise.
"""
from __future__ import annotations

import asyncio
import functools
import time
from collections import defaultdict
from typing import Any

from ...logging_config import get_logger

log = get_logger(__name__)

try:
    import nfl_data_py as nfl  # type: ignore
    import pandas as pd  # noqa: F401

    # Patch the upstream NameError bug — see module docstring.
    if not hasattr(nfl, "Error"):
        nfl.Error = Exception  # type: ignore[attr-defined]

    _AVAILABLE = True
except Exception as e:  # pragma: no cover
    log.warning("nfl_data_py_unavailable", error=str(e))
    nfl = None  # type: ignore
    _AVAILABLE = False


DEFAULT_TIMEOUT_S = 45.0  # First-call parquet downloads can be slow.

# Circuit breaker: if a given (fn, args-key) fails this many times within the
# window, subsequent calls fast-fail for the cooldown duration.
_CB_FAILURE_THRESHOLD = 3
_CB_FAILURE_WINDOW_S = 120.0
_CB_COOLDOWN_S = 180.0

# Recent-failure ledger: key → list[unix_ts]
_recent_failures: dict[str, list[float]] = defaultdict(list)
# Cooldown ledger: key → unix_ts the cooldown ends
_cooldown_until: dict[str, float] = {}


def _cb_key(fn_name: str, args: tuple) -> str:
    return f"{fn_name}:{','.join(str(a) for a in args)}"


def _cb_should_fast_fail(key: str) -> bool:
    until = _cooldown_until.get(key, 0.0)
    if until and time.monotonic() < until:
        return True
    return False


def _cb_record_failure(key: str) -> None:
    now = time.monotonic()
    lst = _recent_failures[key]
    # Drop stale entries
    cutoff = now - _CB_FAILURE_WINDOW_S
    while lst and lst[0] < cutoff:
        lst.pop(0)
    lst.append(now)
    if len(lst) >= _CB_FAILURE_THRESHOLD:
        _cooldown_until[key] = now + _CB_COOLDOWN_S
        lst.clear()
        log.info("nfl_data_py_circuit_open", key=key, cooldown_s=_CB_COOLDOWN_S)


def _cb_record_success(key: str) -> None:
    # Clear any pending failure history on success
    _recent_failures.pop(key, None)
    _cooldown_until.pop(key, None)


async def _run_sync_safe(
    fn, *args, timeout: float = DEFAULT_TIMEOUT_S, fn_name: str = "",
):
    """Run a sync nfl-data-py call in a thread with timeout + error capture.

    Returns None on any failure (network error, missing season, timeout,
    circuit-breaker-open). Logs at warning for real surprises, debug for
    expected upstream noise.
    """
    if not _AVAILABLE:
        return None

    key = _cb_key(fn_name or getattr(fn, "__name__", "?"), args)
    if _cb_should_fast_fail(key):
        log.debug("nfl_data_py_fast_failed_circuit_open", key=key)
        return None

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, functools.partial(fn, *args)),
            timeout=timeout,
        )
        _cb_record_success(key)
        return result
    except asyncio.TimeoutError:
        log.warning("nfl_data_py_timeout", fn=fn_name or getattr(fn, "__name__", "?"))
        _cb_record_failure(key)
        return None
    except Exception as e:  # noqa: BLE001
        # Common case: requested season doesn't exist yet on nflverse, OR
        # upstream's NameError-bug except clause fired. Both are expected
        # noise — debug, not warning.
        msg = str(e)[:200]
        if "not defined" in msg or "404" in msg or "no such" in msg.lower():
            log.debug(
                "nfl_data_py_expected_miss",
                fn=fn_name or getattr(fn, "__name__", "?"),
                error=msg,
            )
        else:
            log.warning(
                "nfl_data_py_failed",
                fn=fn_name or getattr(fn, "__name__", "?"),
                error=msg,
            )
        _cb_record_failure(key)
        return None


def circuit_breaker_status() -> dict[str, Any]:
    """Snapshot for the admin diagnostic endpoint."""
    now = time.monotonic()
    return {
        "open_circuits": {
            k: round(v - now, 1) for k, v in _cooldown_until.items() if v > now
        },
        "recent_failures": {
            k: len([t for t in lst if t > now - _CB_FAILURE_WINDOW_S])
            for k, lst in _recent_failures.items()
        },
    }


class NflDataPyAdapter:
    available = _AVAILABLE

    # ---- raw frame accessors -------------------------------------------------

    async def weekly_df(self, season: int):
        return await _run_sync_safe(nfl.import_weekly_data, [season], fn_name="weekly")

    async def seasonal_df(self, season: int):
        return await _run_sync_safe(nfl.import_seasonal_data, [season], fn_name="seasonal")

    async def rosters_df(self, season: int):
        return await _run_sync_safe(nfl.import_seasonal_rosters, [season], fn_name="rosters")

    async def schedules_df(self, season: int):
        return await _run_sync_safe(nfl.import_schedules, [season], fn_name="schedules")

    async def pbp_df(self, season: int):
        """Play-by-play. ~50MB per season; first call is slow — longer leash."""
        return await _run_sync_safe(nfl.import_pbp_data, [season], timeout=120.0, fn_name="pbp")

    # ---- dict variants for places that don't need DataFrames -----------------

    async def weekly_player_stats(self, season: int) -> list[dict[str, Any]]:
        df = await self.weekly_df(season)
        return df.to_dict(orient="records") if df is not None and len(df) else []

    async def seasonal_player_stats(self, season: int) -> list[dict[str, Any]]:
        df = await self.seasonal_df(season)
        return df.to_dict(orient="records") if df is not None and len(df) else []

    async def rosters(self, season: int) -> list[dict[str, Any]]:
        df = await self.rosters_df(season)
        return df.to_dict(orient="records") if df is not None and len(df) else []

    async def schedules(self, season: int) -> list[dict[str, Any]]:
        df = await self.schedules_df(season)
        return df.to_dict(orient="records") if df is not None and len(df) else []
