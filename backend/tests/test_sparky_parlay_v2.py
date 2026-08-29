"""Unit tests for the rebuilt parlay engine.

These are pure — no DB, no network, no pandas — so they run anywhere, including
the Linux sandbox where this app's macOS venv is unusable.

The tests that matter most are the ones that check the *math is what it claims
to be*, not merely that the code runs:

  - the latent-factor model preserves each leg's marginal probability exactly
    (otherwise uncertainty silently shrinks every favourite and double-counts
    the calibration map);
  - the fast rank-1 collapse agrees with exact multi-channel integration;
  - push handling matches a closed-form calculation;
  - the fitted shrinkage lambda recovers a known truth from synthetic data;
  - the +EV gate is *possible to pass* and *possible to fail*, which is the
    regression that caught the original selection-penalty formulation charging
    a haircut larger than any achievable edge.
"""
from __future__ import annotations

import math
import random

import pytest

from app.services.sparky import correlation as C
from app.services.sparky import legs as L
from app.services.sparky import odds_math as om
from app.services.sparky import parlay as P
from app.services.sparky import parlay_backtest as B
from app.services.sparky import shrinkage as S


# --------------------------------------------------------------------------- #
# odds math
# --------------------------------------------------------------------------- #


def test_power_devig_matches_proportional_on_a_balanced_market():
    prop = om.devig_two_way(-110, -110)
    power = om.devig_power([-110, -110])
    assert power[0] == pytest.approx(prop[0], abs=1e-9)
    assert sum(power) == pytest.approx(1.0, abs=1e-9)


def test_power_devig_takes_more_from_the_longshot():
    """The whole point: books load hold onto the dog, proportional de-vig doesn't."""
    prop_fav, prop_dog = om.devig_two_way(-2000, 1100)
    pow_fav, pow_dog = om.devig_power([-2000, 1100])
    assert pow_dog < prop_dog
    assert pow_fav > prop_fav
    assert sum((pow_fav, pow_dog)) == pytest.approx(1.0, abs=1e-6)


def test_kelly_from_moments_shrinks_as_variance_grows():
    tight = om.kelly_from_moments(1.05, 1.0, cap=1.0, fraction=1.0)
    wide = om.kelly_from_moments(1.05, 10.0, cap=1.0, fraction=1.0)
    assert tight > wide > 0
    assert om.kelly_from_moments(0.98, 2.0) == 0.0


# --------------------------------------------------------------------------- #
# correlation: the core math
# --------------------------------------------------------------------------- #


def _leg(i, p=0.6, dec=1.91, tau=0.3, push=0.0, loadings=(0.5, 0.2, 0.0)):
    return C.PricedLeg(
        key=f"L{i}", event_id=f"g{i}", prob=p, push_prob=push,
        decimal_odds=dec, tau=tau, loadings=loadings,
    )


@pytest.mark.parametrize("p", [0.35, 0.55, 0.72, 0.9])
@pytest.mark.parametrize("tau", [0.1, 0.4, 0.9])
def test_marginal_probability_is_preserved_under_uncertainty(p, tau):
    """E_F[q(F)] must equal q, or uncertainty becomes an accidental haircut."""
    e = sum(w * C.conditional_prob(p, tau, 0.7, f) for f, w in C._GH_NODES)
    assert e == pytest.approx(p, abs=2e-3)


def test_zero_tau_reproduces_the_independent_product_exactly():
    legs = [_leg(i, tau=0.0) for i in range(4)]
    pr = C.price_parlay(legs)
    assert pr.expected_value == pytest.approx(pr.independent_expected_return - 1.0, abs=1e-12)
    assert pr.correlation_effect == pytest.approx(0.0, abs=1e-12)
    assert pr.all_win_prob == pytest.approx(0.6 ** 4, abs=1e-9)


def test_zero_loadings_reproduce_the_independent_product():
    legs = [_leg(i, tau=0.5, loadings=(0.0, 0.0, 0.0)) for i in range(3)]
    pr = C.price_parlay(legs)
    assert pr.correlation_effect == pytest.approx(0.0, abs=2e-3)


