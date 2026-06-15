"""Bet tracker router — log bets, list them, settle, and read the CLV profile.

All routes require a real authenticated user (``get_current_user``). In
multi-user mode that means tracking is members-only by design — a bet log is
inherently per-person.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models.user import SYSTEM_USER_EMAIL, User
from ..schemas.bet import BetCreate, BetOut, BetProfileSummary
from ..services import bet_service

router = APIRouter()


def _guard_real_user(user: User) -> None:
    if user.email == SYSTEM_USER_EMAIL:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Sign in to track bets — the bet log is tied to your account.",
        )


@router.post("", response_model=BetOut, status_code=status.HTTP_201_CREATED)
def create_bet(
    body: BetCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BetOut:
    _guard_real_user(user)
    bet = bet_service.create_bet(db, user.id, body)
    # Best-effort: fill CLV / grade immediately if the game already settled.
    bet_service.settle_user_bets(db, user.id)
    db.refresh(bet)
    return _to_out(bet)


@router.get("", response_model=list[BetOut])
def list_bets(
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[BetOut]:
    _guard_real_user(user)
    bets = bet_service.list_bets(db, user.id, status=status_filter)
    return [_to_out(b) for b in bets]


@router.post("/settle", response_model=dict)
def settle(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _guard_real_user(user)
    return bet_service.settle_user_bets(db, user.id)


@router.get("/profile", response_model=BetProfileSummary)
def profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BetProfileSummary:
    _guard_real_user(user)
    # Settle first so the profile always reflects the latest finals + CLV.
    bet_service.settle_user_bets(db, user.id)
    return BetProfileSummary(**bet_service.profile_summary(db, user.id))


@router.delete("/{bet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bet(
    bet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    _guard_real_user(user)
    try:
        bid = uuid.UUID(bet_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bet not found") from None
    if not bet_service.delete_bet(db, user.id, bid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bet not found")


def _to_out(bet) -> BetOut:
    return BetOut.model_validate(bet)
