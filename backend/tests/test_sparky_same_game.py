"""Same-game pricing, alternate lines, and the unit generalisation.

Read alongside ``test_sparky_parlay_v2.py``. The invariants pinned here are the
ones that, if they break, produce a tool that recommends losing bets while
looking completely healthy:

* the unit refactor must not have moved a single existing price;
* a same-game combination's marginals must equal the value board's, exactly;
* the correlation between two legs of one game must have the right *sign*;
* the break-even price must actually break even.
"""
from __future__ import annotations

import math
import random

import pytest

from app.services import dist_model
from app.services.sparky import correlation as C
from app.services.sparky import legs as L
from app.services.sparky import parlay as P
from app.services.sparky import same_game as SG
from app.services.sparky.correlation import PricedLeg


def _leg(
    key: str, market: str, side: str, line, dec: float, prob: float, push: float,
    *, event_id: str = "G1", loadings=(0.55, 0.2, 0.0), tau: float = 0.15,
) -> L.LegCandidate:
    return L.LegCandidate(
        key=key, event_id=event_id, market=market, side=side, label=key,
        team_id=None, opponent_id=None, line=line, price_american=-110,
        decimal_odds=dec, book="bk", fair_prob=prob, model_prob=prob, prob=prob,
        push_prob=push, edge=0.0, expected_value=0.0, tau=tau, loadings=loadings,
        is_favorite=True,
    )


def _dist() -> dist_model.GameDistribution:
    # Home favoured by 7 in a 54-point game; rho > 0 because blowouts run high.
    return dist_model.GameDistribution(7.0, 54.0, 15.5, 11.0, 0.28)


# --------------------------------------------------------------------------- #
# The unit generalisation must be a no-op for existing tickets
# --------------------------------------------------------------------------- #


def test_to_unit_reproduces_the_leg_algebra_exactly():
    """A single leg lifted to branches must give back `_leg_factors` term for term.

    This is the seam the whole refactor rests on. If it drifts, every cross-game
    ticket silently reprices while every test that only checks "is it roughly
    right" keeps passing.
    """
    leg = PricedLeg(key="k", event_id="G", prob=0.48, push_prob=0.06,
                    decimal_odds=1.91, tau=0.2, loadings=(0.5, 0.2, 0.0))
    unit = C.to_unit(leg)
    fvec = (0.7,)
    q = C._conditional_prob_multi(C._no_push_prob(leg), leg.tau, (0.5,), fvec)
    want = C._leg_factors(leg, q)
    got = C._unit_factors(unit, (0.5,), fvec)
    for a, b in zip(want, got):
        assert a == pytest.approx(b, abs=1e-15)


def test_unit_pricing_is_bit_identical_to_leg_pricing():
    rng = random.Random(7)
    for _ in range(60):
        legs = []
        for i in range(rng.choice([2, 3, 4])):
            push = rng.choice([0.0, 0.0, 0.021, 0.061])
            legs.append(PricedLeg(
                key=f"g{i}:m:s", event_id=f"g{i}",
                prob=rng.uniform(0.25, 0.85) * (1 - push), push_prob=push,
                decimal_odds=rng.uniform(1.4, 4.0), tau=rng.uniform(0.0, 0.35),
                loadings=(rng.uniform(-.6, .6), rng.uniform(-.3, .3), rng.uniform(-.3, .3)),
            ))
        for exact in (False, True):
            via_units = C.price_units([C.to_unit(x) for x in legs], exact=exact)
            via_legs = C.price_parlay(legs, exact=exact)
            assert via_units.expected_return == pytest.approx(
                via_legs.expected_return, abs=1e-15,
            )
            assert via_units.survive_prob == pytest.approx(via_legs.survive_prob, abs=1e-15)
            assert via_units.return_sd == pytest.approx(via_legs.return_sd, abs=1e-15)