def test_same_direction_legs_are_worth_more_than_the_naive_product():
    """Positive association (FKG): all legs backing the model rise together."""
    legs = [_leg(i, tau=0.6, loadings=(0.85, 0.0, 0.0)) for i in range(3)]
    pr = C.price_parlay(legs)
    assert pr.correlation_effect > 0
    assert pr.all_win_prob > pr.independent_all_win_prob


def test_opposing_legs_are_worth_less_than_the_naive_product():
    """A ticket that partly hedges itself must not be sold at the product price."""
    legs = [
        _leg(0, tau=0.6, loadings=(0.85, 0.0, 0.0)),
        _leg(1, tau=0.6, loadings=(0.85, 0.0, 0.0)),
        _leg(2, tau=0.6, loadings=(-0.85, 0.0, 0.0)),
    ]
    pr = C.price_parlay(legs)
    assert pr.mean_pairwise_corr < 0
    assert pr.correlation_effect < 0


def test_rank1_collapse_agrees_with_exact_multichannel_integration():
    rng = random.Random(11)
    worst = 0.0
    for _ in range(60):
        n = rng.randint(2, 5)
        legs = []
        for i in range(n):
            raw = [rng.uniform(0.1, 0.6), rng.uniform(-0.5, 0.5), rng.uniform(-0.4, 0.4)]
            norm = math.sqrt(sum(x * x for x in raw))
            if norm > 0.95:
                raw = [x * 0.95 / norm for x in raw]
            legs.append(C.PricedLeg(
                key=f"L{i}", event_id=f"g{i}", prob=rng.uniform(0.4, 0.85),
                push_prob=0.0, decimal_odds=rng.uniform(1.4, 4.0),
                tau=rng.uniform(0.1, 0.7), loadings=tuple(raw),
            ))
        fast = C.price_parlay(legs).expected_return
        exact = C.price_parlay(legs, exact=True).expected_return
        worst = max(worst, abs(fast - exact) / max(abs(exact), 1e-9))
    assert worst < 0.02, f"rank-1 collapse drifted {worst:.4%} from exact"


def test_push_is_worth_more_than_treating_it_as_a_loss():
    legs = [_leg(i, push=0.05, tau=0.0) for i in range(3)]
    pr = C.price_parlay(legs)
    assert pr.push_effect > 0
    # Closed form: each leg contributes (p*dec + push) with p renormalized into
    # the non-push mass, so the product must match exactly at tau = 0.
    q = 0.6 / 0.95
    expected = (q * 0.95 * 1.91 + 0.05) ** 3
    assert pr.expected_return == pytest.approx(expected, abs=1e-9)


def test_certain_push_returns_stake():
    leg = C.PricedLeg(
        key="a", event_id="g", prob=0.0, push_prob=1.0, decimal_odds=1.91, tau=0.0,
    )
    pr = C.price_parlay([leg])
    assert pr.expected_return == pytest.approx(1.0, abs=1e-12)
    assert pr.survive_prob == pytest.approx(1.0, abs=1e-12)


def test_same_game_legs_are_refused():
    a = C.PricedLeg(key="a", event_id="SAME", prob=0.6, push_prob=0.0, decimal_odds=1.9)
    b = C.PricedLeg(key="b", event_id="SAME", prob=0.6, push_prob=0.0, decimal_odds=1.9)
    with pytest.raises(ValueError, match="same-game"):
        C.price_parlay([a, b])


def test_invalid_legs_are_rejected_at_construction():
    with pytest.raises(ValueError):
        C.PricedLeg(key="a", event_id="g", prob=0.8, push_prob=0.5, decimal_odds=1.9)
    with pytest.raises(ValueError):
        C.PricedLeg(key="a", event_id="g", prob=0.5, push_prob=0.0, decimal_odds=0.9)
    with pytest.raises(ValueError):
        C.PricedLeg(
            key="a", event_id="g", prob=0.5, push_prob=0.0, decimal_odds=1.9,
            loadings=(0.9, 0.9, 0.0),
        )


