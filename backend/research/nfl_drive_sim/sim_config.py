"""Frozen hyperparameters for the drive sim.

P3 walk-forward uses this file unchanged. The sha256 in
``outputs/p2/sim_config.sha256`` is the hash of this source. Estimated rates
(PAT, return touchdowns, league means) are not hyperparameters. They are
computed from games that have already kicked off.
"""

from __future__ import annotations

DEV_FIT_SEASONS = (2015, 2016, 2017)
DEV_EVAL_SEASON = 2018

N_SIMS = 10_000
SEED = 20180907

# Exponential weights across a team's prior games. Age is in games, not days.
HALF_LIFE_GAMES = 8.0
# Empirical-Bayes shrinkage of a team or quarterback estimate toward the
# league mean. k is in games (or dropbacks / 30 for a quarterback).
SHRINK_K = 8.0
QB_SHRINK_DROPBACKS = 80.0

NEUTRAL_SCORE = 7.0
TWO_MINUTE_SECONDS = 120.0
EXPLOSIVE_PASS_YARDS = 20.0
EXPLOSIVE_RUN_YARDS = 10.0
FG_RANGE_YARDLINE = 38.0
RED_ZONE_YARDLINE = 20.0
SEC_CAP = 40.0

PACE_RIDGE_ALPHA = 10.0
YPP_RIDGE_ALPHA = 5.0
DRIVE_LOGIT_L2 = 1.0
DRIVE_LOGIT_ITERS = 15

# Ablation flag. Off uses team drive efficiency only. The 2018 dev report
# is fit with the layer on. P3 turns it off for the comparison.
YPP_LAYER = True

MAX_DRIVES = 40
MAX_PLAYS_PER_DRIVE = 14
MIN_STATE_PLAYS = 4

# 2018 overtime is one 10-minute period. See simulate.ot_rules.
OT_SECONDS = 600.0
REGULATION_HALF_SECONDS = 1800.0

QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)

SITUATIONS = ("neutral", "leading", "trailing", "two_minute")

DRIVE_OUTCOMES = (
    "TD",
    "FG_MADE",
    "FG_MISSED",
    "PUNT",
    "TURNOVER",
    "DOWNS",
    "SAFETY",
    "END_OF_HALF",
)

# State-varying columns are filled inside the sim. The rest are pre-game.
DRIVE_FEATURES = (
    "intercept",
    "yardline_100",
    "score_diff",
    "half_seconds",
    "timeouts",
    "fg_range",
    "red_zone",
    "two_minute",
    "trailing",
    "home",
    "dome",
    "off_td",
    "off_fg",
    "off_punt",
    "off_to",
    "def_td",
    "def_to",
    "qb_epa",
    "ypp_pass",
    "ypp_run",
)

PACE_FEATURES = (
    "intercept",
    "off_rate",
    "def_rate",
    "no_huddle",
    "incomplete",
    "pass_rate",
    "dome",
    "wind",
    "home",
)

YPP_FEATURES = (
    "intercept",
    "off_epa",
    "def_epa",
    "off_success",
    "def_success",
    "off_explosive",
    "def_explosive",
    "early_pass",
    "off_sack",
    "def_sack",
    "off_pressure",
    "def_pressure",
    "qb_epa",
    "qb_cpoe",
    "home",
    "dome",
)
