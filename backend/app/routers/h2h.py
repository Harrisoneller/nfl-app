"""Head-to-head matchup endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..deps import get_db
from ..services import h2h_service, request_budget_service

router = APIRouter()


@router.get("/{team_a}/{team_b}")
async def get_h2h(
    team_a: str,
    team_b: str,
    response: Response,
    season: int | None = None,
    db: Session = Depends(get_db),
):
    """Everything the H2H page needs in one round-trip.

    Budget note: 25s is generous on purpose. A cold compute on a fresh container
    (no warmed nfl_data_py parquet cache, no L2 cache) realistically takes
    10–25s because it has to fetch season schedules, two seasons of PBP, plus 8
    seasons of betting history. After the very first miss writes to L2, every
    subsequent click is sub-100ms. The previous 3.2s budget was tight enough
    that the cold compute was repeatedly cancelled mid-flight, so the cache
    never populated and the page stayed permanently in fallback ("h2h
    temporarily unavailable") on production. See `h2h_service` for the
    shielded compute path that also keeps the work alive when this budget
    trips.
    """
    payload, budget = await request_budget_service.run_with_budget(
        budget_name="h2h.compose",
        timeout_ms=25000,
        execute=lambda: h2h_service.head_to_head(db, team_a, team_b, season),
        summary_fallback=lambda: {
            "team_a": team_a.upper(),
            "team_b": team_b.upper(),
            "season": season,
            "error": "h2h temporarily unavailable",
            "_budget": {"tier": "summary_fallback", "reason": "timeout"},
        },
    )
    cache_status = "miss"
    if isinstance(payload, dict):
        cache_meta = payload.get("_cache")
        if isinstance(cache_meta, dict) and cache_meta.get("served_stale"):
            cache_status = "stale"
        elif isinstance(cache_meta, dict):
            cache_status = "hit"
    request_cache_status = cache_status
    response.headers["X-Budget-Tier"] = str(budget.get("tier", "primary"))
    response.headers["X-Budget-Reason"] = str(budget.get("reason") or "")
    response.headers["X-Cache-Status"] = request_cache_status
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120"
    return payload
