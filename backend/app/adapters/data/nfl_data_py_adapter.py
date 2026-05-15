"""nfl-data-py adapter.

Wraps the synchronous nfl-data-py library. Calls are run in a thread
because the library is sync and can be slow on first hit (it caches
parquet files locally under ~/.nfl_data_py_cache).

Caching note: we DataFrame.to_dict only at the public boundary so callers
can keep working with DataFrames internally when desired.
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


def _run_sync(fn, *args, **kwargs):
    return asyncio.get_event_loop().run_in_executor(None, functools.partial(fn, *args, **kwargs))


class NflDataPyAdapter:
    available = _AVAILABLE

    # ---- raw frame accessors -------------------------------------------------

    async def weekly_df(self, season: int):
        if not _AVAILABLE:
            return None
        return await _run_sync(nfl.import_weekly_data, [season])

    async def seasonal_df(self, season: int):
        if not _AVAILABLE:
            return None
        return await _run_sync(nfl.import_seasonal_data, [season])

    async def rosters_df(self, season: int):
        if not _AVAILABLE:
            return None
        return await _run_sync(nfl.import_seasonal_rosters, [season])

    async def schedules_df(self, season: int):
        if not _AVAILABLE:
            return None
        return await _run_sync(nfl.import_schedules, [season])

    async def pbp_df(self, season: int):
        """Play-by-play. ~50MB per season; first call is slow."""
        if not _AVAILABLE:
            return None
        return await _run_sync(nfl.import_pbp_data, [season])

    # ---- dict variants for places that don't need DataFrames -----------------

    async def weekly_player_stats(self, season: int) -> list[dict[str, Any]]:
        df = await self.weekly_df(season)
        return df.to_dict(orient="records") if df is not None else []

    async def seasonal_player_stats(self, season: int) -> list[dict[str, Any]]:
        df = await self.seasonal_df(season)
        return df.to_dict(orient="records") if df is not None else []

    async def rosters(self, season: int) -> list[dict[str, Any]]:
        df = await self.rosters_df(season)
        return df.to_dict(orient="records") if df is not None else []

    async def schedules(self, season: int) -> list[dict[str, Any]]:
        df = await self.schedules_df(season)
        return df.to_dict(orient="records") if df is not None else []
