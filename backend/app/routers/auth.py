"""Auth router — email/password signup, login, profile.

When MULTI_USER_MODE=false the routes still work (so you can flip the
flag at any time without redeploying), but protected app routes resolve
to the seeded system user without a bearer token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db
from ..models.user import SYSTEM_USER_EMAIL, User
from ..rate_limits import limiter
from ..schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserProfile,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter()


def _effective_is_admin(user: User) -> bool:
    """Resolve the *effective* admin flag the frontend should trust.

    Mirrors ``require_admin`` in ``deps.py`` exactly so the UI's tab gate and
    the backend's route gate stay in lockstep. When ``ADMIN_EMAILS`` is set,
    only those emails are admin (even in single-user mode where everyone
    otherwise resolves to ``system@local`` with ``is_admin=True``). When
    blank, falls back to the DB column.
    """
    allow = get_settings().admin_email_set
    email = (user.email or "").strip().lower()
    if allow:
        return email in allow
    return bool(user.is_admin)


def _user_profile(user: User) -> UserProfile:
    return UserProfile(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        is_admin=_effective_is_admin(user),
    )


def _register_user(body: SignupRequest, db: Session) -> TokenResponse:
    email = body.email.lower().strip()
    if email == SYSTEM_USER_EMAIL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email not available")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=email,
        display_name=(body.display_name or "").strip(),
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    s = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in_minutes=s.jwt_expire_minutes,
    )


@router.post("/signup", response_model=TokenResponse)
@router.post("/register", response_model=TokenResponse, include_in_schema=True)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    return _register_user(body, db)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(get_settings().rate_limit_auth)
def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = body.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account inactive")
    s = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in_minutes=s.jwt_expire_minutes,
    )


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)) -> UserProfile:
    return _user_profile(user)


@router.patch("/me", response_model=UserProfile)
def update_me(
    body: UpdateProfileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserProfile:
    if user.email == SYSTEM_USER_EMAIL:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System account cannot be updated")
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_profile(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, bool]:
    if user.email == SYSTEM_USER_EMAIL:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System account cannot be updated")
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    db.add(user)
    db.commit()
    return {"ok": True}