def test_two_units_on_one_game_are_refused():
    """Bundling is mandatory, not optional.

    Passing a game's legs as two separate units would price the strongest
    dependence in the ticket with the model built for the weakest.
    """
    u = C.to_unit(PricedLeg(key="a", event_id="G", prob=0.5, push_prob=0.0, decimal_odds=2.0))
    v = C.to_unit(PricedLeg(key="b", event_id="G", prob=0.5, push_prob=0.0, decimal_odds=2.0))
    with pytest.raises(ValueError, match="two separate units"):
        C.price_units([u, v])


# --------------------------------------------------------------------------- #
# The joint lattice
# --------------------------------------------------------------------------- #


def test_lattice_keeps_tie_mass_and_sums_to_one():
    lat = SG.build_lattice(_dist())
    assert 0 in lat.margins, "NFL regular-season games can tie; margin 0 must be kept"
    assert lat.mass() == pytest.approx(1.0, abs=1e-12)


def test_lattice_marginals_match_the_closed_form():
    """Both marginals must survive the construction, tightly.

    The lattice is built as margin x (total | margin), so the key-number excess
    on the margin drags the conditional total with it through the correlation —
    about half a point of over probability before raking. Half a point is larger
    than most edges this app is trying to detect, and it would mean the totals
    leg carried one number inside a same-game ticket and another on the value
    board. Key numbers belong in the push mass, not in the total's mean.
    """
    d = _dist()
    lat = SG.build_lattice(d)
    over = sum(
        lat.weight[i][j]
        for i, _ in enumerate(lat.margins)
        for j, t in enumerate(lat.totals)
        if t > 52.5
    )
    assert over == pytest.approx(d.over_prob(52.5), abs=5e-4)

    cover = sum(
        lat.weight[i][j]
        for i, m in enumerate(lat.margins)
        for j, _ in enumerate(lat.totals)
        if m - 7.5 > 0
    )
    # The margin keeps its key-number layer, so it is compared with the discrete
    # PMF rather than the smooth CDF. NFL keeps ties, so this is the raw cover.
    want, _, _ = d.cover_prob_discrete(-7.5)
    assert cover == pytest.approx(want, abs=5e-4)


def test_key_number_push_mass_survives_raking():
    """Raking must fix the marginals without flattening what the discrete layer
    exists for: real push mass on 3 and 7."""
    d = _dist()
    lat = SG.build_lattice(d)
    leg = _leg("s", "spread", "home", -7.0, 1.91, 0.5, 0.0)
    push = sum(
        lat.weight[i][j]
        for i, m in enumerate(lat.margins)
        for j, t in enumerate(lat.totals)
        if SG.outcome_for(leg, m, t) == SG.PUSH
    )
    _, want, _ = d.cover_prob_discrete(-7.0)
    assert push == pytest.approx(want, abs=5e-4)
    assert push > 0.03, "a 7-point spread must carry real push mass in the NFL"


def test_the_lattice_keeps_nfl_tie_mass():
    """NFL regular-season games can finish 0-0 in overtime. The lattice must
    carry that mass so a moneyline can push and a pick'em is not mispriced.

    ``dist_model.margin_pmf`` integrates a continuous Normal over the
    ``[-0.5, 0.5]`` bin, so it assigns a few percent to a final margin of
    exactly zero. That is a real NFL outcome; deleting it (the CFB choice)
    would show up as a phantom moneyline win and a mispriced pick'em.
    """
    d = _dist()
    tie = d.margin_pmf().get(0, 0.0)
    assert 0.01 < tie < 0.04, "if this moved, the sigma model changed"

    lat = SG.build_lattice(d)
    assert 0 in lat.margins
    lattice_tie = sum(
        lat.weight[i][j]
        for i, m in enumerate(lat.margins)
        for j, _ in enumerate(lat.totals)
        if m == 0
    )
    assert lattice_tie == pytest.approx(tie, abs=5e-4)


