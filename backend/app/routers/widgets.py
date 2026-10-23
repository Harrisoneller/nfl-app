from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models.user import User
from ..schemas.widget import PublicWidgetResponse, WidgetCreate, WidgetOut
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


@router.get("/{widget_id}/public", response_model=PublicWidgetResponse)
async def read_public_widget(
    widget_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
):
    """Fetch and render one widget for the anonymous share page.

    No bearer token. The id is a uuid4, so possession of the URL is the
    capability. A widget with ``is_public`` false is indistinguishable from
    a missing id. Listing, create, inline render, delete, and revoke stay
    authenticated.
    """
    widget = widget_service.get(db, widget_id)
    if widget is None or not widget.is_public:
        raise HTTPException(404, "widget not found")
    # Render the whitelisted spec so owner fields stored beside the
    # definition never become query parameters.
    spec = widget_service.public_spec(widget.spec if isinstance(widget.spec, dict) else {})
    data = await widget_service.render(db, spec)
    response.headers["Cache-Control"] = "public, max-age=60"
    return widget_service.public_view(widget, data)


@router.post("/{widget_id}/revoke")
def revoke_widget(
    widget_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Owner-only. Stop serving this widget on the public share URL."""
    if not widget_service.revoke_public(db, user.id, widget_id):
        raise HTTPException(404, "widget not found")
    return {"ok": True, "is_public": False}


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