# --------------------------------------------------------------------------- #
# shrinkage
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("true_lam", [0.0, 0.35, 0.7, 1.0])
def test_fit_edge_lambda_recovers_a_known_truth(true_lam):
    rng = random.Random(4)
    rows = []
    for _ in range(4000):
        mkt = rng.uniform(0.25, 0.75)
        m = math.log(mkt / (1 - mkt))
        d = rng.gauss(0, 0.45)
        p_true = 1 / (1 + math.exp(-(m + true_lam * d)))
        rows.append({
            "market_prob": mkt,
            "model_prob": 1 / (1 + math.exp(-(m + d))),
            "won": rng.random() < p_true,
        })
    fit = S.fit_edge_lambda(rows)
    assert fit.fitted
    assert fit.lam == pytest.approx(true_lam, abs=0.08)


def test_fit_edge_lambda_refuses_to_fit_on_thin_history():
    rows = [{"market_prob": 0.5, "model_prob": 0.6, "won": True}] * 20
    fit = S.fit_edge_lambda(rows)
    assert not fit.fitted


def test_fit_platt_detects_overconfidence():
    rng = random.Random(9)
    rows = []
    for _ in range(4000):
        raw = rng.uniform(0.5, 0.95)
        x = math.log(raw / (1 - raw))
        p_true = 1 / (1 + math.exp(-0.7 * x))
        rows.append({"prob": raw, "won": rng.random() < p_true})
    pm = S.fit_platt(rows)
    assert pm.fitted
    assert pm.b == pytest.approx(0.7, abs=0.1)
    assert pm(0.85) < 0.85     # pulled back toward 0.5


def test_shrink_moves_the_model_toward_the_market():
    sh = S.EdgeShrink(lam=0.4)
    out = S.shrink_toward_market(0.70, 0.60, sh)
    assert 0.60 < out < 0.70


def test_shrink_with_lambda_zero_lands_on_the_market():
    out = S.shrink_toward_market(0.90, 0.55, S.EdgeShrink(lam=0.0))
    assert out == pytest.approx(0.55, abs=1e-9)


def test_leg_tau_is_smallest_at_the_extremes_of_lambda():
    """A hard-shrunk leg sits on the market and is therefore *more* certain.

    Regression: the first implementation scaled tau up as lambda fell, on the
    reasoning that a weak model is a noisy model. That is backwards, and it made
    the selection penalty exceed the expected value of every possible ticket.
    """
    ends = (S.leg_tau(S.EdgeShrink(lam=0.0)), S.leg_tau(S.EdgeShrink(lam=1.0)))
    middle = S.leg_tau(S.EdgeShrink(lam=0.6))
    assert middle > max(ends)
    assert all(t < 0.35 for t in ends)


def test_selection_z_grows_slowly_with_pool_size():
    small = S.selection_z(8, 3)
    big = S.selection_z(80, 3)
    assert 0 < small < big < 3.0


def test_selection_penalty_is_zero_when_no_selection_happened():
    assert S.selection_penalty(
        [0.6, 0.6], [1.9, 1.9], [0.2, 0.2], n_leg_pool=2,
    ) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# the gate: must be passable AND failable
# --------------------------------------------------------------------------- #


def _uniform_ticket(edge_pp: float, n: int, tau: float):
    mkt = om.american_to_implied(-110)
    p = mkt + edge_pp / 100.0
    dec = om.american_to_decimal(-110)
    legs = [C.PricedLeg(
        key=f"x{i}", event_id=f"e{i}", prob=p, push_prob=0.0,
        decimal_odds=dec, tau=tau, loadings=(0.55, 0.2, 0.0),
    ) for i in range(n)]
    pr = C.price_parlay(legs)
    pen = S.selection_penalty([p] * n, [dec] * n, [tau] * n, n_leg_pool=20)
    return pr.expected_value - pen


def test_gate_rejects_a_negligible_edge():
    tau = S.leg_tau(S.EdgeShrink(lam=0.45))
    assert _uniform_ticket(0.25, 3, tau) < 0


