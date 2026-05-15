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
