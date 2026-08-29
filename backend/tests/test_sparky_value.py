"""Tests for the Value Board — the layer that decides what is worth betting.

These are pure (no DB, no network), matching the rest of the Sparky engine
tests so they run in the Linux sandbox where this app's macOS venv is unusable.

The regressions that matter here are behavioural, not arithmetic. The bug this
layer exists to fix was that the dashboard ranked by confidence, so extreme
favourites sat at the top of a board whose whole purpose is finding bets. So the
tests assert the *product* properties directly:

  - heavy chalk never gets recommended, and says why;
  - chalk is rejected by the trust layer and the price floor, NOT by the
    ranking — there is a test pinning that distinction, because assuming
    growth-ranking demotes chalk is the easy mistake here and it is wrong;
  - the board covers spreads and totals, not just moneylines;
  - break-even, EV and push accounting agree with closed-form values;
  - the band-aware shrinkage actually discounts chalk more than the middle.
"""
from __future__ import annotations

import math

import pytest

from app.services.sparky import legs as L
from app.services.sparky import odds_math as om
from app.services.sparky import shrinkage as S
from app.services.sparky import value as V


def _leg(
    price: int,
    prob: float,
    fair: float,
    *,
    market: str = L.MARKET_MONEYLINE,
    side: str = "home",
    push: float = 0.0,
    n_books: int = 6,
    line: float | None = None,
    label: str = "TEAM",
) -> L.LegCandidate:
    return L.LegCandidate(
        key=f"e:{market}:{side}",
        event_id="e",
        market=market,
        side=side,
        label=label,
        team_id="TEAM",
        opponent_id="OPP",
        line=line,
        price_american=price,
        decimal_odds=om.american_to_decimal(price),
        book="DK",
        fair_prob=fair,
        model_prob=prob,
        prob=prob,
        push_prob=push,
        edge=prob - fair,
        expected_value=0.0,
        tau=0.14,
        loadings=(0.5, 0.2, 0.0),
        is_favorite=price < 0,
        n_books=n_books,
    )


# --------------------------------------------------------------------------- #
# Closed-form checks
# --------------------------------------------------------------------------- #


def test_breakeven_probability_matches_the_no_vig_implied_price():
    """With no push, break-even is exactly 1/decimal — the vig-included implied."""
    for price in (-250, -110, 100, 145, 600):
        dec = om.american_to_decimal(price)
        assert V.breakeven_probability(dec, 0.0) == pytest.approx(
            om.american_to_implied(price), abs=1e-12
        )


def test_push_mass_lowers_the_win_probability_needed_to_break_even():
    dec = om.american_to_decimal(-110)
    assert V.breakeven_probability(dec, 0.08) < V.breakeven_probability(dec, 0.0)
    # A push refunds the stake, so it costs nothing: p_needed = (1 - push)/dec.
    assert V.breakeven_probability(dec, 0.08) == pytest.approx((1 - 0.08) / dec, abs=1e-12)


def test_return_moments_agree_with_a_hand_computed_distribution():
    dec = 1.91
    mean, sd = V.return_moments(0.55, 0.05, dec)
    assert mean == pytest.approx(0.55 * dec + 0.05, abs=1e-12)
    second = 0.55 * dec ** 2 + 0.05
    assert sd == pytest.approx(math.sqrt(second - mean ** 2), abs=1e-12)


def test_growth_rate_is_zero_for_a_negative_ev_bet():
    mean, sd = V.return_moments(0.45, 0.0, 1.91)
    assert mean < 1.0
    assert V.growth_rate(mean, sd) == 0.0


def test_cents_of_value_is_continuous_across_the_odds_boundary():
    """-110 vs +100 is ten cents; a raw American subtraction says 210."""
    assert V._cents_of_value(-110, 100) == -10
    assert V._cents_of_value(105, -105) == 10
    assert V._cents_of_value(160, 140) == 20
    assert V._cents_of_value(-400, -300) == -100


# --------------------------------------------------------------------------- #
# Tail uncertainty
# --------------------------------------------------------------------------- #


def test_tail_inflation_is_identity_at_a_pickem_and_grows_toward_the_ends():
    tau = 0.14
    assert V.tail_inflated_tau(tau, 0.5, 1.5) == pytest.approx(tau, abs=1e-12)
    assert V.tail_inflated_tau(tau, 0.75, 1.5) > tau
    assert V.tail_inflated_tau(tau, 0.95, 1.5) > V.tail_inflated_tau(tau, 0.75, 1.5)