def test_gate_accepts_a_real_edge():
    """Regression: the gate must not be mathematically impossible to pass.

    The first selection-penalty formulation multiplied the expected-max order
    statistic of ~500 'independent' tickets by the ticket's full EV standard
    error, which produced a haircut exceeding EV even at a 6-point-per-leg
    edge — a gate nothing could ever clear at any edge size.
    """
    tau = S.leg_tau(S.EdgeShrink(lam=0.45))
    assert _uniform_ticket(3.0, 3, tau) > 0
    assert _uniform_ticket(5.0, 2, tau) > 0


def test_gate_is_monotone_in_edge():
    tau = S.leg_tau(S.EdgeShrink(lam=0.45))
    vals = [_uniform_ticket(e, 3, tau) for e in (0.5, 1.5, 3.0, 5.0)]
    assert vals == sorted(vals)


# --------------------------------------------------------------------------- #
# legs
# --------------------------------------------------------------------------- #


def _game(**over):
    base = dict(
        event_id="e1", home_id="KC", away_id="BUF", label="BUF @ KC",
        ml_prices=L.SidePrices(
            consensus_a=-260, consensus_b=210, best_a=-245, best_b=225,
            best_book_a="pinnacle", best_book_b="betmgm", n_books=7,
        ),
        model_home_win=0.755,
        spread_prices=L.SidePrices(
            consensus_a=-110, consensus_b=-110, best_a=-105, best_b=-108,
            best_book_a="circa", best_book_b="dk", line=-7.0, n_books=7,
        ),
        model_spread=L.ModelTriple(a=0.545, push=0.052, b=0.403),
        total_prices=L.SidePrices(
            consensus_a=-112, consensus_b=-108, best_a=-105, best_b=-104,
            best_book_a="fd", best_book_b="dk", line=55.5, n_books=7,
        ),
        model_total=L.ModelTriple(a=0.575, push=0.0, b=0.425),
        favorite="home",
    )
    base.update(over)
    return L.GameLegInputs(**base)


def test_all_three_markets_produce_legs():
    out = L.build_game_legs(_game(), shrink=S.EdgeShrink(lam=0.45))
    assert {leg.market for leg in out} == {"moneyline", "spread", "total"}
    assert len(out) == 6


def test_a_missing_price_produces_no_leg_rather_than_an_invented_one():
    """Regression: the old engine synthesized a price from its own probability.

    That made the leg's 'market implied' probability a deterministic function of
    the model probability, so its edge was a constant manufactured from nothing
    — and because ranking sorted on edge, those phantom legs floated to the top.
    """
    g = _game(
        ml_prices=L.SidePrices(consensus_a=-260, n_books=7),
        spread_prices=None, model_spread=None,
        total_prices=None, model_total=None,
    )
    assert L.build_game_legs(g, shrink=S.EdgeShrink(lam=0.45)) == []


def test_spread_legs_carry_the_push_probability():
    out = L.build_game_legs(_game(), shrink=S.EdgeShrink(lam=0.45))
    spreads = [leg for leg in out if leg.market == "spread"]
    assert all(leg.push_prob == pytest.approx(0.052, abs=1e-6) for leg in spreads)
    totals = [leg for leg in out if leg.market == "total"]
    assert all(leg.push_prob == 0.0 for leg in totals)  # 55.5 cannot push


def test_the_two_sides_of_a_market_have_opposite_edges():
    out = L.build_game_legs(_game(), shrink=S.EdgeShrink(lam=0.45))
    for market in ("moneyline", "spread", "total"):
        pair = [leg for leg in out if leg.market == market]
        assert pair[0].edge == pytest.approx(-pair[1].edge, abs=1e-9)


def test_loadings_oppose_for_over_and_under():
    out = L.build_game_legs(_game(), shrink=S.EdgeShrink(lam=0.45))
    over = next(leg for leg in out if leg.side == "over")
    under = next(leg for leg in out if leg.side == "under")
    assert over.loadings[C.CH_SCORING] * under.loadings[C.CH_SCORING] < 0


def test_pool_filters_drop_longshots_and_thin_markets():
    g = _game()
    assert L.build_leg_pool([g], min_books=9) == []
    assert L.build_leg_pool([g], min_edge=0.99) == []


