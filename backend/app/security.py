"""JWT + password helpers.

Lightweight enough that we can swap in Supabase/Clerk later by replacing
just `get_current_user`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return _pwd.verify(plain, hashed)


def create_access_token(sub: str | uuid.UUID, **extra: Any) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(sub),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.jwt_expire_minutes)).timestamp()),
        **extra,
    }
    return jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.secret_key, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None
