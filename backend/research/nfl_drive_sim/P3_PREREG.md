# P3 preregistration

This document locks the comparison. It does not score any game. QA has to lock it before any P3 number is computed. No sim-minus-v5 margin, total, CRPS, MAE, or Brier has been computed on a real season.

The simulator stays on the P2 freeze in `FREEZE.md`. Nothing in `sim_config.py` is retuned. `v5_replay.py` is the no-market path those rules describe. Its tests are synthetic.

## Seasons and the game set

The schedule is the cached nflverse file `backend/research/nfl_drive_sim/.cache/schedules/games.parquet`. Kickoff is `gameday` plus `gametime`, read as an America/New_York wall clock. A missing clock is 13:00 Eastern. A row is eligible only when that kickoff is strictly before 2027-01-01 00:00 America/New_York.

A game is completed when all of these hold:

- `home_score` and `away_score` are both finite
- `home_team` and `away_team` are both non-null
- the kickoff parses and is strictly before the cutoff above

A null score is not completed. That includes the 90 regular-season rows in 2026 that have a kickoff before the cutoff and no score, and it includes every row on or after the cutoff.

Regular season means `game_type == REG`. Playoff means `game_type` in {WC, DIV, CON, SB}.

The primary analysis is regular season only. Playoff games are descriptive only. They never enter the primary call, the yards-per-play ablation, the Holm family, or the drop-rate denominator for that call.

2015 is fit-only. The 2016 week-1 training floor is the 256 completed 2015 regular-season games, every one of which kicks off before the first 2016 regular-season kickoff (2016-09-08 20:30 America/New_York). No 2016 game is in that fit.

A later week is fit on completed regular-season games whose kickoff is strictly before that week's first regular-season kickoff. The same cutoff removes anything on or after 2027-01-01.

Completed regular-season counts before the cutoff, from the schedule and not from either model:

| season | role | completed REG |
| --- | --- | ---: |
| 2015 | fit only | 256 |
| 2016 | primary and ablation | 256 |
| 2017 | primary and ablation | 256 |
| 2018 | dev-touched, sensitivity only | 256 |
| 2019 | primary and ablation | 256 |
| 2020 | primary and ablation | 256 |
| 2021 | primary and ablation | 272 |
| 2022 | primary and ablation | 271 |
| 2023 | primary and ablation | 272 |
| 2024 | primary and ablation | 272 |
| 2025 | primary and ablation | 272 |
| 2026 | primary and ablation, partial | 151 |

The primary set is those rows for 2016, 2017, and 2019 through 2026: 2,534 games, 169 season-weeks. 2018's 256 regular-season games are the with-2018 sensitivity and cannot choose the yards-per-play layer. Thirty-one 2026 regular-season rows sit on or after the cutoff and are excluded. Completed playoff games before the cutoff number 133 and stay descriptive.

## One game set

Both models use the same pre-game quarterback rule.

1. The depth-chart QB1 from `pregame_quarterback`: a weekly chart is as-of kickoff minus one hour, and a snapshot chart is the latest `dt` strictly before kickoff.
2. If that is missing, the prior-start quarterback is the depth-chart QB1 of that team's most recent completed game whose kickoff is strictly before this kickoff. Walk backward through those games. Do not use `passer_player_id` or any other in-game passer.
3. If neither exists, the quarterback is missing.

A missing quarterback on either side drops the game from both models. If either model raises, returns a non-finite margin, total, sigma, or home-win probability, the game is dropped from both. The two models are then scored on the intersection.

Drop counts are reported by reason (`missing_qb`, `model_failure`). The drop rate is dropped games divided by completed games in the analysis set before drops. A rate above 2% voids the run for QA review, and no SUCCESS, FAILURE, or INCONCLUSIVE call is issued.

## v5 replay

`v5_replay.py` is the path. It does not call `build_week_model` (that calls `market_ratings`), it does not call `market_ratings`, and it does not run `scripts.backtest_v5`. It does not read `MARGIN_COEFS`, `MARGIN_COEFS_NO_MKT`, or `TOTAL_COEFS`.

Market columns are stripped with `strip_market_columns` before a frame is used, and `assert_no_market_columns` then refuses the frame. The cache handed to the replay is that stripped frame. No Odds API call and no other paid call. Closing lines are not features and are not targets.