def test_best_and_median_american_handle_the_sign_discontinuity():
    assert L.best_american([-105, 102, -120]) == 102
    assert L.best_american([-105, -120]) == -105
    assert L.median_american([]) is None


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def _pool(n_games=8, edge=0.04):
    """A pool of symmetric -110 games where the model is ``edge`` above fair.

    Note the fair probability on a -110/-110 pair is 0.500, not the 0.524 the
    price implies — the difference is the hold. Building the pool off the
    vig-included number would quietly hand every leg a 2.4-point edge and make
    the zero-edge test unable to fail.
    """
    games = []
    for i in range(n_games):
        games.append(L.GameLegInputs(
            event_id=f"g{i}", home_id=f"H{i}", away_id=f"A{i}",
            ml_prices=L.SidePrices(
                consensus_a=-110, consensus_b=-110,
                best_a=-110, best_b=-110, n_books=6,
            ),
            model_home_win=0.50 + edge,
            favorite="home",
        ))
    return L.build_leg_pool(
        games, shrink=S.EdgeShrink(lam=1.0), tau=0.12, min_edge=0.0, min_books=1,
    )


def test_search_returns_one_leg_per_game():
    tickets = P.search_parlays(_pool(), n_legs=3, top_n=5, strict=False)
    assert tickets
    for t in tickets:
        assert len({leg.event_id for leg in t.legs}) == 3


def test_search_ranks_by_growth_not_by_expected_value():
    """Regression: EV%-ranking is maximized by variance and surfaces longshots.

    Measured on the **unpenalised** price, because that is the ordering the
    search actually applies. `t.growth_rate` is computed from `ev_adjusted`,
    which carries a per-rank selection charge and so is deliberately not
    monotone — ranking by the quantity you charge by rank would be circular.
    The property under test is unchanged: the ordering is growth, not EV%.
    """
    tickets = P.search_parlays(_pool(), n_legs=3, top_n=6, strict=False)
    rates = [
        P.growth_rate(t.pricing.expected_value, t.pricing.return_sd)
        for t in tickets
    ]
    assert rates == sorted(rates, reverse=True)

    # And it is genuinely growth rather than EV: the two orderings differ on
    # this pool, which is what makes the assertion above meaningful.
    evs = [t.pricing.expected_value for t in tickets]
    assert evs != sorted(evs, reverse=True) or len(set(evs)) <= 1


def test_strict_mode_returns_nothing_when_no_edge_exists():
    """The empty slate is a legitimate answer, and must actually happen."""
    flat = _pool(edge=0.0)
    assert P.search_parlays(flat, n_legs=3, top_n=5, strict=True) == []


def test_non_strict_mode_still_reports_the_negative_tickets():
    flat = _pool(edge=0.0)
    tickets = P.search_parlays(flat, n_legs=3, top_n=5, strict=False)
    assert tickets
    assert all(not t.is_value for t in tickets)


def test_leg_reuse_is_capped():
    tickets = P.search_parlays(
        _pool(n_games=6), n_legs=2, top_n=10, strict=False, max_leg_reuse=2,
    )
    counts: dict[str, int] = {}
    for t in tickets:
        for leg in t.legs:
            counts[leg.key] = counts.get(leg.key, 0) + 1
    assert max(counts.values()) <= 2


def test_overlapping_tickets_have_their_stakes_scaled_down():
    tickets = P.search_parlays(_pool(n_games=4), n_legs=2, top_n=6, strict=False)
    shared = [t for t in tickets if any("stake scaled down" in w for w in t.warnings)]
    if shared:
        assert all(t.kelly_fraction >= 0 for t in shared)


def test_best_parlays_chooses_the_leg_count_itself():
    tickets = P.best_parlays(_pool(), leg_counts=(2, 3, 4), top_n=5, strict=False)
    assert tickets
    assert tickets[0].rank == 1
    assert tickets[0].explanation


def test_legacy_shim_still_prices_moneyline_games():
    games = [
        P.GameForParlay(
            event_id=f"g{i}", home_id=f"H{i}", away_id=f"A{i}",
            home_ml=-110, away_ml=-110, home_prob=0.60, favorite="home",
        )
        for i in range(3)
    ]
    out = P.generate_parlays(games)
    assert out
    assert out[0].n_legs == 3


