"""Admin-only model rerun endpoints — recompute game + player outputs.

Backs /admin → Reruns:

* ``POST /admin/rerun``         kick off a background rerun (scope-selectable)
* ``GET  /admin/rerun/status``  running flag + recent run history (UI poll)

A rerun is the "make my tuning live everywhere" button: it evicts the season
Monte-Carlo sim (which isn't param-versioned) and rewarms the game slate and
player boards, so playoff odds and start/sit numbers reflect the latest params
immediately instead of waiting on a TTL or the nightly cron. The ``full`` scope
also rebuilds Elo + profiles so K-factor / spread-conversion changes land.

Execution is asynchronous (background task + DataSyncRun tracking); the trigger
returns right away and the UI polls ``/status``. Only one rerun runs at a time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models.user import User
from ..services import model_rerun_service

router = APIRouter(dependencies=[Depends(require_admin)])


class RerunRequest(BaseModel):
    scope: str = Field(default="quick")
    season: int | None = None
    week: int | None = Field(default=None, ge=1, le=23)


@router.post("")
async def trigger_rerun(
    body: RerunRequest,
    admin: User = Depends(require_admin),
):
    try:
        return await model_rerun_service.trigger(
            body.scope, actor=admin.email or "", season=body.season, week=body.week,
        )
    except ValueError as e:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(http_status.HTTP_409_CONFLICT, str(e)) from None


@router.get("/status")
def rerun_status(db: Session = Depends(get_db)):
    return model_rerun_service.status(db)