def test_zero_inflation_reproduces_flat_tau_exactly():
    for p in (0.05, 0.4, 0.62, 0.97):
        assert V.tail_inflated_tau(0.2, p, 0.0) == pytest.approx(0.2, abs=1e-12)


# --------------------------------------------------------------------------- #
# The chalk regression — the reason this module exists
# --------------------------------------------------------------------------- #


def test_extreme_favorite_is_never_recommended_even_when_nominally_plus_ev():
    """A -2000 moneyline with a real 1% edge is still not a bet.

    This is the exact shape that used to top the dashboard as an "anchor".
    """
    pick = V.score_leg(
        _leg(-2000, 0.962, 0.9524), tau=0.14, band=S.BAND_CHALK,
    )
    assert pick.ev > 0                      # it really is +EV on paper
    assert pick.tier == V.TIER_PASS
    assert pick.is_recommended is False
    assert pick.stake_units == 0.0          # no stake shown next to "no bet"
    assert any("break even" in r for r in pick.reasons)


def test_priced_out_chalk_still_explains_itself():
    pick = V.score_leg(_leg(-600, 0.90, 0.857), tau=0.14, band=S.BAND_CHALK)
    text = V.explain(pick)
    assert "no bet" not in text.lower()
    assert "play " not in text.lower()
    assert "-600" in text


def test_growth_alone_does_not_demote_chalk():
    """Documents the thing it is tempting to assume and wrong to rely on.

    If a heavy favourite's edge were real, it would compound a bankroll faster
    than a bigger dog edge, because it is collected at a fraction of the
    variance. Growth rate says so, correctly. Chalk is rejected by the trust
    layer and the price floor instead — never by the ranking. If this test ever
    starts failing, someone has quietly moved gating into the ranking.
    """
    chalk = V.score_leg(_leg(-500, 0.87, 0.833), tau=0.14, band=S.BAND_CHALK)
    dog = V.score_leg(_leg(150, 0.44, 0.42), tau=0.14, band=S.BAND_MID)
    assert chalk.ev < dog.ev                    # the dog has the bigger EV
    assert chalk.growth_rate > dog.growth_rate  # and still grows slower
    # But the tier gate, not the ranking, is what puts the dog on top.
    assert chalk.tier == V.TIER_PASS
    assert sorted([chalk, dog], key=lambda p: p.sort_key)[0] is dog


def test_band_shrinkage_is_what_actually_removes_a_chalk_edge():
    """The real mechanism, exercised end to end on equal raw disagreements.

    Both legs start from the *same* logit-scale disagreement with the market.
    After band-aware shrinkage the middle-of-the-board leg keeps enough edge to
    bet and the chalk leg does not — which is the whole design, stated as a
    test rather than as a comment.
    """
    banded = S.fit_edge_lambda_banded([])
    raw_disagreement = 0.25  # logits

    chalk_fair, mid_fair = 0.88, 0.5238
    chalk_raw = S.inv_logit(S.logit(chalk_fair) + raw_disagreement)
    mid_raw = S.inv_logit(S.logit(mid_fair) + raw_disagreement)

    chalk_p = S.shrink_toward_market(chalk_raw, chalk_fair, banded.for_band(S.BAND_CHALK))
    mid_p = S.shrink_toward_market(mid_raw, mid_fair, banded.for_band(S.BAND_MID))

    # Surviving edge, measured on the logit scale so the two are comparable.
    chalk_surviving = S.logit(chalk_p) - S.logit(chalk_fair)
    mid_surviving = S.logit(mid_p) - S.logit(mid_fair)
    assert mid_surviving > 2.0 * chalk_surviving


def test_a_fair_priced_bet_is_rejected_with_a_no_edge_reason():
    pick = V.score_leg(_leg(-110, 0.5238, 0.5238), tau=0.14, band=S.BAND_MID)
    assert pick.tier == V.TIER_PASS
    assert any("no edge" in r for r in pick.reasons)


def test_thin_market_is_priced_but_not_recommended():
    pick = V.score_leg(_leg(-110, 0.58, 0.5238, n_books=1), tau=0.14, band=S.BAND_MID)
    assert pick.tier == V.TIER_PASS
    assert any("book" in r for r in pick.reasons)