def test_favourite_cover_and_over_are_positively_correlated():
    """The whole reason this module exists.

    Points that build a cover are in the total, so on a favourite these two legs
    rise together. A tool that multiplies them underprices the pair — which is
    exactly what a book is charging you for when it reprices an SGP.
    """
    d = _dist()
    lat = SG.build_lattice(d)

    def p(pred):
        return sum(
            lat.weight[i][j]
            for i, m in enumerate(lat.margins)
            for j, t in enumerate(lat.totals)
            if pred(m, t)
        )

    cover = p(lambda m, t: m - 7.5 > 0)
    over = p(lambda m, t: t > 52.5)
    joint = p(lambda m, t: (m - 7.5 > 0) and (t > 52.5))
    assert joint > cover * over
    # And the mirror: favourite cover with the UNDER must go the other way.
    under = p(lambda m, t: t < 52.5)
    joint_u = p(lambda m, t: (m - 7.5 > 0) and (t < 52.5))
    assert joint_u < cover * under


# --------------------------------------------------------------------------- #
# IPF: the SGP and the value board must agree on every leg
# --------------------------------------------------------------------------- #


def test_ipf_matches_the_single_leg_probabilities_exactly():
    """If this drifts, a leg is worth one number on the board and another inside
    a parlay — and the difference is untrusted edge, hidden where nobody looks."""
    d = _dist()
    legs = [
        _leg("G1:spread:home:-7", "spread", "home", -7.0, 1.909, 0.512, 0.072),
        _leg("G1:total:over:54", "total", "over", 54.0, 1.870, 0.508, 0.036,
             loadings=(0.55, 0.0, 0.30)),
    ]
    quote = SG.price_same_game(d, legs)
    assert quote.ipf is not None and quote.ipf.converged
    for leg, row in zip(legs, quote.ipf.adjusted):
        assert row["fitted_win"] == pytest.approx(leg.unconditional_win_prob, abs=1e-6)
        assert row["fitted_push"] == pytest.approx(leg.push_prob, abs=1e-6)


def test_unconditional_win_prob_is_not_the_conditional_one():
    """`prob` is conditional on no push. Confusing the two inflates every
    whole-number leg by its push mass — a silent few points per ticket."""
    leg = _leg("k", "total", "over", 54.0, 1.87, 0.52, 0.04)
    assert leg.unconditional_win_prob == pytest.approx(0.52 * 0.96)


def test_branches_partition_the_outcome_space():
    d = _dist()
    legs = [
        _leg("G1:spread:home:-7", "spread", "home", -7.0, 1.909, 0.512, 0.072),
        _leg("G1:total:over:54", "total", "over", 54.0, 1.870, 0.508, 0.036,
             loadings=(0.55, 0.0, 0.30)),
    ]
    q = SG.price_same_game(d, legs)
    assert sum(b.mass for b in q.unit.branches) == pytest.approx(1.0, abs=1e-9)
    # Two whole-number legs => four push patterns, all with real mass.
    assert len(q.unit.branches) == 4
    assert q.push_any_prob > 0.0
    assert q.survive_prob >= q.all_win_prob


# --------------------------------------------------------------------------- #
# The headline number
# --------------------------------------------------------------------------- #


def test_fair_decimal_actually_breaks_even():
    """`fair_decimal` is what the user is told to compare against their book.
    It has to be the price at which E[return] is exactly 1, not an approximation
    of it."""
    d = _dist()
    legs = [
        _leg("G1:spread:home:-7.5", "spread", "home", -7.5, 1.909, 0.508, 0.0),
        _leg("G1:total:over:53", "total", "over", 53.0, 1.870, 0.531, 0.021,
             loadings=(0.55, 0.0, 0.30)),
    ]
    q = SG.price_same_game(d, legs)
    k = (q.fair_decimal - 1.0) / (q.multiplied_decimal - 1.0)
    er = sum(
        b.mass * b.win_prob * (1.0 + (b.multiplier - 1.0) * k if b.multiplier > 1 else b.multiplier)
        for b in q.unit.branches
    )
    assert er == pytest.approx(1.0, abs=1e-6)
    assert 0.0 < q.fair_decimal < q.multiplied_decimal
    assert q.shade_room == pytest.approx(1.0 - q.fair_decimal / q.multiplied_decimal)