Frozen `DEFAULT_PARAMS` are hashed as JSON with sorted keys and compact separators. The sha256 is `f2e0072102c421144df23dceec743c7b6eab51d17858be4279709ad5238eb22a`. The `mkt_*` keys sit in that dict and are not read, because `market_ratings` is not called. `fit_ratings` and `build_qb_book` receive this dict. `seasons_back` is therefore 2, the frozen value. Stage-2 is not limited to that window.

Ratings and the quarterback book for a week see only rows whose kickoff is strictly before that week's first kickoff and strictly before the 2027 cutoff. The pre-game quarterback id above is the id passed to `QBBook.delta`. The in-game passer remains only the historical label inside `team_game_rows` for past games' pass totals.

Stage-2 is ordinary least squares, `numpy.linalg.lstsq` with `rcond=None`, on every completed regular-season game in that pre-week window. A week is identified only when that window has at least 32 games. 2016 week 1 has 256, so it clears the floor on 2015 alone.

The margin design is `epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`. `hfa` is 1, or 0 at a neutral site. There is no `mkt_d`. The total design is `pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`, and a column of ones. `env2` is twice the points-rating intercept from `fit_ratings`. The v5 total is that linear prediction. The actual total is `home_score + away_score`. The actual margin is `home_score - away_score`. A non-finite coefficient or prediction is a model failure.

## Sigma

Sigma is not 13.5 and not 10.0. For each scoring week, and separately for margin and total, take the walk-forward residuals of completed regular-season games whose kickoff is strictly before that week's first kickoff. A residual exists only for a game that itself received a finite walk-forward prediction. 2015 is the seed: 2016 week 1 uses the 2015 regular-season residuals and nothing from 2016. Later weeks add the current season's earlier regular-season weeks. Playoff residuals are not in the window.

The estimator is the sample standard deviation, divisor n − 1. If fewer than two residuals are finite, that week is a model failure. Do not use a median absolute deviation.

## Scores

Primary scores use integer outcomes. The grid is every integer from −80 to 80. Mass outside the grid is piled on the endpoints.

The sim probability of integer k is the share of the frozen `N_SIMS` draws whose margin, or total, rounds to k, after that pile-up. v5's probability of integer k is Φ((k + 0.5 − mean) / sigma) − Φ((k − 0.5 − mean) / sigma), with the two endpoints replaced by the corresponding tail so the masses sum to 1.

Discrete CRPS, the ranked probability score, is

sum over k from −80 through 79 of (F(k) − 1{actual ≤ k})²

where F is the cumulative sum of that integer probability. This is the primary CRPS for both models.

Home-win probability is the probability of a strictly positive margin: the sim's share of draws with margin > 0, and v5's summed integer mass on k ≥ 1. The Brier outcome is 1 when `home_score > away_score` and 0 otherwise, including ties. Both models use that pair. MAE uses the sim's mean margin or mean total against the v5 mean, then the absolute error against the actual.

The sensitivity check, which cannot change the decision, is continuous CRPS for v5 (`crps_normal` of the same mean and sigma) and `crps_ensemble` on the sim's raw draws.

## Primary call

The single primary metric is margin CRPS, sim minus v5, on the 2,534-game regular-season set that excludes 2018.

d is that difference per game. The mean's 90% interval is the Webb wild cluster bootstrap in `webb_mean_interval`: clusters are `(season, week)`, weights are −√1.5, −1, −√0.5, √0.5, 1, √1.5, each with probability 1/6 (the last draw absorbs the floating-point remainder so the six probabilities sum to 1), 999 replicates, and the generator is `numpy.random.default_rng(20261115)`. The 90% bounds are the 5th and 95th percentiles. Each analysis set draws its own weights from that seed. The per-game sim generator is separate: `default_rng(sim_config.SEED + int(sha256(game_id).hexdigest()[:6], 16))`, with `SEED` the frozen 20180907.

SUCCESS if the mean d is negative and the 90% upper bound is negative. FAILURE if the 90% lower bound is positive. Otherwise INCONCLUSIVE.

The with-2018 regular-season result is a sensitivity check. It cannot flip the call. Playoffs cannot flip the call.

## Everything else

