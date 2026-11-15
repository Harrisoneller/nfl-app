"""Locked constants for the single-feature preregistration.

Phase 1 scaffolding only. These numbers are the procedure in
``docs/prereg/PROTOCOL.md``. They are not estimates, and this module does not
read a schedule, a play-by-play file, or a shipped coefficient table.

The base specification is market-blind qb-ridge-v5. Stage-2 weights are
walk-forward refit. The shipped coefficient tables are not an input.
"""
from __future__ import annotations

# Scoring constants. Untuned. They are the fallback constants in
# prediction_dist, not the residual SD of any fitted model and not the
# admin-tunable registry values.
MARGIN_SIGMA = 13.5
TOTAL_SIGMA = 10.0

# Season rule from QA. No 2013–2026 season is an untouched confirmatory season.
# 2012 and earlier are a different regime and do not enter this study.
# Stage A is the exploratory screen. January 2027 is the harm check.
# Stage C, the confirmatory sample, is the 2027 regular season.
REGIME_LAST = 2012
FEATURE_HISTORY_FIRST = 2013
TRAIN_FIRST = 2015
EXPLORATORY_TEST_FIRST = 2015
EXPLORATORY_TEST_LAST = 2026
HARM_SEASON = 2026
CONFIRM_SEASON = 2027
CONFIRM_WEEK_FIRST = 1
CONFIRM_WEEK_LAST = 18
# Expectations, not a frozen game list. Harm is every 2026-season January
# kickoff, about a week-17/18 slate plus the January playoff rounds.
HARM_N_EXPECTED = 43
HARM_CLUSTERS_EXPECTED = 5
CONFIRM_N_EXPECTED = 272
CONFIRM_CLUSTERS_EXPECTED = 18
# One-sided 90% upper bound for the screen. Harm passes at or below this.
SCREEN_UB_QUANTILE = 0.90
HARM_UB_LIMIT = 0.25
# Two-sided 90% lower bound on ATS edge versus a fair close.
ATS_LB_QUANTILE = 0.05
ATS_LB_LIMIT = -0.02
# Reported power. beta = 0.5 is a footnote and is not this value.
POWER = 0.80
STRUCTURED_SHIFT_POINTS = 1.0

# Holm, one-sided, family of the three preregistered features.
HOLM_FAMILY = 3
HOLM_ALPHA = 0.025
HOLM_ALPHA_STAR = HOLM_ALPHA / HOLM_FAMILY

# Footnote only. The reported MDE uses POWER = 0.80, so beta = 0.20.
BETA_FOOTNOTE = 0.5

BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261115
BOOTSTRAP_UPPER_Q = 0.975

# Base allowlists. Margin has no intercept column; home field is ``hfa``.
# Total has an intercept. ``plays_s`` stays in the total model; neutral pace
# is an added column, not a replacement. ``pass_d`` is not a stage-2 term.
MARGIN_BASE = ("epa_d", "pts_d", "st_d", "qb_d", "hfa")
TOTAL_BASE = ("pts_s", "epa_s", "env2", "plays_s", "qb_s")

# Pressure. PFR era starts in 2018. Shrinkage is 8 pseudo-games, the heavy
# end of the 6–8 game range, and it is not searched.
PRESSURE_FIRST_SEASON = 2018
PRESSURE_K_GAMES = 8.0
PRESSURE_WP_LO = 0.10
PRESSURE_WP_HI = 0.90

# Offensive line. Questionable is available. The center flag weight is part
# of the one feature; it is not a second coefficient.
OL_STARTER_SHARE = 0.80
OL_STARTER_GAMES = 4
OL_MAX_STARTERS = 5
OL_CENTER_WEIGHT = 1.0
OL_POSITIONS = frozenset({"C", "G", "T", "OL", "OT", "OG", "LT", "LG", "RG", "RT"})
OL_CENTER_POSITIONS = frozenset({"C"})
OL_MISSING_STATUS = frozenset({
    "out", "doubtful", "ir", "injured reserve", "pup", "reserve/injured",
})

# Neutral pace. The clock cap is the 40-second play clock. A longer gap is a
# stoppage, not a snap. v4's 60-second cap is not used.
PACE_WP_LO = 0.20
PACE_WP_HI = 0.80
PACE_SCORE_WITHIN = 7
PACE_LAST_TWO_MINUTES = 120
PACE_CLOCK_CAP = 40.0
PACE_MIN_DELTAS = 30
PACE_SEASONS_BACK = 2

FEATURES: dict[str, dict[str, object]] = {
    "pressure_x": {
        "endpoint": "margin",
        "target": "margin",
        "expected_sign": -1,
        "base": MARGIN_BASE,
        "sigma": MARGIN_SIGMA,
        "intercept": False,
    },
    "ol_d": {
        "endpoint": "margin",
        "target": "margin",
        "expected_sign": -1,
        "base": MARGIN_BASE,
        "sigma": MARGIN_SIGMA,
        "intercept": False,
    },
    "neutral_pace_sum": {
        "endpoint": "total",
        "target": "total",
        "expected_sign": -1,
        "base": TOTAL_BASE,
        "sigma": TOTAL_SIGMA,
        "intercept": True,
    },
}

MARKET_NAME_PARTS = (
    "spread",
    "moneyline",
    "odds",
    "price",
    "kalshi",
    "vig",
    "juice",
    "mkt",
    "market",
    "opener",
    "closing_line",
    "total_line",
    "implied",
)
