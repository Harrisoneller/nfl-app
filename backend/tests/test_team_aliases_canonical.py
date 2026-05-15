"""Canonical-team-id mapping tests."""
from app.utils.teams import canonical_team


def test_passthrough_known_ids():
    assert canonical_team("PHI") == "PHI"
    assert canonical_team("SF") == "SF"


def test_legacy_abbreviations():
    assert canonical_team("WSH") == "WAS"
    assert canonical_team("JAC") == "JAX"
    assert canonical_team("LA") == "LAR"
    assert canonical_team("OAK") == "LV"
    assert canonical_team("SD") == "LAC"


def test_lowercase_and_whitespace():
    assert canonical_team("  phi  ") == "PHI"


def test_none_and_empty():
    assert canonical_team(None) is None
    assert canonical_team("") is None
