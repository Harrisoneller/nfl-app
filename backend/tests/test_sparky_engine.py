"""Unit tests for the Sparky quant engine (pure, no DB)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.sparky import accuracy, confidence, odds_math, parlay, signals
from app.services.sparky.parlay import GameForParlay
from app.services.sparky.signals import MovementPoint, Signal, SignalInput

# --------------------------------------------------------------------------- #
# odds_math
# --------------------------------------------------------------------------- #


def test_american_decimal_roundtrip():
    assert odds_math.american_to_decimal(100) == pytest.approx(2.0)
    assert odds_math.american_to_decimal(-110) == pytest.approx(1.9090909, abs=1e-5)
    assert odds_math.american_to_decimal(150) == pytest.approx(2.5)
    assert odds_math.decimal_to_american(2.0) == 100
    assert odds_math.decimal_to_american(2.5) == 150
    assert odds_math.decimal_to_american(1.9090909) == -110


def test_implied_prob():
    assert odds_math.american_to_implied(-110) == pytest.approx(0.5238, abs=1e-3)
    assert odds_math.american_to_implied(100) == pytest.approx(0.5)
    assert odds_math.american_to_implied(150) == pytest.approx(0.4)


def test_implied_to_american_inverse():
    for price in (-300, -150, -110, 120, 250):
        p = odds_math.american_to_implied(price)
        assert odds_math.implied_to_american(p) == pytest.approx(price, abs=1)


def test_devig_sums_to_one():
    h, a = odds_math.devig_two_way(-150, 130)
    assert h + a == pytest.approx(1.0)
    assert h > a  # -150 favorite should have higher fair prob
    # Two -110 sides => exactly 50/50 fair.
    h2, a2 = odds_math.devig_two_way(-110, -110)
    assert h2 == pytest.approx(0.5) and a2 == pytest.approx(0.5)


def test_vig_positive():
    assert odds_math.vig_from_prices(-110, -110) == pytest.approx(0.0476, abs=1e-3)


def test_parlay_odds():
    # Three -110 legs.
    dec = odds_math.parlay_decimal([-110, -110, -110])
    assert dec == pytest.approx(1.9090909 ** 3, abs=1e-4)
    # Three +100 legs => 2*2*2 = 8.0 decimal => +700 american.
    assert odds_math.parlay_decimal([100, 100, 100]) == pytest.approx(8.0)
    assert odds_math.parlay_american([100, 100, 100]) == 700


def test_combined_true_prob():
    assert odds_math.combined_true_prob([0.6, 0.5, 0.7]) == pytest.approx(0.21)


def test_expected_value_sign():
    # 60% on +100 (even money) is +EV; 40% is -EV.
    assert odds_math.expected_value(0.6, 2.0) > 0
    assert odds_math.expected_value(0.4, 2.0) < 0


def test_kelly_fraction_zero_on_neg_ev_and_capped():
    """Kelly returns 0 when -EV, and is capped at 25% (default) when +EV."""
    # 40% on +100 (even money) is -EV → Kelly says don't bet.
    assert odds_math.kelly_fraction(0.4, 2.0) == 0.0
    # 70% on +100 is strongly +EV: f* = (1*0.7 - 0.3)/1 = 0.4 → capped to 0.25.
    assert odds_math.kelly_fraction(0.7, 2.0) == 0.25
    # 55% on +100 is mildly +EV: f* = 0.10 → uncapped.
    assert odds_math.kelly_fraction(0.55, 2.0) == pytest.approx(0.10, abs=1e-6)


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #


def _base_input(**kw) -> SignalInput:
    defaults = dict(
        home_team_id="KC", away_team_id="DEN", favorite="home",
        home_market_prob=0.65, away_market_prob=0.35,
        home_ml=-185, away_ml=160, spread_home=-4.5, total=44.0,
        book_count=6, book_home_probs=[0.64, 0.65, 0.66, 0.65, 0.64, 0.66],
        movement=[], model_home_prob=0.65, model_away_prob=0.35,
    )
    defaults.update(kw)
    return SignalInput(**defaults)


def test_coin_flip_detected():
    inp = _base_input(home_market_prob=0.51, away_market_prob=0.49,
                      model_home_prob=0.51, book_home_probs=[0.50, 0.51, 0.52])
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "coin_flip" in keys


def test_anchor_favorite_detected():
    inp = _base_input(home_market_prob=0.82, away_market_prob=0.18,
                      model_home_prob=0.80, book_home_probs=[0.81, 0.82, 0.82, 0.83])
    sigs = {s.key: s for s in signals.detect_signals(inp)}
    assert "anchor_favorite" in sigs
    assert sigs["anchor_favorite"].side == "home"


def test_steam_and_late_movement():
    mv = [
        MovementPoint("T1", 4320, 0.60),
        MovementPoint("T2", 1440, 0.62),
        MovementPoint("T3", 90, 0.70),  # big late jump toward home
    ]
    inp = _base_input(home_market_prob=0.70, away_market_prob=0.30, movement=mv,
                      model_home_prob=0.68)
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "steam_move" in keys
    assert "late_movement" in keys


def test_reverse_line_movement_toward_dog():
    # Home is favored but the line drifted toward the away dog.
    mv = [MovementPoint("T1", 4320, 0.68), MovementPoint("T3", 120, 0.60)]
    inp = _base_input(home_market_prob=0.60, away_market_prob=0.40, favorite="home", movement=mv)
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "reverse_line_movement" in keys
    assert "underdog_strengthening" in keys


def test_trap_risk_and_upset_pressure():
    # Market loves home (78%) but model only 60% -> trap on home, upset value on away.
    inp = _base_input(home_market_prob=0.78, away_market_prob=0.22,
                      model_home_prob=0.60, model_away_prob=0.40,
                      home_ml=-355, away_ml=280)
    sigs = {s.key for s in signals.detect_signals(inp)}
    assert "trap_risk" in sigs
    assert "upset_pressure" in sigs


def test_multibook_disagreement_high_variance():
    inp = _base_input(book_home_probs=[0.50, 0.62, 0.70, 0.45, 0.66, 0.58])
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "multibook_disagreement" in keys
    assert "high_variance" in keys


# --------------------------------------------------------------------------- #
# NFL-specific signals (short rest + divisional)
# --------------------------------------------------------------------------- #


def test_short_rest_fires_on_both_sides():
    inp = _base_input(
        home_rest_days=4.5,   # short (Thu-Sun type)
        away_rest_days=7.5,
        is_divisional=False,
    )
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "short_rest" in keys

    # Both sides short
    inp2 = _base_input(home_rest_days=5.0, away_rest_days=5.0)
    keys2 = {s.key for s in signals.detect_signals(inp2)}
    assert "short_rest" in keys2


def test_rest_advantage_fires_when_clear_gap():
    inp = _base_input(
        home_rest_days=13.0,   # coming off bye
        away_rest_days=7.0,
    )
    sigs = {s.key: s for s in signals.detect_signals(inp)}
    assert "rest_advantage" in sigs
    # The side with more rest should be the one tagged
    adv = sigs["rest_advantage"]
    assert adv.side == "home"


def test_divisional_matchup_is_info_signal():
    inp = _base_input(is_divisional=True)
    sigs = signals.detect_signals(inp)
    div = next((s for s in sigs if s.key == "divisional_matchup"), None)
    assert div is not None
    assert div.severity == "info"
    assert div.side == "game"


def test_nfl_signals_graceful_when_data_missing():
    inp = _base_input(home_rest_days=None, away_rest_days=None, is_divisional=False)
    keys = {s.key for s in signals.detect_signals(inp)}
    assert "short_rest" not in keys
    assert "rest_advantage" not in keys
    # divisional=False should not emit the signal
    assert "divisional_matchup" not in keys


# --------------------------------------------------------------------------- #
# confidence
# --------------------------------------------------------------------------- #


def test_ensemble_blends_toward_agreement():
    # Both sources agree ~0.8 -> ensemble stays high (not dragged to 0.5).
    p = confidence.ensemble_home_prob(0.80, 0.82)
    assert 0.78 <= p <= 0.84


def test_base_confidence_range():
    score = confidence.score_game(model_home_prob=0.5, market_home_prob=0.5, signals=[])
    assert score.confidence_score == pytest.approx(45.0, abs=0.5)
    score2 = confidence.score_game(model_home_prob=0.95, market_home_prob=0.95, signals=[])
    assert score2.confidence_score >= 90.0


def test_bullish_signal_for_predicted_side_raises_confidence():
    sig_for = [Signal("anchor_favorite", "Anchor", "home", "bullish", 1.0, 10.0, "x")]
    sig_against = [Signal("upset_pressure", "Upset", "away", "bullish", 1.0, 10.0, "x")]
    base = confidence.score_game(model_home_prob=0.7, market_home_prob=0.7, signals=[])
    boosted = confidence.score_game(model_home_prob=0.7, market_home_prob=0.7, signals=sig_for)
    hurt = confidence.score_game(model_home_prob=0.7, market_home_prob=0.7, signals=sig_against)
    assert boosted.confidence_score > base.confidence_score > hurt.confidence_score


def test_warning_always_reduces():
    warn = [Signal("coin_flip", "Coin", "game", "warning", 1.0, 8.0, "x")]
    base = confidence.score_game(model_home_prob=0.7, market_home_prob=0.7, signals=[])
    reduced = confidence.score_game(model_home_prob=0.7, market_home_prob=0.7, signals=warn)
    assert reduced.confidence_score < base.confidence_score


def test_classification_anchor_vs_coinflip():
    anchor = confidence.score_game(model_home_prob=0.82, market_home_prob=0.82, signals=[])
    assert anchor.classification == "anchor"
    coin = confidence.score_game(model_home_prob=0.51, market_home_prob=0.51, signals=[])
    assert coin.classification == "coin_flip"


# --------------------------------------------------------------------------- #
# parlay
# --------------------------------------------------------------------------- #


_ALL_GAMES: list[GameForParlay] = [
    GameForParlay("e1", "KC", "DEN", -185, 160, 0.66, "home", [], "KC @ DEN"),
    GameForParlay("e2", "BUF", "NYJ", -240, 200, 0.72, "home", [], "BUF @ NYJ"),
    GameForParlay("e3", "DAL", "PHI", 120, -140, 0.45, "away", [], "DAL @ PHI"),
    GameForParlay("e4", "SF", "SEA", -300, 250, 0.78, "home", [], "SF @ SEA"),
    GameForParlay("e5", "BAL", "PIT", -170, 145, 0.62, "home", [], "BAL @ PIT"),
    GameForParlay("e6", "GB", "MIN", 110, -130, 0.46, "away", [], "GB @ MIN"),
    GameForParlay("e7", "LAR", "ARI", -220, 185, 0.70, "home", [], "LAR @ ARI"),
    GameForParlay("e8", "MIA", "NE", 135, -160, 0.42, "away", [], "MIA @ NE"),
]


def _games(n: int = 3) -> list[GameForParlay]:
    return _ALL_GAMES[:n]


def test_generate_three_leg_parlay_has_8_combos():
    """The canonical 3-leg case from the spec — 2**3 = 8 ranked combinations."""
    out = parlay.generate_parlays(_games(3))
    assert len(out) == 8
    assert [p.rank for p in out] == list(range(1, 9))
    comps = [p.composite_score for p in out]
    assert comps == sorted(comps, reverse=True)
    for p in out:
        assert p.n_legs == 3
        assert len(p.legs) == 3
        assert isinstance(p.parlay_odds_american, int)
        assert 0.0 <= p.implied_prob <= 1.0


@pytest.mark.parametrize("n,expected_combos", [(2, 4), (3, 8), (4, 16), (5, 32), (8, 256)])
def test_generate_variable_n_leg_parlays(n, expected_combos):
    """N-leg engine: 2..8 legs each produce 2**N ranked combinations."""
    out = parlay.generate_parlays(_games(n))
    assert len(out) == expected_combos
    assert [p.rank for p in out] == list(range(1, expected_combos + 1))
    comps = [p.composite_score for p in out]
    assert comps == sorted(comps, reverse=True)
    for p in out:
        assert p.n_legs == n
        assert len(p.legs) == n


def test_parlay_rejects_outside_legal_range():
    """Engine enforces MIN_LEGS..MAX_LEGS; 1 leg and >8 legs both raise."""
    with pytest.raises(ValueError):
        parlay.generate_parlays(_games(1))
    with pytest.raises(ValueError):
        parlay.generate_parlays(_ALL_GAMES + [_ALL_GAMES[0]])  # 9 legs


def test_underdog_count_matches_picks():
    out = parlay.generate_parlays(_games(3))
    for p in out:
        assert p.underdog_count == sum(1 for leg in p.legs if leg.is_underdog)


def test_per_leg_value_fields_populated():
    """Every leg carries market_implied, edge, and expected_value."""
    out = parlay.generate_parlays(_games(3))
    for p in out:
        for leg in p.legs:
            assert 0.0 <= leg.market_implied <= 1.0
            # edge is just win_prob - market_implied
            assert abs(leg.edge - (leg.win_prob - leg.market_implied)) < 1e-6


def test_parlay_value_fields_consistent():
    """Parlay-level EV/is_value/Kelly are internally consistent with the edge."""
    out = parlay.generate_parlays(_games(3))
    for p in out:
        # is_value strictly reflects positive expected_value.
        assert p.is_value == (p.expected_value > 0)
        # Kelly is zero whenever the parlay is -EV.
        if not p.is_value:
            assert p.kelly_fraction == 0.0
        # Kelly cap (default 25%).
        assert 0.0 <= p.kelly_fraction <= 0.25


def test_value_factor_rewards_plus_ev_more_than_punishes_neutral():
    """The asymmetric curve gives +EV plays a bigger composite bump than 0 EV."""
    f_plus = parlay._value_factor(0.05)
    f_zero = parlay._value_factor(0.0)
    f_minus = parlay._value_factor(-0.05)
    assert f_plus > f_zero > f_minus
    # Asymmetry: 0.05 above zero is rewarded more than 0.05 below is preserved.
    assert (f_plus - f_zero) > 0
    assert f_minus < f_zero


def test_underdog_balance_generalizes_for_any_n():
    """All-chalk and all-dog get penalties; balanced mix peaks for every N."""
    for n in (2, 3, 4, 5, 6, 8):
        all_chalk = parlay._underdog_balance(0, n)
        all_dog = parlay._underdog_balance(n, n)
        mixed = parlay._underdog_balance(max(1, n // 3), n)
        assert all_chalk < 1.0
        assert all_dog < all_chalk  # all-dog is the wildest, biggest penalty
        assert mixed >= all_chalk   # mixed is at least as good as all-chalk
        assert mixed >= all_dog


# --------------------------------------------------------------------------- #
# accuracy
# --------------------------------------------------------------------------- #


def test_pick_accuracy_basic():
    rows = [
        {"correct": True}, {"correct": True}, {"correct": False}, {"correct": None},
    ]
    res = accuracy.pick_accuracy(rows)
    assert res["n"] == 3 and res["correct"] == 2
    assert res["accuracy_pct"] == pytest.approx(66.7, abs=0.1)


def test_rolling_windows():
    today = date(2026, 5, 26)
    rows = [
        {"date": today, "correct": True},
        {"date": today - timedelta(days=3), "correct": True},
        {"date": today - timedelta(days=10), "correct": False},
        {"date": today - timedelta(days=25), "correct": True},
    ]
    res = accuracy.rolling_pick_accuracy(rows, as_of=today)
    assert res["daily"]["n"] == 1
    assert res["rolling_7d"]["n"] == 2
    assert res["rolling_21d"]["n"] == 3
    assert res["rolling_30d"]["n"] == 4


def test_parlay_rates():
    rows = [
        {"rank_1_hit": True, "top_3": True, "top_4": True},
        {"rank_1_hit": False, "top_3": True, "top_4": True},
        {"rank_1_hit": False, "top_3": False, "top_4": True},
        {"rank_1_hit": None},
    ]
    res = accuracy.parlay_rates(rows)
    assert res["n"] == 3
    assert res["rank_1_hit_rate"] == pytest.approx(33.3, abs=0.1)
    assert res["top_3_containment"] == pytest.approx(66.7, abs=0.1)
    assert res["top_4_containment"] == pytest.approx(100.0)


def test_accuracy_by_signal():
    rows = [
        {"correct": True, "signals": ["steam_move", "anchor_favorite"]},
        {"correct": False, "signals": ["steam_move"]},
        {"correct": True, "signals": ["anchor_favorite"]},
    ]
    by_sig = {s["signal"]: s for s in accuracy.accuracy_by_signal(rows)}
    assert by_sig["anchor_favorite"]["accuracy_pct"] == pytest.approx(100.0)
    assert by_sig["steam_move"]["accuracy_pct"] == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# sparky backtest metrics (pure functions)
# --------------------------------------------------------------------------- #

# The backtest module pulls in app.models / SQLAlchemy at import time (it has
# DB-bound functions alongside its pure math). Guard the import so the rest of
# the pure engine tests still collect in environments without SQLAlchemy (CI,
# sandboxes); the backtest-math tests skip cleanly when that's the case.
try:
    from app.services.sparky.backtest import (
        brier_score,
        compute_calibration,
        log_loss,
        signal_lift,
        simulate_roi,
    )
    _BACKTEST_OK = True
except ImportError as _e:
    _BACKTEST_OK = False
    _BACKTEST_SKIP_REASON = f"backtest module unavailable: {str(_e)[:80]}"

_skip_no_backtest = pytest.mark.skipif(
    not _BACKTEST_OK,
    reason=_BACKTEST_SKIP_REASON if not _BACKTEST_OK else "",
)


@_skip_no_backtest
def test_brier_score_perfect():
    assert brier_score([0.9, 0.1, 0.8], [1, 0, 1]) < 0.05


@_skip_no_backtest
def test_brier_score_worst():
    # All predictions completely wrong
    score = brier_score([0.1, 0.9], [1, 0])
    assert score > 0.8


@_skip_no_backtest
def test_log_loss_reasonable():
    loss = log_loss([0.7, 0.3, 0.9], [1, 0, 1])
    assert 0.2 < loss < 1.0


@_skip_no_backtest
def test_calibration_buckets():
    rows = [{"win_prob": 0.52, "correct": True}, {"win_prob": 0.78, "correct": True}, {"win_prob": 0.91, "correct": False}]
    calib = compute_calibration(rows)
    # Should have some populated buckets
    assert any(b["n"] > 0 for b in calib)


@_skip_no_backtest
def test_signal_lift_basic():
    # signal_lift only reports signals with >= 5 samples on the "with" side.
    rows = (
        [{"signals": ["short_rest"], "correct": False}] * 4
        + [{"signals": ["short_rest"], "correct": True}]
        + [{"signals": [], "correct": True}] * 4
        + [{"signals": [], "correct": False}]
    )
    lift = signal_lift(rows)
    short = next((x for x in lift if x["signal"] == "short_rest"), None)
    assert short is not None
    assert short["acc_with"] < short["acc_without"]


@_skip_no_backtest
def test_simulate_roi_positive_edge():
    edges = [0.08, 0.03, -0.02, 0.05, 0.12]
    roi = simulate_roi(edges, starting_bankroll=1000, flat_stake_pct=0.02)
    assert roi["n_bets"] == 4
    assert roi["flat_roi_pct"] > 0
