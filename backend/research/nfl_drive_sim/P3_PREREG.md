# P3 preregistration

This document locks the comparison. It does not score any game. QA has to lock it before any P3 number is computed. The simulator stays on the P2 freeze in `FREEZE.md`. Nothing in `sim_config.py` is retuned for P3.

## Seasons

Walk forward over the seasons already in the research cache, 2015 through 2026. A week is fit on kickoffs strictly before that week's first kickoff ET, the same cutoff the 2018 dev run used. A later game in the week does not refit. Kickoffs on or after 2027-01-01 ET are excluded.

A week is scored only when that pre-week fit already contains at least 256 completed games, one regular season. Scoring therefore starts in 2016. 2015 is fit-only.

2018 is dev-touched. Every results table is reported twice: all scored games, and all scored games except 2018.

## Comparator

The comparator is a v5 walk-forward refit. It is not the shipped `MARGIN_COEFS`, `TOTAL_COEFS`, or `MARGIN_COEFS_NO_MKT` in `team_ratings_v5.py`. Those shipped coefficients are not read.

For each scoring week, build ratings and the quarterback book with `fit_ratings` and `build_qb_book` on plays whose games kicked off before that week's first kickoff. Do not call `market_ratings`. Do not include `mkt_d`. The stage-2 design, refit by ordinary least squares on those same prior games, is:

- Margin: `epa_d`, `pts_d`, `st_d`, `qb_d`, plus home-field (1, or 0 at a neutral site).
- Total: `pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`, plus a constant.

The point predictions are that week's refit applied to the week's features. No closing line, total, or moneyline enters the fit or the prediction.

v5 has no sample distribution. Its predictive distributions, used only for CRPS and the home-win probability, are the ones in `prediction_dist`:

- Margin ~ Normal(refit margin, 13.5).
- Total ~ Normal(refit total, 10.0).
- Home-win probability = Φ(refit margin / 13.5), which is P(margin > 0). The sigmas are the fallback constants `NFL_MARGIN_SIGMA` and `NFL_TOTAL_SIGMA`. They are not the admin registry, and they are not re-estimated.

Margin CRPS and total CRPS for v5 are the closed form `crps_normal` of those two normals. The sim's CRPS is `crps_ensemble` on its frozen `N_SIMS` draws. Margin MAE uses the sim's mean margin against the v5 refit margin. Total MAE does the same for the total. Home-win Brier is (p − 1{home score > away score})². The sim's p is the share of draws with margin > 0. v5's p is Φ(refit margin / 13.5). A tie scores 0 on both, matching the 2018 dev Brier.

## Primary metrics

For each game, d = sim score − v5 score, on margin CRPS, total CRPS, margin MAE, total MAE, and home-win Brier. Lower is better, so a negative mean d favors the sim.

The reported uncertainty is a Webb wild cluster bootstrap of the mean of d. The cluster is `(season, week)`. Center the game-level d values by their sample mean, multiply every game in a week by one Webb weight, and add the sample mean back. The Webb weights are −√1.5, −1, −√0.5, √0.5, 1, √1.5 with probabilities 1/6, 1/3, 1/6, 1/6, 1/3, 1/6. Use 999 replicates from `numpy.random.Generator(20261115)`. The 90% interval is the 5th and 95th percentiles of those 999 means. Each analysis set (all seasons, seasons except 2018, and each ablation below) draws its own weights from that same seed.

The primary comparison uses the yards-per-play setting chosen by the ablation below. The other setting is still reported.

## Yards-per-play ablation

This is pre-stated. Let d be yards-per-play on minus yards-per-play off, for the same game.

Keep the layer only if the mean margin-CRPS d is negative and its 90% upper bound is negative, and the total-CRPS d has a 90% upper bound of +0.05 or lower. Otherwise the reported comparison uses the layer off. The bounds are not revised after the interval is seen.

## Pace validation

Once per season, and once on the pooled scored games, report plays per team, plays per game, drives per team, and seconds per play, each as MAE and bias against the naive baseline from the 2018 dev report. Also report 80% and 90% play-count interval coverage, and the total-CRPS gap of predicted pace minus the game-average pace oracle. That oracle is a handicapped benchmark. A gap whose 90% interval covers zero is not a win for the predicted pace.

## Tracked metrics

Report, and do not promote to primary:

- share of games with absolute final margin 3
- share of games with absolute final margin 7
- regulation-tie rate
- overtime-entry rate
- seconds-per-play bias
- a margin-at-5:00-left series

The series is one number per game: the mean, across sims, of home margin minus away margin at the first simulated state where 300 or fewer seconds remain in the second half. Commit it as a column on the per-game file. It is not a scoring target.

## Disagreement flags

A game is flagged when the sim and the v5 refit disagree by at least:

- 3 points of absolute margin
- 4 points of absolute total
- 10 percentage points of home-win probability

Report each flag rate. If a rate lands far outside 10–15%, report that. Do not change 3, 4, or 10.

## Wording

P3 is not a fair ATS or key-number comparison against v5 until a fourth-quarter endgame model is accepted on a later season. The results say that in the first paragraph. Do not describe the primary table as an ATS test or a key-number test.

## Files

Commit, under `backend/research/nfl_drive_sim/outputs/p3/`:

- `per_game_d.csv`, one row per scored game and yards-per-play setting, with the five d columns, the three flags, the 5:00 margin, season, week, kickoff ET, and a 2018 dev-touched flag
- `manifest.sha256` covering that file and the written report

No other artifact is required for the lock.

## Market and runtime

Market data is out of the fit, the sim, and the primary scores. It may be used only for a later post-hoc grade, and that grade is not part of this lock. No Odds API call and no other paid call.

Stay on the frozen `N_SIMS` and seed. Run the yards-per-play layer on and off, and nothing else. No grid, no extra sim count, and no season outside the cache.
