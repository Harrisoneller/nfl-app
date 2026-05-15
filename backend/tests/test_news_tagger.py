"""Tests for news team tagging — pure logic."""
from app.utils.team_aliases import tags_for_text


def test_full_team_name():
    assert "PHI" in tags_for_text("Philadelphia Eagles win NFC East")


def test_nickname_alone():
    assert "PHI" in tags_for_text("Eagles offense rolling")
    assert "SF" in tags_for_text("49ers defense looks elite")


def test_multiple_teams_in_one_headline():
    tags = tags_for_text("Eagles beat 49ers in NFC Championship rematch")
    assert "PHI" in tags
    assert "SF" in tags


def test_empty_input():
    assert tags_for_text("") == []
    assert tags_for_text(None) == []  # type: ignore[arg-type]


def test_no_team_mentioned():
    assert tags_for_text("League announces new instant replay rules") == []
