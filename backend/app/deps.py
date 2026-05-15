"""FastAPI dependencies — DB session and current user.

When MULTI_USER_MODE=false the app always resolves to the seeded
`system@local` user. Flip the flag to enable real auth.
"""
from __future__ import annotations

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

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inactive or unknown user")
    return user
