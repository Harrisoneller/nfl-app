from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db
from ..models.user import User
from ..rate_limits import limiter
from ..schemas.ai import ChatRequest, ChatResponse, WidgetBuildRequest
from ..schemas.widget import WidgetSpec
from ..services import ai_service, widget_service
from ..services.cost_service import BudgetExceeded

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(get_settings().rate_limit_ai)
async def chat(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await ai_service.chat(
            db,
            user_id=user.id,
            user_message=body.message,
            session_id=body.session_id,
            enable_tools=body.enable_tools,
        )
    except BudgetExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return result


@router.post("/widgets", response_model=WidgetSpec)
@limiter.limit(get_settings().rate_limit_ai)
async def build_widget(
    request: Request,
    body: WidgetBuildRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    spec = await ai_service.build_widget_only(body.prompt)
    if body.save:
        widget_service.create(db, user.id, spec, pinned=False)
    return spec