def test_correlated_legs_beat_naive_multiplication():
    d = _dist()
    legs = [
        _leg("G1:spread:home:-7.5", "spread", "home", -7.5, 1.909, 0.508, 0.0),
        _leg("G1:total:over:53", "total", "over", 53.0, 1.870, 0.531, 0.021,
             loadings=(0.55, 0.0, 0.30)),
    ]
    q = SG.price_same_game(d, legs)
    assert q.correlation_effect > 0.05, (
        "a favourite cover stacked with the over is materially worth more than "
        "the product of its legs; if this collapses the joint is not being used"
    )
    assert q.expected_return_at_multiplied > q.independent_expected_return


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def test_opposite_sides_of_one_market_are_refused():
    d = _dist()
    with pytest.raises(SG.SameGameError, match="opposite sides"):
        SG.price_same_game(d, [
            _leg("a", "total", "over", 53.0, 1.9, 0.53, 0.02),
            _leg("b", "total", "under", 53.0, 1.9, 0.45, 0.02),
        ])


def test_the_same_bet_twice_is_refused():
    d = _dist()
    with pytest.raises(SG.SameGameError, match="[Dd]uplicate"):
        SG.price_same_game(d, [
            _leg("a", "spread", "home", -7.5, 1.9, 0.51, 0.0),
            _leg("a", "spread", "home", -7.5, 1.9, 0.51, 0.0),
        ])


def test_legs_from_two_games_are_refused_here():
    d = _dist()
    with pytest.raises(SG.SameGameError, match="2 games"):
        SG.price_same_game(d, [
            _leg("a", "spread", "home", -7.5, 1.9, 0.51, 0.0),
            _leg("b", "spread", "home", -3.5, 1.9, 0.51, 0.0, event_id="G2"),
        ])


def test_leg_count_is_capped():
    d = _dist()
    legs = [
        _leg("a", "spread", "home", -7.5, 1.9, 0.51, 0.0),
        _leg("b", "total", "over", 53.0, 1.9, 0.53, 0.0),
        _leg("c", "moneyline", "home", None, 1.5, 0.68, 0.0),
    ]
    with pytest.raises(SG.SameGameError, match="capped"):
        SG.price_same_game(d, legs, max_legs=2)


def test_outcome_settlement_matches_the_grader():
    """The lattice and settlement must agree on what a leg means, or a ticket is
    priced on one rule and paid on another."""
    from app.services.sparky_parlay_service import grade_leg

    cases = [
        (_leg("s", "spread", "home", -7.5, 1.9, 0.5, 0.0), 10, 0),
        (_leg("s", "spread", "home", -7.0, 1.9, 0.5, 0.07), 7, 0),
        (_leg("s", "spread", "away", 3.5, 1.9, 0.5, 0.0), 0, 3),
        (_leg("t", "total", "over", 54.0, 1.9, 0.5, 0.03), 30, 24),
        (_leg("t", "total", "under", 48.0, 1.9, 0.5, 0.03), 20, 21),
        (_leg("m", "moneyline", "away", None, 1.9, 0.5, 0.0), 17, 24),
    ]
    name = {SG.WIN: "win", SG.PUSH: "push", SG.LOSS: "loss"}
    for leg, hs, as_ in cases:
        lattice_view = name[SG.outcome_for(leg, hs - as_, hs + as_)]
        assert lattice_view == grade_leg(leg.as_dict(), hs, as_), leg.market


# --------------------------------------------------------------------------- #
# Alternate lines
# --------------------------------------------------------------------------- #


