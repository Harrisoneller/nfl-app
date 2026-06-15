"""FastAPI dependencies — DB session and current user.

When MULTI_USER_MODE=false the app always resolves to the seeded
`system@local` user. Flip the flag to enable real auth.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models.user import SYSTEM_USER_EMAIL, User
from .security import decode_token


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_or_create_system_user(db: Session) -> User:
    user = db.query(User).filter(User.email == SYSTEM_USER_EMAIL).first()
    if user is None:
        user = User(email=SYSTEM_USER_EMAIL, display_name="System", is_admin=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    settings = get_settings()
    if not settings.multi_user_mode:
        return _get_or_create_system_user(db)

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inactive or unknown user")
    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    """Resolve the caller if a valid bearer token is present, else fall back to
    the seeded ``system@local`` user.

    Used by routes that should stay public even when ``MULTI_USER_MODE`` is on
    (AI chat, widget builder, fantasy advice). Logged-in users get their own
    identity (and their own AI sessions); anonymous visitors transparently
    share the system account instead of hitting a 401.
    """
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        payload = decode_token(token)
        if payload and "sub" in payload:
            try:
                user_id = uuid.UUID(str(payload["sub"]))
            except ValueError:
                user_id = None
            if user_id is not None:
                user = db.query(User).filter(User.id == user_id).first()
                if user and user.is_active:
                    return user
    return _get_or_create_system_user(db)


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate a route to admin-only.

    Logic
    -----
    * If ``ADMIN_EMAILS`` is set, the caller's email MUST be in that allowlist.
      This is the strict path and works even in single-user mode (where
      ``get_current_user`` would otherwise resolve everyone to the seeded
      ``system@local`` admin user).
    * If ``ADMIN_EMAILS`` is unset, we fall back to the DB ``is_admin`` flag
      — same behavior as before.

    Apply with ``Depends(require_admin)`` on any route that should be
    restricted, e.g. Sparky's ``/admin/*`` endpoints.
    """
    settings = get_settings()
    allow = settings.admin_email_set
    email = (user.email or "").strip().lower()

    if allow:
        if email not in allow:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    elif not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")

    return user