Secondary, on the same 2,534 games, and unable to flip the call: total CRPS, margin MAE, total MAE, and home-win Brier, each as sim minus v5. They are one Holm family. The note is printed with their intervals. A Holm adjustment is not a second decision.

Also descriptive, and outside that family: the continuous-CRPS sensitivity, the with-2018 table, playoffs, the pace table, the tracked metrics, and the disagreement flags.

## Yards-per-play ablation

The decision set is the same 2,534 regular-season games: 2016, 2017, 2019, 2020, 2021, 2022, 2023, 2024, 2025, and the 151 completed 2026 games. 2018 cannot choose the layer. Playoffs cannot.

d is yards-per-play on minus off. Keep the layer only if the mean margin-CRPS d is negative, its 90% upper bound is negative, and the total-CRPS d has a 90% upper bound of +0.05 or lower. Otherwise the primary comparison uses the layer off. The other setting is still reported. The bounds are not revised after they are seen.

The primary comparison uses the setting this rule selects.

## Pace

Once per season and once on the primary set, report plays per team, plays per game, drives per team, and seconds per play, each as MAE and bias against the naive baseline from the 2018 dev report. Also report 80% and 90% play-count coverage, and the total-CRPS gap of predicted pace minus the game-average pace oracle. That oracle is a handicapped benchmark. A gap whose 90% interval covers zero is not a win for the predicted pace.

## Tracked metrics

Report, and do not promote:

- share of games with absolute final margin 3
- share of games with absolute final margin 7
- regulation-tie rate
- overtime-entry rate
- seconds-per-play bias
- the margin-at-5:00-left series

The series is one number per game: the mean, across sims, of home score minus away score at the first simulated state with 300 or fewer seconds left in the second half. It is a column on the per-game file. It is not a scoring target.

## Disagreement flags

A game is flagged when the absolute gap between the sim mean and the v5 mean is at least:

- 3 points of margin
- 4 points of total
- 10 percentage points of home-win probability

Report each flag rate. If a rate lands far outside 10–15%, report that. Do not change 3, 4, or 10.

## Sample size and the minimum detectable effect

The primary sample is the 2,534 completed regular-season games above. That count is from the schedule. It does not use a model outcome.

The spread of d was not computed, because that would be a sim-minus-v5 result. The proxy is the committed column `crps_margin` on the 256 regular-season rows of `outputs/p2/dev_2018_games.csv`. That column is the sim's own ensemble margin CRPS from the 2018 dev run. Its sample standard deviation, divisor n − 1, is 6.052997400188693.

Assume the unseen v5 per-game margin CRPS has the same spread, and assume the two CRPS series are uncorrelated. A positive correlation would shrink the spread of the difference, so zero correlation is the conservative choice. Then

sd(d) = 6.052997400188693 × √2 = 8.560231016355935

On those 256 games and 17 weeks, the cluster-robust standard error of the mean of `crps_margin`, with the G/(G−1) correction, is 0.4482344746416253 against an iid standard error of 0.37831233751179333. The design effect is 1.4038137842450578. Apply that design effect to the 2,534-game primary set:

SE = √(1.4038137842450578 × 8.560231016355935² / 2534) = 0.2014823145343824

The normal 90% critical value is 1.6448536269514722. The minimum detectable |mean d| is 1.6448536269514722 × 0.2014823145343824 = 0.3314089158284562 points of margin CRPS. This is a normal approximation to the Webb interval, not a bootstrap of d.

## Wording

P3 is not a fair ATS or key-number comparison against v5 until a fourth-quarter endgame model is accepted on a later season. The results say that in the first paragraph. Do not describe the primary table as an ATS test or a key-number test.

## Files

When QA has locked this document and a run is allowed, write:

- `backend/research/nfl_drive_sim/outputs/p3/p3_report.md`
- `backend/research/nfl_drive_sim/outputs/p3/per_game_d.csv`, one row per scored game and yards-per-play setting, with the d columns, the three flags, the 5:00 margin, season, week, kickoff ET, a regular-season flag, and a 2018 dev-touched flag
- `backend/research/nfl_drive_sim/outputs/p3/manifest.sha256` covering those two files

## Runtime

Stay on the frozen `N_SIMS` and the per-game seed above. Run the yards-per-play layer on and off, and nothing else. No grid and no season outside the cache.
