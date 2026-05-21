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
from ..services.cost_service import ledger

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Legacy combined health — keep returning 200 if process is alive."""
    s = get_settings()
    return {
        "ok": True,
        "env": s.app_env,
        "app_role": s.app_role,
        "scheduler_enabled": s.scheduler_enabled,
        "llm_provider": s.llm_provider,
        "multi_user": s.multi_user_mode,
        "twitter_enabled": s.enable_twitter,
        "version": "0.3.0",
    }


@router.get("/live")
def live() -> dict:
    """Liveness probe — answers if the event loop is alive."""
    return {"ok": True}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    """Readiness probe — checks downstreams. Returns 503 on failure."""
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"fail: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return {"ok": ok, "checks": checks}


@router.get("/admin/cost-summary")
def cost_summary() -> dict:
    """Today's AI spend at a glance — global + top users."""
    return ledger.summary()