def test_a_real_spread_edge_is_recommended_with_a_stake():
    pick = V.score_leg(
        _leg(-110, 0.565, 0.5238, market=L.MARKET_SPREAD, line=-6.5, label="TEAM -6.5"),
        tau=0.14, band=S.BAND_MID,
    )
    assert pick.is_recommended
    assert pick.tier in (V.TIER_PLAYABLE, V.TIER_STRONG)
    assert 0 < pick.stake_units <= 100 * V.DEFAULT_KELLY_CAP
    text = V.explain(pick)
    assert "TEAM -6.5" in text
    assert "play " not in text.lower()
    assert " for " not in text.lower()
    assert "watch " not in text.lower()
    assert "no bet" not in text.lower()


def test_totals_are_priced_with_push_mass_on_a_whole_number():
    over = V.score_leg(
        _leg(-105, 0.55, 0.5122, market=L.MARKET_TOTAL, side="over", push=0.04,
             line=52.0, label="Over 52"),
        tau=0.14, band=S.BAND_MID,
    )
    assert over.leg.push_prob == 0.04
    # Push mass must lower the break-even bar relative to a half-point line.
    no_push = V.score_leg(
        _leg(-105, 0.55, 0.5122, market=L.MARKET_TOTAL, side="over", push=0.0,
             line=52.5, label="Over 52.5"),
        tau=0.14, band=S.BAND_MID,
    )
    assert over.breakeven_prob < no_push.breakeven_prob


def test_longshot_ceiling_rejects_lottery_prices():
    pick = V.score_leg(_leg(1400, 0.10, 0.0667), tau=0.14, band=S.BAND_DOG)
    assert pick.tier == V.TIER_PASS
    assert any("de-vig" in r for r in pick.reasons)


def test_thin_edge_is_surfaced_rather_than_hidden():
    """Between "no edge" and "recommended" there has to be a visible middle."""
    pick = V.score_leg(_leg(-110, 0.532, 0.5238), tau=0.14, band=S.BAND_MID)
    assert pick.tier == V.TIER_THIN
    assert pick.is_recommended is False
    assert pick.ev > 0


def test_absurd_model_market_gap_is_treated_as_a_fault_not_an_edge():
    """The circuit breaker. Without it this is a max-stake "strong" pick.

    Nobody beats a seven-book consensus by seven points of spread. A gap that
    size means a stale distribution, a mismatched line, or an unratable
    opponent — every one of which has happened in this codebase.
    """
    # Fair 52.4%, model 78% — about 1.2 log-odds of disagreement.
    pick = V.score_leg(
        _leg(-110, 0.78, 0.5238, market=L.MARKET_TOTAL, side="under", label="Under 44.5"),
        tau=0.14, band=S.BAND_MID,
    )
    assert pick.ev > 0.20                # nominally enormous value
    assert pick.tier == V.TIER_PASS      # and rejected anyway
    assert pick.stake_units == 0.0
    assert any("bad input" in r for r in pick.reasons)


def test_the_fault_gate_is_measured_before_shrinkage():
    """Shrinkage hides faults by construction; the gate has to run first.

    A leg whose *shrunk* probability looks merely optimistic can still have come
    from a raw disagreement that was absurd. ``model_prob`` is the pre-shrinkage
    number, and that is what the gate reads.
    """
    leg = _leg(-110, 0.78, 0.5238)
    object.__setattr__(leg, "prob", 0.56)   # what shrinkage would have produced
    pick = V.score_leg(leg, tau=0.14, band=S.BAND_MID)
    assert pick.tier == V.TIER_PASS
    assert any("bad input" in r for r in pick.reasons)


def test_a_normal_sized_edge_passes_the_fault_gate():
    """The breaker must not fire on real edges, or it is just a mute button."""
    pick = V.score_leg(
        _leg(-110, 0.575, 0.5238, market=L.MARKET_SPREAD), tau=0.14, band=S.BAND_MID,
    )
    assert pick.tier in (V.TIER_PLAYABLE, V.TIER_STRONG)
    assert not any("bad input" in r for r in pick.reasons)


