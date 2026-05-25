from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator

MIN_PASSWORD_LEN = 8


def _validate_password(v: str) -> str:
    if len(v) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    return v


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        return _validate_password(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_min_length(cls, v: str) -> str:
        return _validate_password(v)


class UserProfile(BaseModel):
    id: str
    email: str
    display_name: str
    is_admin: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
