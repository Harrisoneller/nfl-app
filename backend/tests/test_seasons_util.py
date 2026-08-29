"""Tests for the seasons helper — pure logic, no DB."""
from datetime import date

from app.utils.seasons import (
    available_seasons,
    current_or_upcoming_season,
    is_season_upcoming,
    latest_completed_season,
)


def test_completed_season_after_super_bowl():
    # March 1 2026 → 2025 season is complete
    assert latest_completed_season(date(2026, 3, 1)) == 2025


def test_completed_season_before_super_bowl():
    # February 1 2026 → 2024 still the latest completed (SB hasn't happened)
    assert latest_completed_season(date(2026, 2, 1)) == 2024


def test_upcoming_is_one_ahead_of_completed():
    today = date(2026, 5, 1)
    assert current_or_upcoming_season(today) == latest_completed_season(today) + 1


def test_available_seasons_has_upcoming_first():
    today = date(2026, 5, 1)
    s = available_seasons(today)
    assert s[0] == current_or_upcoming_season(today)
    # Newest to oldest
    assert s == sorted(s, reverse=True)
    # Reaches all the way back to the start
    assert s[-1] == 2020


def test_is_season_upcoming():
    today = date(2026, 5, 1)
    assert is_season_upcoming(2026, today)
    assert not is_season_upcoming(2024, today)


def test_meta_default_is_current_or_upcoming(monkeypatch):
    """Team/player SeasonSelect reads /meta/seasons.default — that must be 2026
    once the upcoming season is the product surface, not last year's book."""
    from app.routers import meta as meta_router

    monkeypatch.setattr(meta_router, "available_seasons", lambda: [2026, 2025, 2024])
    monkeypatch.setattr(meta_router, "current_or_upcoming_season", lambda: 2026)
    monkeypatch.setattr(meta_router, "latest_completed_season", lambda: 2025)
    monkeypatch.setattr(meta_router, "season_info", lambda s: {"season": s})

    payload = meta_router.get_seasons()
    assert payload["default"] == 2026
    assert payload["current_or_upcoming"] == 2026
    assert payload["latest_completed"] == 2025
    assert payload["available"][0] == 2026