def test_a_single_bad_quote_cannot_manufacture_a_max_stake_pick():
    """The price breaker. EV is computed from the best price across books, so
    one stale row is enough to invent a huge edge that every model-side check
    waves through — the model and the consensus both look completely normal.

    A +450 quote sitting in a -110/-110 market with the model at 52%:
    the model/market disagreement is 0.08 log-odds (fine), the book count is
    fine, the price is inside the longshot ceiling, and the resulting bet prices
    at nearly +180% EV with 100% stated confidence.
    """
    leg = _leg(450, 0.52, 0.50, market=L.MARKET_SPREAD)
    pick = V.score_leg(leg, tau=0.14, band=S.BAND_MID)
    assert pick.ev > 1.0                 # nominally +100%+ EV
    assert pick.tier == V.TIER_PASS
    assert pick.stake_units == 0.0
    assert any("stale or bad quote" in r for r in pick.reasons)


def test_real_line_shopping_still_passes_the_price_breaker():
    """The breaker must not fire on a genuinely soft book, or it deletes the
    product. Beating the no-vig fair number by a few points is the whole game."""
    # Fair 52.38%, offered +100 (dec 2.0) -> ~4.8% free money at the fair number.
    pick = V.score_leg(
        _leg(100, 0.565, 0.5238, market=L.MARKET_SPREAD), tau=0.14, band=S.BAND_MID,
    )
    assert pick.tier in (V.TIER_PLAYABLE, V.TIER_STRONG)
    assert not any("stale or bad quote" in r for r in pick.reasons)


def test_normal_vigged_prices_show_negative_free_money():
    """Sanity on the quantity the breaker gates: at a fairly priced book it is
    negative, because what you give up is exactly the hold."""
    leg = _leg(-110, 0.55, 0.5238)
    price_edge = leg.fair_prob * leg.decimal_odds - 1.0
    assert price_edge < 0
    assert price_edge > -0.06


def test_breakeven_is_reported_on_both_conditioning_bases():
    """`prob` and `fair_prob` are conditional on no push; the unconditional
    break-even is not comparable to them and overstates the edge if shown
    side by side."""
    pick = V.score_leg(
        _leg(-110, 0.56, 0.5238, market=L.MARKET_SPREAD, push=0.06, line=-7.0),
        tau=0.14, band=S.BAND_MID,
    )
    dec = om.american_to_decimal(-110)
    assert pick.breakeven_prob == pytest.approx((1 - 0.06) / dec, abs=1e-12)
    assert pick.breakeven_vs_ours == pytest.approx(1.0 / dec, abs=1e-9)
    assert pick.breakeven_vs_ours > pick.breakeven_prob
    # The sentence a user reads must compare like with like.
    assert f"{pick.breakeven_vs_ours * 100:.1f}%" in V.explain(pick)


def test_p_edge_real_uses_the_conditional_bar():
    """With push mass, comparing against the unconditional bar would inflate
    confidence on every whole-number line."""
    push = V.score_leg(
        _leg(-110, 0.55, 0.5238, market=L.MARKET_SPREAD, push=0.08),
        tau=0.14, band=S.BAND_MID,
    )
    no_push = V.score_leg(
        _leg(-110, 0.55, 0.5238, market=L.MARKET_SPREAD, push=0.0),
        tau=0.14, band=S.BAND_MID,
    )
    # Same conditional probability against the same conditional bar.
    assert push.p_edge_real == pytest.approx(no_push.p_edge_real, abs=1e-9)


def test_no_model_view_is_not_reported_as_no_edge():
    """The bug that produced a totally empty board and blamed the market.

    With a cold prediction store the ensemble collapses onto the de-vigged
    market number, so the edge is identically zero. Reporting that as "no edge
    at this price" asserts the market is efficient when the truth is that we had
    no opinion. Those are opposite claims.
    """
    leg = _leg(-110, 0.5238, 0.5238)          # model == market, i.e. no view
    pick = V.score_leg(leg, tau=0.14, band=S.BAND_MID, model_available=False)
    assert pick.tier == V.TIER_PASS
    assert pick.reason_code == V.R_NO_MODEL
    assert pick.model_available is False
    assert not any("no edge" in r for r in pick.reasons)
    assert any("absent opinion" in r for r in pick.reasons)
    text = V.explain(pick)
    assert "market price only" in text
    assert "nothing to compare" in text


def test_a_genuinely_fair_price_still_says_no_edge():
    """The other side of the same coin: when we DO have a view and it agrees
    with the market, "no edge" is the correct and honest label."""
    pick = V.score_leg(
        _leg(-110, 0.5238, 0.5238), tau=0.14, band=S.BAND_MID, model_available=True,
    )
    assert pick.reason_code == V.R_NO_EDGE
    assert any("no edge" in r for r in pick.reasons)


