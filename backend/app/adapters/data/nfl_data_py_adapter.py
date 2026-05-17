"""nfl-data-py adapter.

Wraps the synchronous nfl-data-py library. Calls are run in a thread because
the library is sync and can be slow on first hit (it caches parquet files
locally under ~/.nfl_data_py_cache).

Robustness:
- Every call is wrapped in try/except. A missing-season request (e.g. asking
  for 2026 player data in May 2026 when nflverse hasn't published it yet)
  raises inside nfl-data-py; we catch that and return None so callers fall
  back gracefully instead of hanging.
- Per-call timeout via asyncio.wait_for so a stuck download can't pin a
  request thread forever.
"""
from __future__ import annotations

import asyncio
import functools
from typing import Any

from ...logging_config import get_logger

log = get_logger(__name__)

try:
    import nfl_data_py as nfl  # type: ignore
    import pandas as pd  # noqa: F401
    _AVAILABLE = True
except Exception as e:  # pragma: no cover
    log.warning("nfl_data_py_unavailable", error=str(e))
    nfl = None  # type: ignore
    _AVAILABLE = False


DEFAULT_TIMEOUT_S = 45.0  # Generous — first-call parquet downloads can be slow.


async def _run_sync_safe(fn, *args, timeout: float = DEFAULT_TIMEOUT_S, fn_name: str = ""):
    """Run a sync nfl-data-py call in a thread with timeout + error capture.

    Returns None on any failure (network error, missing season, timeout).
    Logs at warning level so we can spot real upstream issues in the access log.
    """
    if not _AVAILABLE:
        return None
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, functools.partial(fn, *args)),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning("nfl_data_py_timeout", fn=fn_name or getattr(fn, "__name__", "?"))
        return None
    except Exception as e:  # noqa: BLE001
        # Common case: requested season doesn't exist yet on nflverse.
        log.warning(
            "nfl_data_py_failed",
            fn=fn_name or getattr(fn, "__name__", "?"),
            error=str(e)[:200],
        )
        return None


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
        """Play-by-play. ~50MB per season; first call is slow — give it a longer leash."""
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
