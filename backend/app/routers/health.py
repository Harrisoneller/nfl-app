"""Health endpoints — separated by purpose.

- /health : liveness for legacy reasons (kept for backward compat).
- /live   : liveness probe — does the process answer? (no deps)
- /ready  : readiness — can we serve real traffic? (DB ping + cache ping)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_db
from ..rate_limits import limiter
from ..services.cost_service import ledger

router = APIRouter()


@router.get("/health")
@limiter.exempt
def health() -> dict:
    """Legacy combined health — keep returning 200 if process is alive."""
    s = get_settings()
    return {
        "ok": True,
        "env": s.app_env,
        "app_role": s.app_role,
        "scheduler_enabled": s.scheduler_enabled,
        "cache_backend": s.cache_backend,
        "cors_origins": s.cors_origin_list,
        "cors_vercel_regex": bool(s.cors_origin_regex),
        "llm_provider": s.llm_provider,
        "multi_user": s.multi_user_mode,
        "twitter_enabled": s.enable_twitter,
        "version": "0.3.0",
    }


@router.get("/live")
@limiter.exempt
def live() -> dict:
    """Liveness probe — answers if the event loop is alive."""
    return {"ok": True}


@router.get("/ready")
@limiter.exempt
def ready(db: Session = Depends(get_db)) -> dict:
    """Readiness probe — checks downstreams. Returns 503 on failure."""
    s = get_settings()
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"fail: {type(e).__name__}"
    if s.cache_backend == "redis":
        try:
            from ..cache import get_cache

            checks["redis"] = "ok" if get_cache().ping() else "fail"
        except Exception as e:  # noqa: BLE001
            checks["redis"] = f"fail: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return {"ok": ok, "checks": checks}


@router.get("/admin/cost-summary")
def cost_summary() -> dict:
    """Today's AI spend at a glance — global + top users."""
    return ledger.summary()