def test_model_availability_is_checked_before_the_edge_gates():
    """Ordering matters: a market-only leg must never fall through to an edge
    verdict, whatever its arithmetic happens to look like."""
    # Give it an edge on paper; it must still be reported as having no view.
    pick = V.score_leg(
        _leg(-110, 0.60, 0.5238), tau=0.14, band=S.BAND_MID, model_available=False,
    )
    assert pick.reason_code == V.R_NO_MODEL
    assert pick.is_recommended is False


def test_every_rejection_carries_a_machine_readable_code():
    """The board aggregates these into a histogram, so an empty page can say
    why in one line instead of making someone read forty rows."""
    cases = [
        (V.score_leg(_leg(-110, 0.78, 0.5238), tau=0.14, band=S.BAND_MID), V.R_MODEL_FAULT),
        (V.score_leg(_leg(450, 0.52, 0.50, market=L.MARKET_SPREAD), tau=0.14, band=S.BAND_MID), V.R_BAD_QUOTE),
        (V.score_leg(_leg(-110, 0.58, 0.5238, n_books=1), tau=0.14, band=S.BAND_MID), V.R_THIN_MARKET),
        (V.score_leg(_leg(1400, 0.10, 0.0667), tau=0.14, band=S.BAND_DOG), V.R_LONGSHOT),
        (V.score_leg(_leg(-2000, 0.962, 0.9524), tau=0.14, band=S.BAND_CHALK), V.R_PRICED_OUT),
        (V.score_leg(_leg(-110, 0.50, 0.5238), tau=0.14, band=S.BAND_MID), V.R_NO_EDGE),
        (V.score_leg(_leg(-110, 0.532, 0.5238), tau=0.14, band=S.BAND_MID), V.R_EDGE_TOO_SMALL),
    ]
    for pick, expected in cases:
        assert pick.reason_code == expected, f"{pick.leg.label} -> {pick.reason_code}"
        assert pick.reason_code in V.REASON_LABEL
        assert pick.reasons, "every verdict must carry prose too"


def test_strictness_presets_widen_the_search_without_touching_the_breakers():
    """Widening a search and disabling a safety rail must not share a control."""
    strict = V.STRICTNESS_PRESETS["strict"]
    loose = V.STRICTNESS_PRESETS["loose"]
    assert loose["min_ev"] < strict["min_ev"]
    assert loose["min_p_edge_real"] < strict["min_p_edge_real"]
    assert loose["ml_price_floor"] < strict["ml_price_floor"]
    # No preset may carry a breaker override.
    for preset in V.STRICTNESS_PRESETS.values():
        assert "max_disagreement" not in preset
        assert "max_price_edge" not in preset


def test_even_the_loosest_preset_still_excludes_heavy_chalk():
    """The whole point of the rewrite must survive the escape hatch."""
    rules = V.BoardRules(**{
        **V.STRICTNESS_PRESETS["loose"],
        "ml_price_floor": int(V.STRICTNESS_PRESETS["loose"]["ml_price_floor"]),
        "max_price": int(V.STRICTNESS_PRESETS["loose"]["max_price"]),
        "min_books": int(V.STRICTNESS_PRESETS["loose"]["min_books"]),
    })
    pick = V.score_leg(_leg(-2000, 0.962, 0.9524), tau=0.14, band=S.BAND_CHALK, rules=rules)
    assert pick.tier == V.TIER_PASS
    assert pick.reason_code == V.R_PRICED_OUT


def test_market_summary_counts_each_market_separately():
    picks = [
        V.score_leg(_leg(-110, 0.565, 0.5238, market=L.MARKET_SPREAD), tau=0.14, band=S.BAND_MID),
        V.score_leg(_leg(-105, 0.56, 0.5122, market=L.MARKET_TOTAL, side="over"), tau=0.14, band=S.BAND_MID),
        V.score_leg(_leg(-2000, 0.962, 0.9524), tau=0.14, band=S.BAND_CHALK),
    ]
    summary = V.market_summary(picks)
    assert summary[L.MARKET_SPREAD]["recommended"] == 1
    assert summary[L.MARKET_TOTAL]["recommended"] == 1
    assert summary[L.MARKET_MONEYLINE]["recommended"] == 0
    assert summary[L.MARKET_MONEYLINE]["priced"] == 1


# --------------------------------------------------------------------------- #
# Band-aware shrinkage
# --------------------------------------------------------------------------- #


def test_bands_partition_the_probability_line():
    assert S.band_for(0.10) == S.BAND_DOG
    assert S.band_for(0.34) == S.BAND_DOG
    assert S.band_for(0.35) == S.BAND_MID
    assert S.band_for(0.79) == S.BAND_MID
    assert S.band_for(0.80) == S.BAND_CHALK
    assert S.band_for(0.97) == S.BAND_CHALK


def test_unfitted_bands_fall_back_to_conservative_priors():
    banded = S.fit_edge_lambda_banded([])
    mid = banded.for_band(S.BAND_MID).lam
    chalk = banded.for_band(S.BAND_CHALK).lam
    dog = banded.for_band(S.BAND_DOG).lam
    assert chalk < dog < mid
    assert chalk == pytest.approx(mid * S.DEFAULT_BAND_PRIORS[S.BAND_CHALK], abs=1e-9)
    assert banded.for_band(S.BAND_CHALK).fitted is False


def test_chalk_shrinkage_kills_a_marginal_chalk_edge_but_spares_the_middle():
    """The band prior is the mechanism, and it has to bite asymmetrically."""
    banded = S.fit_edge_lambda_banded([])
    chalk_shrunk = S.shrink_toward_market(0.965, 0.9524, banded.for_band(S.BAND_CHALK))
    mid_shrunk = S.shrink_toward_market(0.58, 0.5238, banded.for_band(S.BAND_MID))
    assert chalk_shrunk - 0.9524 < 0.5 * (0.965 - 0.9524)   # most of it gone
    assert mid_shrunk - 0.5238 > 0.4 * (0.58 - 0.5238)      # middle mostly kept


def test_banded_fit_recovers_a_known_lambda_per_band():
    """Synthetic history where chalk edge is pure noise and mid edge is real."""
    import random

    rng = random.Random(11)
    rows = []
    for _ in range(4000):
        # Middle band: model edge is fully real (lambda -> 1).
        mkt = rng.uniform(0.40, 0.70)
        d = rng.gauss(0.0, 0.35)
        model = S.inv_logit(S.logit(mkt) + d)
        rows.append({
            "market_prob": mkt, "model_prob": model,
            "won": rng.random() < S.inv_logit(S.logit(mkt) + 1.0 * d),
        })
    for _ in range(4000):
        # Chalk band: model edge is pure noise (lambda -> 0).
        mkt = rng.uniform(0.82, 0.95)
        d = rng.gauss(0.0, 0.35)
        model = S.inv_logit(S.logit(mkt) + d)
        rows.append({
            "market_prob": mkt, "model_prob": model,
            "won": rng.random() < mkt,
        })

    banded = S.fit_edge_lambda_banded(rows)
    assert banded.for_band(S.BAND_MID).fitted
    assert banded.for_band(S.BAND_CHALK).fitted
    assert banded.for_band(S.BAND_MID).lam > 0.7
    assert banded.for_band(S.BAND_CHALK).lam < 0.3


def test_banded_payload_reports_prior_versus_fit_honestly():
    blob = S.fit_edge_lambda_banded([]).as_dict()
    assert blob["bands"][S.BAND_CHALK]["fitted"] is False
    assert blob["bands"][S.BAND_CHALK]["prior_multiplier"] == pytest.approx(
        S.DEFAULT_BAND_PRIORS[S.BAND_CHALK]
    )
    assert blob["cuts"] == list(S.BAND_CUTS)


# --------------------------------------------------------------------------- #
# Rules plumbing
# --------------------------------------------------------------------------- #


def test_rules_are_reported_verbatim_so_an_empty_board_can_explain_itself():
    r = V.BoardRules(min_ev=0.05, ml_price_floor=-180)
    blob = r.as_dict()
    assert blob["min_ev"] == 0.05
    assert blob["ml_price_floor"] == -180


def test_loosening_the_floor_lets_a_shorter_favorite_through():
    leg = _leg(-300, 0.80, 0.75)
    strict = V.score_leg(leg, tau=0.14, band=S.BAND_MID, rules=V.BoardRules())
    loose = V.score_leg(
        leg, tau=0.14, band=S.BAND_MID, rules=V.BoardRules(ml_price_floor=-400),
    )
    assert strict.tier == V.TIER_PASS
    assert loose.tier != V.TIER_PASS