def _game_with_alt_lines() -> L.GameLegInputs:
    return L.GameLegInputs(
        event_id="E1", home_id="KC", away_id="BUF", label="BUF @ KC",
        ml_prices=L.SidePrices(consensus_a=-260, consensus_b=210, best_a=-250,
                               best_b=215, n_books=5),
        model_home_win=0.74,
        spread_markets=(
            (L.SidePrices(consensus_a=-110, consensus_b=-110, best_a=-105, best_b=-110,
                          line=-7.5, n_books=5), L.ModelTriple(0.52, 0.0, 0.48)),
            (L.SidePrices(fair_a=0.545, fair_b=0.455, best_a=-120, best_b=None,
                          line=-6.5, n_books=1, is_alt=True, fair_source="derived"),
             L.ModelTriple(0.565, 0.0, 0.435)),
        ),
        total_markets=(
            (L.SidePrices(consensus_a=-110, consensus_b=-110, best_a=-108, best_b=-110,
                          line=53.0, n_books=5), L.ModelTriple(0.53, 0.021, 0.449)),
        ),
    )


def test_alternate_lines_produce_distinct_legs():
    """Two numbers are two bets. Sharing a key would collapse them in every dict
    keyed by it — the reuse cap, the hand-built lookup, settlement."""
    pool = L.build_leg_pool([_game_with_alt_lines()], min_books=0, min_edge=-1)
    keys = [x.key for x in pool]
    assert len(keys) == len(set(keys))
    assert "E1:spread:home:-7.5" in keys
    assert "E1:spread:home:-6.5" in keys
    assert "E1:moneyline:home" in keys, "a moneyline has no line and must not gain one"


def test_derived_alt_lines_are_labelled():
    pool = L.build_leg_pool([_game_with_alt_lines()], min_books=0, min_edge=-1)
    alt = next(x for x in pool if x.key == "E1:spread:home:-6.5")
    assert alt.is_alt and alt.fair_source == "derived"
    assert any("alternate line" in n for n in alt.notes)
    assert any("derived" in n for n in alt.notes)
    main = next(x for x in pool if x.key == "E1:spread:home:-7.5")
    assert not main.is_alt and main.fair_source == "quoted" and main.notes == []


def test_derived_fair_is_used_instead_of_a_one_sided_devig():
    """The supplied fair pair must survive normalisation, not be re-derived from
    a price that only one side of the market is quoted at."""
    pool = L.build_leg_pool([_game_with_alt_lines()], min_books=0, min_edge=-1)
    alt = next(x for x in pool if x.key == "E1:spread:home:-6.5")
    assert alt.fair_prob == pytest.approx(0.545, abs=1e-6)


def test_a_mixed_market_ticket_prices_across_games():
    """The request this feature exists for: a spread here, a moneyline there, a
    total somewhere else."""
    a = _leg("A:spread:away:7.5", "spread", "away", 7.5, 1.91, 0.53, 0.0, event_id="A")
    b = _leg("B:moneyline:home", "moneyline", "home", None, 3.00, 0.36, 0.0,
             event_id="B", loadings=(0.55, -0.2, 0.0))
    c = _leg("C:total:over:56.5", "total", "over", 56.5, 1.87, 0.55, 0.0,
             event_id="C", loadings=(0.55, 0.0, 0.30))
    pricing = C.price_units([C.to_unit(P.to_priced(x)) for x in (a, b, c)], exact=True)
    assert pricing.n_legs == 3
    assert pricing.decimal_odds == pytest.approx(1.91 * 3.00 * 1.87)
    assert 0.0 < pricing.survive_prob < 1.0


def test_a_same_game_bundle_composes_with_cross_game_legs():
    d = _dist()
    bundle = SG.price_same_game(d, [
        _leg("G1:spread:home:-7.5", "spread", "home", -7.5, 1.909, 0.508, 0.0),
        _leg("G1:total:over:53", "total", "over", 53.0, 1.870, 0.531, 0.021,
             loadings=(0.55, 0.0, 0.30)),
    ])
    other = C.to_unit(P.to_priced(
        _leg("G2:moneyline:home", "moneyline", "home", None, 1.71, 0.62, 0.0, event_id="G2")
    ))
    pricing = C.price_units([bundle.unit, other], exact=True)
    assert pricing.n_legs == 3, "the bundle must report its own leg count"
    assert pricing.decimal_odds == pytest.approx(bundle.multiplied_decimal * 1.71)
    assert math.isfinite(pricing.expected_value)


