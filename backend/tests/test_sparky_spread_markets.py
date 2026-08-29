"""Regression: spread and total legs must be built from odds_lines.

The board used to drop every spread and total in two independent ways that
looked identical from the UI:

1. Team-name matching required an exact string match between the Odds API
   outcome label ("Kansas City Chiefs") and the Sparky prediction's
   ``home_team`` ("Kansas City"). A miss meant ``_modal_line`` saw no two-way
   market and no spread leg was constructed, even though odds_lines was full.
2. ``_game_inputs`` refused to even look at odds_lines unless a model
   distribution was already persisted. A slate built while the prediction
   store was cold therefore stayed moneyline-only forever.

These tests pin both: names that would have failed exact-match still produce
a spread market, and a prediction row with no dist blob still produces
spread and total legs from the odds board.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import sparky_parlay_service as S
from app.services.sparky import legs as L


def _line(*, label, point, price=-110, book="dk", home=None, away=None):
    return SimpleNamespace(
        label=label, point=point, price=price, bookmaker=book,
        home_team=home, away_team=away, event_id="e1", market="spreads",
    )


def _pred(**kw):
    market = {
        "home_ml": -260, "away_ml": 210, "book_count": 6,
        "home_win_prob_ensemble": 0.72, "favorite": "home",
        "spread_home": -7.5, "total": 52.5, "dist": {},
    }
    market.update(kw.pop("market", {}) if "market" in kw else {})
    base = dict(
        event_id="e1",
        home_team="Kansas City Chiefs",
        away_team="San Francisco 49ers",
        home_team_id="KC",
        away_team_id="SF",
        win_prob=0.72,
        predicted_winner="KC",
        pred_margin=None,
        pred_total=None,
        market=market,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _odds(home_label="Kansas City Chiefs", away_label="San Francisco 49ers",
          home_event="Kansas City Chiefs", away_event="San Francisco 49ers"):
    return {
        ("e1", "spreads"): [
            _line(label=home_label, point=-7.5, home=home_event, away=away_event),
            _line(label=away_label, point=7.5, home=home_event, away=away_event,
                  price=-108, book="fd"),
        ],
        ("e1", "totals"): [
            _line(label="Over", point=52.5, price=-105),
            _line(label="Under", point=52.5, price=-115, book="fd"),
        ],
    }


# --------------------------------------------------------------------------- #
# Name matching
# --------------------------------------------------------------------------- #


def test_exact_odds_api_names_group_as_one_spread_market():
    grouped = S._lines_from_points(
        _odds()[("e1", "spreads")],
        "Kansas City Chiefs", "San Francisco 49ers",
    )
    assert -7.5 in grouped
    assert grouped[-7.5]["a_prices"]
    assert grouped[-7.5]["b_prices"]
    assert S._modal_line(grouped) == -7.5


def test_offshore_quotes_are_dropped_from_spread_grouping():
    """Pinnacle/Bovada numbers must not become Sparky's 'best' price."""
    rows = [
        _line(label="Kansas City Chiefs", point=-7.5, book="Pinnacle", price=-105),
        _line(label="San Francisco 49ers", point=7.5, book="Pinnacle", price=-115),
        _line(label="Kansas City Chiefs", point=-7.5, book="DraftKings"),
        _line(label="San Francisco 49ers", point=7.5, book="FanDuel", price=-108),
        _line(label="Kansas City Chiefs", point=-7.5, book="Bovada", price=-102),
    ]
    grouped = S._lines_from_points(
        rows, "Kansas City Chiefs", "San Francisco 49ers",
    )
    books = grouped[-7.5]["books"]
    assert "DraftKings" in books
    assert "FanDuel" in books
    assert "Pinnacle" not in books
    assert "Bovada" not in books


def test_short_city_name_still_matches_odds_api_full_label():
    """The bug: pred.home_team is 'Kansas City', the book's label is the full string."""
    grouped = S._lines_from_points(
        _odds()[("e1", "spreads")],
        "Kansas City", "San Francisco",
    )
    assert S._modal_line(grouped) == -7.5


def test_row_home_team_is_preferred_over_prediction_name():
    """Even if the prediction name is unusable, the OddsLine's own home/away match."""
    grouped = S._lines_from_points(
        _odds()[("e1", "spreads")],
        "HOME", "AWAY",  # would fail exact-match and id-match
    )
    assert S._modal_line(grouped) == -7.5


def test_case_does_not_drop_a_spread():
    rows = [
        _line(label="san francisco 49ers", point=-3.5,
              home="San Francisco 49ers", away="Seattle Seahawks"),
        _line(label="Seattle Seahawks", point=3.5,
              home="San Francisco 49ers", away="Seattle Seahawks"),
    ]
    grouped = S._lines_from_points(rows, "San Francisco 49ers", "Seattle Seahawks")
    assert S._modal_line(grouped) == -3.5


def test_unmatched_label_is_skipped_not_assigned_to_the_wrong_side():
    rows = [
        _line(label="Mystery Squad", point=21.5,
              home="Kansas City Chiefs", away="Mystery Squad"),
        _line(label="Someone Else", point=-21.5,
              home="Kansas City Chiefs", away="Mystery Squad"),
    ]
    grouped = S._lines_from_points(rows, "Kansas City Chiefs", "Mystery Squad")
    # Only the home side matched, so there is no two-way market at 21.5.
    modal = S._modal_line(grouped)
    assert modal is None


def test_canonical_match_without_oddsline_home_away():
    """If the row has no home/away, city vs full name still has to match via ids."""
    rows = [
        _line(label="Kansas City Chiefs", point=-7.5, home=None, away=None),
        _line(label="San Francisco 49ers", point=7.5, home=None, away=None),
    ]
    grouped = S._lines_from_points(rows, "Kansas City", "San Francisco")
    assert S._modal_line(grouped) == -7.5


def test_same_team_maps_city_nickname_and_id():
    assert S._same_team("Kansas City", "Kansas City Chiefs")
    assert S._same_team("KC", "Kansas City Chiefs")
    assert S._same_team("Chiefs", "Kansas City Chiefs")
    assert S._same_team("kansas city chiefs", "Kansas City")
    assert S._same_team("JAC", "JAX")
    assert S._same_team("WAS", "WSH")
    assert not S._same_team("Kansas City Chiefs", "San Francisco 49ers")
    assert not S._same_team("New York Jets", "New York Giants")
    assert not S._same_team("Los Angeles Rams", "Los Angeles Chargers")


# --------------------------------------------------------------------------- #
# _game_inputs: odds present, model dist missing
# --------------------------------------------------------------------------- #


def test_spread_and_total_legs_are_built_without_a_persisted_dist():
    """Cold prediction store must not blank the spread/total markets."""
    gi = S._game_inputs(_pred(), _odds())
    assert gi is not None
    assert gi.spread_prices is not None
    assert gi.spread_prices.line == -7.5
    assert gi.model_spread is not None
    assert gi.total_prices is not None
    assert gi.total_prices.line == 52.5
    assert gi.model_total is not None

    legs = L.build_game_legs(gi)
    markets = {leg.market for leg in legs}
    assert "spread" in markets
    assert "total" in markets
    assert "moneyline" in markets
    labels = {leg.label for leg in legs if leg.market == "spread"}
    assert any("KC" in x and "-" in x for x in labels)
    assert any("SF" in x and "+" in x for x in labels)


def test_short_prediction_names_still_produce_spread_legs():
    gi = S._game_inputs(
        _pred(home_team="Kansas City", away_team="San Francisco"),
        _odds(),
    )
    assert gi is not None
    assert gi.spread_prices is not None
    assert gi.model_spread is not None
    legs = L.build_game_legs(gi)
    assert any(leg.market == "spread" for leg in legs)


def test_market_centered_fallback_puts_cover_prob_near_half():
    """With no model view the centre sits on the market line, so there is no
    invented edge — the legs exist so they can be clicked, not so they look +EV.
    """
    gi = S._game_inputs(_pred(), _odds())
    assert gi.model_spread is not None
    m = gi.model_spread.normalized()
    room = m.a + m.b
    assert m.a / room == pytest.approx(0.5, abs=0.01)


def test_persisted_dist_is_preferred_over_the_market_line():
    """A real model view has to survive — otherwise every spread is a coin flip."""
    pred = _pred(
        pred_margin=21.0, pred_total=55.0,
        market={
            "home_ml": -260, "away_ml": 210, "book_count": 6,
            "home_win_prob_ensemble": 0.78, "favorite": "home",
            "spread_home": -7.5, "total": 52.5,
            "dist": {
                "expected_margin": 21.0, "expected_total": 55.0,
                "margin_sd": 15.5, "total_sd": 11.0, "margin_total_rho": 0.25,
            },
        },
    )
    gi = S._game_inputs(pred, _odds())
    m = gi.model_spread.normalized()
    room = m.a + m.b
    # Home favoured by 21 against a -7.5 line is well above a coin flip,
    # even after NFL key-number mass at 3 and 7 pulls the discrete PMF inward.
    assert m.a / room > 0.55
    # And it is not the market-centered fallback (that one is ~50%).
    assert m.a / room != pytest.approx(0.5, abs=0.02)


def test_none_valued_dist_blob_does_not_shadow_column_fallback():
    """``.get(key, default)`` returns None when the key exists with value None."""
    pred = _pred(
        pred_margin=10.0, pred_total=50.0,
        market={
            "home_ml": -180, "away_ml": 155, "book_count": 5,
            "home_win_prob_ensemble": 0.64, "favorite": "home",
            "spread_home": -3.5, "total": 48.5,
            "dist": {
                "expected_margin": None, "expected_total": None,
                "margin_sd": None, "total_sd": None, "margin_total_rho": None,
            },
        },
    )
    gd = S._distribution(pred)
    assert gd is not None
    assert gd.mu_m == pytest.approx(10.0)
    assert gd.mu_t == pytest.approx(50.0)


def test_spread_legs_build_when_totals_are_missing():
    """A missing total quote must not take the spread market down with it."""
    odds = {("e1", "spreads"): _odds()[("e1", "spreads")]}
    gi = S._game_inputs(_pred(), odds)
    assert gi is not None
    assert gi.spread_prices is not None
    assert gi.model_spread is not None
    assert gi.total_prices is None
    legs = L.build_game_legs(gi)
    assert any(leg.market == "spread" for leg in legs)
    assert not any(leg.market == "total" for leg in legs)


def test_persisted_margin_survives_a_missing_total():
    """A real model margin must not be inverted just because total is absent."""
    pred = _pred(
        pred_margin=21.0, pred_total=None,
        market={
            "home_ml": -260, "away_ml": 210, "book_count": 6,
            "home_win_prob_ensemble": 0.78, "favorite": "home",
            "spread_home": -7.5, "total": None,
            "dist": {
                "expected_margin": 21.0, "expected_total": None,
                "margin_sd": 15.5, "total_sd": None, "margin_total_rho": None,
            },
        },
    )
    gi = S._game_inputs(pred, {("e1", "spreads"): _odds()[("e1", "spreads")]})
    m = gi.model_spread.normalized()
    room = m.a + m.b
    assert m.a / room > 0.55
    assert m.a / room != pytest.approx(0.5, abs=0.02)


def test_priced_out_keeps_consensus_spreads_ahead_of_alts():
    """The cart sources priced_out. Alts must not crowd the main number out."""
    from app.services import sparky_value_service as VS

    def wrap(is_alt: bool, key: str):
        return SimpleNamespace(leg=SimpleNamespace(is_alt=is_alt, key=key))

    passed = [wrap(True, f"alt{i}") for i in range(8)]
    passed.append(wrap(False, "main"))
    out = VS._priced_out_slice(passed, limit=3)
    assert out[0].leg.key == "main"
    assert all(p.leg.is_alt for p in out[1:])
    assert len(out) == 3


# --------------------------------------------------------------------------- #
# Unresolved / non-NFL games stay off Sparky
# --------------------------------------------------------------------------- #


def test_unresolved_opponent_does_not_build_legs():
    """A game whose away id never resolved must not produce a priceable Sparky leg."""
    pred = _pred(away_team_id=None, away_team="Mystery Squad")
    assert S._game_inputs(pred, _odds()) is None


def test_two_unmodeled_sides_do_not_build_legs():
    pred = _pred(
        home_team_id=None, away_team_id=None,
        home_team="Mystery Home", away_team="Mystery Away",
    )
    assert S._game_inputs(pred, _odds()) is None


def test_nfl_only_drops_unresolved_games_and_keeps_modeled_ones():
    nfl = _pred()
    unresolved = _pred(event_id="e2", away_team_id=None, away_team="Mystery Squad")
    kept = S.nfl_only([nfl, unresolved])
    assert kept == [nfl]
    assert S.fbs_only([nfl, unresolved]) == [nfl]
