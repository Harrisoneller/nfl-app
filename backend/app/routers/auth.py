"""Auth router — scaffold.

When MULTI_USER_MODE=false the routes still work (so you can flip the
flag at any time without redeploying), but the app accepts requests
without a token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db
from ..models.user import User
from ..schemas.auth import LoginRequest, SignupRequest, TokenResponse
from ..security import create_access_token, hash_password, verify_password

router = APIRouter()


@router.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=body.email,
        display_name=body.display_name,
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


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    s = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in_minutes=s.jwt_expire_minutes,
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }
