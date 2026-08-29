"""Sparky may only quote major US retail books."""
from __future__ import annotations

import pytest

from app.services.sparky.books import is_major_book


@pytest.mark.parametrize(
    "name",
    [
        "DraftKings", "draftkings", "dk", "DK",
        "FanDuel", "fanduel", "fd",
        "BetMGM", "betmgm",
        "Caesars", "williamhill_us",
        "ESPN BET", "espnbet",
        "Fanatics",
        "Hard Rock Bet",
        "bet365",
        "BetRivers",
        "PointsBet", "PointsBet (US)",
        "SuperBook",
        "Unibet",
    ],
)
def test_major_us_books_are_allowed(name: str) -> None:
    assert is_major_book(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Pinnacle",
        "Bovada",
        "LowVig",
        "LowVig.ag",
        "BetOnline.ag",
        "betonlineag",
        "MyBookie.ag",
        "Circa",
        "Bookmaker",
        "Heritage",
        "Betfair",
        "Fliff",
        None,
        "",
        "unknown",
    ],
)
def test_offshore_and_sharp_books_are_excluded(name) -> None:
    assert is_major_book(name) is False