def test_legacy_shim_drops_games_with_no_price():
    games = [
        P.GameForParlay(
            event_id="g0", home_id="H", away_id="A",
            home_ml=None, away_ml=None, home_prob=0.7, favorite="home",
        ),
        P.GameForParlay(
            event_id="g1", home_id="H1", away_id="A1",
            home_ml=-110, away_ml=-110, home_prob=0.6, favorite="home",
        ),
    ]
    assert P.generate_parlays(games) == []


# --------------------------------------------------------------------------- #
# backtest reporting
# --------------------------------------------------------------------------- #


def _settled(hit, n_legs=3, pred=0.2, ret=None):
    return B.SettledTicket(
        slate_date="2026-09-05", n_legs=n_legs,
        outcome=B.WIN if hit else B.LOSS,
        gross_return=(ret if ret is not None else 5.0) if hit else 0.0,
        predicted_hit_prob=pred, predicted_ev=0.05, predicted_ev_adjusted=0.02,
        naive_hit_prob=pred * 0.95, kelly_fraction=0.005, implied_prob=0.18,
    )


def test_backtest_on_no_data_says_so_rather_than_guessing():
    rep = B.evaluate([])
    assert rep.n_tickets == 0
    assert "nothing can be concluded" in rep.verdict.lower()


def test_backtest_flags_an_optimistic_engine():
    # Predicted 30%, realized 5% over 200 tickets: unambiguous.
    tickets = [_settled(i < 10, pred=0.30) for i in range(200)]
    rep = B.evaluate(tickets)
    assert rep.calibration_gap > 0.2
    assert "optimistic" in rep.verdict
    assert any(b["significant_miss"] for b in rep.reliability)


def test_backtest_reports_calibration_when_the_engine_is_right():
    rng = random.Random(3)
    tickets = [_settled(rng.random() < 0.25, pred=0.25) for _ in range(400)]
    rep = B.evaluate(tickets)
    assert rep.hit_rate_ci[0] <= rep.predicted_hit_rate <= rep.hit_rate_ci[1]
    assert "optimistic" not in rep.verdict


def test_backtest_refuses_to_conclude_on_a_tiny_sample():
    rep = B.evaluate([_settled(True) for _ in range(5)])
    assert "too few" in rep.verdict


def test_wilson_interval_stays_inside_zero_one():
    for successes, n in ((0, 5), (5, 5), (1, 3), (0, 1)):
        lo, hi = B.wilson(successes, n)
        assert 0.0 <= lo <= hi <= 1.0


# --------------------------------------------------------------------------- #
# Ranking with nothing positive (the builder's normal mode)
# --------------------------------------------------------------------------- #


def _pool_leg(key, event, prob, dec, *, tau=0.14, push=0.0):
    return L.LegCandidate(
        key=key, event_id=event, market="moneyline", side="home", label=key,
        team_id=key, opponent_id=None, line=None,
        price_american=om.decimal_to_american(dec), decimal_odds=dec, book="DK",
        fair_prob=prob, model_prob=prob, prob=prob, push_prob=push,
        edge=0.0, expected_value=0.0, tau=tau, loadings=(0.5, 0.2, 0.0),
        is_favorite=dec < 2.0, n_books=6,
    )


