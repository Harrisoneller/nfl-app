"""Walk-forward refit of the spread residual. Offline. No database writes.

Train on regular-season games before each test season. Score 2016-2025
against the nflverse close (home margin, positive = home favored).

    cd backend && python -m scripts.refit_spread_residual
"""
from __future__ import annotations

import asyncio
import json

import pandas as pd

from app.services import ml_predictions_service
from app.services.spread_residual_refit import summarize, walk_forward

TRAIN_FROM = 2012
TEST_SEASONS = range(2016, 2026)


class _EmptyQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []


class _EmptyDb:
    def query(self, *_args, **_kwargs):
        return _EmptyQuery()


async def _load() -> pd.DataFrame:
    frames = []
    for season in range(TRAIN_FROM, TEST_SEASONS.stop):
        frame = await ml_predictions_service._build_features_for_season(_EmptyDb(), season)
        if frame is None or len(frame) == 0:
            print(f"skip {season}: no features", flush=True)
            continue
        reg = frame[frame["game_type"].astype(str).str.upper() == "REG"].copy()
        reg = reg.dropna(subset=["home_margin", "market_spread_home"])
        print(f"loaded {season}: {len(reg)} reg games", flush=True)
        frames.append(reg)
    if not frames:
        raise SystemExit("no regular-season games")
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    games = asyncio.run(_load())
    scored = walk_forward(games, test_seasons=TEST_SEASONS)
    summary = summarize(scored)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
