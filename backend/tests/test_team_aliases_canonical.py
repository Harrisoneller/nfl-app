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


def test_full_city_nickname_maps_to_id():
    assert canonical_team("Kansas City Chiefs") == "KC"
    assert canonical_team("San Francisco 49ers") == "SF"
    assert canonical_team("New York Jets") == "NYJ"
    assert canonical_team("New York Giants") == "NYG"
    assert canonical_team("Los Angeles Rams") == "LAR"
    assert canonical_team("Los Angeles Chargers") == "LAC"


def test_nickname_and_unique_city_map_to_id():
    assert canonical_team("Chiefs") == "KC"
    assert canonical_team("49ers") == "SF"
    assert canonical_team("Kansas City") == "KC"
    assert canonical_team("Buffalo") == "BUF"


def test_shared_city_names_do_not_collapse_two_clubs():
    # "New York" and "Los Angeles" each cover two teams — they must not
    # silently resolve to one of them.
    assert canonical_team("New York") == "NEW YORK"
    assert canonical_team("Los Angeles") == "LOS ANGELES"


def test_historical_and_odds_api_aliases():
    assert canonical_team("Oakland Raiders") == "LV"
    assert canonical_team("San Diego Chargers") == "LAC"
    assert canonical_team("Washington Football Team") == "WAS"
    assert canonical_team("NY Jets") == "NYJ"