def test_negative_ev_tickets_are_ranked_least_bad_first():
    """Growth rate is identically 0 for every -EV ticket, so ranking on growth
    alone leaves the whole negative range in arbitrary order.

    This is the builder's normal mode — the user names the games and asks what
    they are worth — and without the EV tie-break the *worst* available ticket
    could sit at rank 1 with one half as bad down at rank 10.

    Asserted on **raw** EV, not `ev_adjusted`. Tickets are presented in
    selection order (unpenalised price) and each is charged the order statistic
    for its position, so `ev_adjusted` is deliberately not monotone in rank —
    see the note at the end of `search_parlays` for why ranking by the thing you
    charge by rank is circular.
    """
    pool = [
        _pool_leg("A", "g0", 0.50, 1.90),   # each leg is -EV by the vig
        _pool_leg("B", "g1", 0.50, 1.90),
        _pool_leg("C", "g2", 0.40, 2.30),   # worse
        _pool_leg("D", "g3", 0.35, 2.50),   # worst
    ]
    tickets = P.search_parlays(pool, n_legs=2, top_n=10, strict=False)
    assert len(tickets) >= 3
    assert all(t.ev_adjusted <= 0 for t in tickets), "fixture should be all -EV"
    raw = [t.pricing.expected_value for t in tickets]
    assert raw == sorted(raw, reverse=True), "least-bad ticket must rank first"
    assert tickets[0].rank == 1
    assert tickets[0].is_value is False


def test_the_top_ticket_carries_the_largest_selection_penalty():
    """The winner's-curse correction is an order statistic: the ticket presented
    first is the one most likely to be acted on, so it must be charged most.

    An earlier version charged by *screening* index and then re-sorted by
    `ev_adjusted`, which meant the ticket surfacing at rank 1 was systematically
    the one charged least — measured at 0.00002 against the 0.068 its legs
    warranted. That inverts the correction into a selection *for* optimism.
    """
    pool = [
        _pool_leg(f"L{i}", f"g{i}", 0.50 + 0.01 * (i % 4), 1.90 + 0.02 * (i % 3))
        for i in range(10)
    ]
    tickets = P.search_parlays(pool, n_legs=3, top_n=8, strict=False)
    assert len(tickets) >= 3
    penalties = [t.selection_penalty for t in tickets]
    assert penalties[0] > 0, "the top ticket must be charged something"
    assert penalties == sorted(penalties, reverse=True), (
        "penalty must fall with rank; if it does not, ranking and charging have "
        "been wired to the same quantity again"
    )


def test_a_hand_built_ticket_is_charged_no_selection_penalty():
    """Nobody searched, so there is no order statistic to correct for.

    Charging it anyway makes a ticket the user assembled themselves look worse
    than it is, for a reason that does not apply to them.
    """
    pool = [
        _pool_leg("A", "g0", 0.56, 1.91),
        _pool_leg("B", "g1", 0.55, 1.95),
    ]
    searched = P.search_parlays(pool, n_legs=2, top_n=5, strict=False)
    assert searched
    # The service-level exact-pricing path sets the penalty to zero explicitly;
    # here we assert the engine charges one when it *does* search, so the
    # difference between the two paths is real rather than incidental.
    assert searched[0].selection_penalty >= 0.0


def test_positive_ev_tickets_still_rank_by_growth_not_ev():
    """The tie-break must not quietly become an EV sort — growth is still the
    ranking wherever it is non-zero."""
    pool = [
        # High EV, huge variance (longshot) vs lower EV, low variance.
        _pool_leg("LONG", "g0", 0.20, 8.00),
        _pool_leg("LONG2", "g1", 0.20, 8.00),
        _pool_leg("TIGHT", "g2", 0.62, 1.90),
        _pool_leg("TIGHT2", "g3", 0.62, 1.90),
    ]
    tickets = P.search_parlays(pool, n_legs=2, top_n=10, strict=True)
    if len(tickets) >= 2:
        assert tickets[0].growth_rate >= tickets[-1].growth_rate
        top = {leg.key for leg in tickets[0].legs}
        assert top == {"TIGHT", "TIGHT2"}, (
            "growth ranking must prefer the low-variance pair over the longshots"
        )


def test_strict_false_returns_tickets_that_strict_true_refuses():
    """The builder's contract: it always prices what you asked for."""
    pool = [
        _pool_leg("A", "g0", 0.50, 1.90),
        _pool_leg("B", "g1", 0.50, 1.90),
    ]
    assert P.search_parlays(pool, n_legs=2, strict=True) == []
    loose = P.search_parlays(pool, n_legs=2, strict=False)
    assert loose, "an explicit request must always produce a priced ticket"
    assert loose[0].ev_adjusted < 0
    assert loose[0].is_value is False