# --------------------------------------------------------------------------- #
# The market-implied fair curve, which every alternate line is priced against
# --------------------------------------------------------------------------- #


def test_market_implied_curve_passes_through_the_quoted_number():
    """The inversion must be done in the measure it is read back in.

    An earlier version solved for the centre with the continuous Normal and then
    evaluated alternates with the discrete key-number PMF. At a 7.5 line, mass
    piled on 7 makes the discrete cover probability at ``mu = 7.5`` about 0.456,
    not 0.5 — so the fair curve did not pass through the market's own quoted
    number and every alternate line inherited a four-point error. Four points is
    larger than the half-point edge the alternate exists to capture, and it
    pointed the wrong way, so it would have manufactured value on the wrong side.
    """
    from app.services import sparky_parlay_service as S

    gd = dist_model.GameDistribution(8.5, 55.0, 15.5, 11.0, 0.28)
    mkt = S.market_implied_dist(
        gd, spread_line=-7.5, spread_fair_home=0.5,
        total_line=53.0, total_fair_over=0.5,
    )

    win, _, lose = mkt.cover_prob_discrete(-7.5)
    assert win / (win + lose) == pytest.approx(0.5, abs=1e-4)

    over, _, under = S._over_under_discrete(mkt, 53.0)
    assert over / (over + under) == pytest.approx(0.5, abs=1e-4)


def test_the_fair_curve_moves_the_right_way_and_prices_the_key_number():
    """Fewer points must mean a higher cover probability, and crossing 7 must be
    worth materially more than crossing a non-key number."""
    from app.services import sparky_parlay_service as S

    gd = dist_model.GameDistribution(8.5, 55.0, 15.5, 11.0, 0.28)
    mkt = S.market_implied_dist(
        gd, spread_line=-7.5, spread_fair_home=0.5, total_line=None,
        total_fair_over=None,
    )

    def fair(line: float) -> float:
        w, _, l = mkt.cover_prob_discrete(line)
        return w / (w + l)

    curve = [fair(x) for x in (-9.5, -8.5, -7.5, -6.5, -5.5)]
    assert curve == sorted(curve), "giving fewer points cannot lower the cover prob"

    across_seven = fair(-6.5) - fair(-7.5)     # buys the 7
    across_nine = fair(-8.5) - fair(-9.5)      # buys a non-key number
    assert across_seven > 0.03
    assert across_seven > across_nine * 1.3, (
        "the key number has to be worth more than an ordinary point, or the "
        "whole reason to enumerate alternate lines is gone"
    )


def test_an_unreachable_market_target_leaves_the_centre_alone():
    """A market our dispersion cannot express is a data problem to surface, not
    a centre to invent."""
    from app.services import sparky_parlay_service as S

    gd = dist_model.GameDistribution(8.5, 55.0, 15.5, 11.0, 0.28)
    mkt = S.market_implied_dist(
        gd, spread_line=-7.5, spread_fair_home=0.99999,
        total_line=None, total_fair_over=None,
    )
    assert mkt.mu_m == pytest.approx(gd.mu_m)


def test_the_same_bet_at_two_numbers_is_refused():
    """Nested legs: the harder one winning implies the easier one does, so the
    pair is one opinion sold twice at a worse combined price."""
    d = _dist()
    with pytest.raises(SG.SameGameError, match="two different numbers"):
        SG.price_same_game(d, [
            _leg("a", "spread", "home", -7.5, 1.91, 0.51, 0.0),
            _leg("b", "spread", "home", -6.5, 1.80, 0.54, 0.0),
        ])
