from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models.user import User
from ..schemas.widget import WidgetCreate, WidgetOut
from ..services import widget_service

router = APIRouter()


@router.get("", response_model=list[WidgetOut])
def list_widgets(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return widget_service.list_for_user(db, user.id)


@router.post("", response_model=WidgetOut)
def create_widget(
    body: WidgetCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return widget_service.create(db, user.id, body.spec, pinned=body.pinned)


@router.delete("/{widget_id}")
def delete_widget(
    widget_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not widget_service.delete(db, user.id, widget_id):
        raise HTTPException(404, "widget not found")
    return {"ok": True}


@router.get("/{widget_id}/render")
async def render_widget(
    widget_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    widgets = widget_service.list_for_user(db, user.id)
    w = next((x for x in widgets if x.id == widget_id), None)
    if w is None:
        raise HTTPException(404, "widget not found")
    payload = await widget_service.render(db, w.spec)
    return {"widget": w.spec, "data": payload}


@router.post("/render")
async def render_inline(
    spec: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Render a spec without persisting — used by the AI chat preview."""
    return {"widget": spec, "data": await widget_service.render(db, spec)}
