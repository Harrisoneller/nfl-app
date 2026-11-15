# Single-feature tests — procedure lock

Status: phase 1. This file is the shared procedure for the three feature preregistrations. It is not permission to fit or score real games. This commit does not download play-by-play, does not open outcome files, and does not estimate a coefficient. Publishing stays off. `qb-ridge-v5` stays the live model. Nothing here merges, deploys, or changes Railway.

A change to a feature definition, the allowlist, the windows, sigma, the MDE formula, or the Holm rule after a real fit is a new preregistration. It does not edit this file after a grade.

The three features, and only these three, are the confirmatory family:

| Feature | File | Endpoint | Column | Expected coefficient sign |
| --- | --- | --- | --- | --- |
| Pass rush vs pass protection | `01-pressure-interaction.md` | home margin | `pressure_x` | negative |
| OL starter availability | `02-ol-starter-availability.md` | home margin | `ol_d` | negative |
| Combined neutral pace | `03-combined-neutral-pace.md` | total points | `neutral_pace_sum` | negative |

Home margin is `home_score - away_score`. The total is `home_score + away_score`. Ties stay in the sample. Pushes are not an endpoint.

## Market blind

Spreads, totals, moneylines, odds, and prediction-market prices are not inputs, priors, blend weights, or fit targets. A design column whose name contains a market token (`spread`, `moneyline`, `odds`, `price`, `kalshi`, `mkt`, `market`, `opener`, `closing_line`, `total_line`, `implied`, and the rest of the list in `specs.MARKET_NAME_PARTS`) raises before the regression. The guard is an allowlist plus that name check.

Market columns may be joined after the projection exists, for a grade that is not this protocol's gate. They never enter `X`.

## Base model inventory

The base is market-blind `qb-ridge-v5` (`backend/app/services/team_ratings_v5.py`, `MODEL_VERSION = "qb-ridge-v5"`). The live service is not edited by this branch.

What the ridge fits, from games strictly before the week: EPA/play, pass EPA/play, points/game, special-teams EPA/game, and plays/game. Scrimmage snaps only. Kneels and spikes are dropped. Snaps with win probability outside 10–90% are weighted 0.35. Fourth-quarter snaps with a margin of 17 or more are weighted 0.20. EPA is winsorized at ±4.5.

Pass EPA uses the nflverse `pass` flag, so sacks are included. Scrambles are rushes (`pass == 0`) and are not in the pass metric. The pass rating is exposed as `pass_d` / `pass_s`. It is not a term in the shipped margin or total equation. Pass-play EPA still enters the margin and the total through `epa_d` and `epa_s`, and it enters the QB term.

Plays/game is the unweighted scrimmage snap count `n`. It enters the total as `plays_s`. It is not neutral seconds per play. The v4 neutral-pace adjustment is not on the v5 path.

The QB term is on. `qb_d` is on the margin and `qb_s` is on the total. A QB rating is opponent-adjusted pass EPA per pass play, half-life 40 weeks, shrunk with 250 pseudo-dropbacks toward a prior that moves from replacement level (−0.10) at no volume to the league average at 600 dropbacks.

The market prior is off for this study. Live v5 has `mkt_d`, fit from previous games' closing spreads, with shipped weight 0.669. That column is not in the allowlist. `market_ratings` is not called. The no-market column set is the base. The numeric shipped weights are not used, including `MARGIN_COEFS`, `MARGIN_COEFS_NO_MKT`, and `TOTAL_COEFS`.

Margin allowlist, no intercept: `epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`.

Total allowlist, plus an intercept: `pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`.

`plays_s` stays when neutral pace is added. `pass_d` stays out of both equations. Hyperparameters stay at `DEFAULT_PARAMS` (half-life 10 weeks, season carry 0.60, ridge 5 pseudo-games on per-play metrics and 8 on per-game metrics, three-season window). They are not searched.

## Seasons already touched

No season from 2013 through 2026 is a clean confirmatory sample. QA ruled this. The code agrees.

| Seasons | What touched them | Where |
| --- | --- | --- |
| Through 2012 | Different regime. Out of this study. | QA ruling. v5's loader can start at 2013 (`backtest_v5.py --first`). |
| 2013–2014 | History only. Not a grade. | `backtest_v5.py` defaults `--first 2013`, `--start 2014`. QA ruling. |
| 2015 through 2026 week 3 | v5 stage-2 OLS, the fit behind the shipped coefficient tables. | `team_ratings_v5.py` stage-2 comment, `docs/MODEL_V5.md`, `param_registry.py` v5 category, commit `432197e`. |
| 2016–2020 | v5 hyperparameter validation. | `DEFAULT_PARAMS` comment and `docs/MODEL_V5.md`. |
| 2021–2026 | v5 holdout was inspected. The code comment calls it a holdout. QA ruled it is not untouched. | `docs/MODEL_V5.md`. Reported card is 2021–26. |
| 2015 | First v6 training season. | `backtest_v6_blind.py` on `origin/prod/v6-blind-main`, commit `5914855`. Not on this branch. |
| 2016–2023 | v6-blind grid and walk-forward selection, margin CRPS. | Same script. Selection loop `range(2016, 2024)`. |
| 2024–2025 | v6-blind holdout, scored once. | Same script. |
| 2026 weeks 1–6 | v6-blind live check. | QA ruling. The script scores season 2026 in the live-check loop. |

`backtest_v5.py` prints margin and total RMSE, correlation with the close, slope, ATS, and a realization beta. It does not compute CRPS or MAE. Closed-form normal CRPS lives in `prediction_dist.crps_normal`. The board backtest (`backtest_service._distribution_eval`) scores the shipped margin, market prior included when the week model built one, with the registry sigma. That is not this protocol.

## Samples

The old windows are dropped: history 2018–20, selection 2021–22, final 2023–25, and the 2026-forward fallback.

Exploratory screen: regular-season games in 2016 through 2026, except the confirmatory kickoffs below. When `game_type` is present, only `REG` rows are graded. Training rows are every complete game the caller supplies with `2015 <= season < S`, playoffs included when the caller included them, which is how `backtest_v5.py` builds the stage-2 sample. A grade on 2015–2026 outside the January 2027 kickoffs is this screen and nothing else. 2015 is the first training season for the 2016 fold. It is not graded, because a 2015 test has no prior season inside the training floor. 2013–2014 may be feature history. They are not stage-2 rows. Seasons through 2012 raise if they appear on a stage-2 frame, and the feature builders drop them. The 2027 regular season is not a sample in this study.

Confirmatory sample, the only one: 2026-season NFL games whose kickoff falls in January 2027 in `America/New_York`. This is the owner's definition of early 2027: "2026-season bowls and playoff games played in January 2027." It is not the 2027 regular season, and it is not weeks 1–4 of that season. One look, the whole window together. Games inside the window do not train each other. The confirmatory fit uses seasons `2015 <= season < 2026` only.

Kickoff rule, same as `scores_service._parse_kickoff`: nflverse `gameday` plus `gametime` is an Eastern wall clock. A missing `gametime` would be 1:00 PM Eastern. An aware timestamp is converted to Eastern before the month is read. A UTC instant that is still December 31 in New York is outside the window. The 2027 regular season is outside it even if someone labels the kickoff January.

## Lock-time schedule census

Counted on 2026-11-15 from nflverse `schedules/games.csv` (`nflverse_stats.SCHEDULES_URL`). Columns read: `game_id`, `season`, `game_type`, `week`, `gameday`, `gametime`. No score, spread, total, or result column was opened.

The 2026 file has 272 rows, all `game_type == REG`. It has no wild-card, divisional, conference, or Super Bowl rows. Those matchups are not on the schedule yet, so they are not in this count. "Bowls" are not a row type in the file. The January games that exist are regular-season weeks 17 and 18.

| Week | Eastern kickoff | Games | Cluster |
| --- | --- | --- | --- |
| 17 | 2027-01-03 (14 games) and 2027-01-04 (1 game) | 15 | `(2026, 17)` |
| 18 | 2027-01-10 | 16 | `(2026, 18)` |

One other week-17 game kicks off 2026-12-31 and is outside the window. Expected `n = 31`. Season-week clusters `C = 2`. Monday's game is in the week-17 cluster, not a third cluster.

That `n` is the scheduled count at lock time. It is the largest n this window can have until a new preregistration adds rows. A missing feature can only make the graded n smaller.

## Power at that n

`sd(d)` is still measured on the base replay before any candidate is fit. It is not known in this commit, and it is not filled in from outcomes.

With `z_(1-beta) = 0`:

`MDE = z_alpha* * sd(d) / sqrt(31)`

`z_alpha* / sqrt(31) = 2.3939798012415405 / sqrt(31) = 0.42997146524798546`

So `MDE = 0.42997146524798546 * sd(d)`.

The formula is the iid one above. It is not a cluster-robust standard error. The confirmatory gate still uses the season-week cluster bootstrap. With two clusters, a resample is a draw of those two weeks with replacement. The bootstrap distribution of the mean has a handful of shapes, and the 97.5th percentile is a coarse function of two week means. Holm's tightest threshold is `0.025 / 3`. A feature has to clear that multiplicity bar, a strict upper bound below 0, and the sign check, on 31 games that are really two weeks.

This window is very likely too small to pass any feature under Holm at `0.025 / 3` with cluster-bootstrap percentiles. The published multiple is already 0.43 of the per-game CRPS standard deviation at the most favorable n. Clustering makes the effective sample the two weeks, not the 31 games. That statement is about the design. It is not a grade, and no feature has been fit.

## Week 18

Sixteen of the 31 games are week 18. Week 18 is the last regular-season week. Teams rest starters and teams out of the seeding race tank. That shifts who plays, how fast the game is, and how often the quarterback is pressured. The OL feature is directly exposed, because the point-in-time starter can be a backup by design. The pressure interaction and the neutral-pace sum move with a non-competitive script. This is a known bias. The window is not trimmed to drop week 18. A pass, if one ever occurred, would still carry that contamination.

## Walk-forward stage-2

For each exploratory test season, and once for the January 2027 block:

1. Fit the base by ordinary least squares on the training rows. Margin has no intercept. Total has an intercept.
2. Store the base per-game CRPS. This is the replay.
3. Fit the candidate on the same rows. The candidate is the base allowlist plus the one new column. Every base coefficient is refit. The base prediction is not held fixed and then nudged by a single slope.
4. Score the test rows. Do not refit them.

Rows need a finite endpoint, finite base columns, and a finite feature. A missing feature is dropped, not filled. The same rows train both fits. A test-season outcome does not enter that season's fit.

The shipped coefficient vectors are not a comparison and not a fallback.

## CRPS, MAE, and sigma

Per-game CRPS is the closed-form normal score, the same formula as `prediction_dist.crps_normal`. The harness implements it because `backtest_v5.py` does not.

Sigma is frozen and not tuned:

- Margin: 13.5 (`NFL_MARGIN_SIGMA`).
- Total: 10 (`NFL_TOTAL_SIGMA`).

`MARGIN_RESID_SD` (12.89) is not used. It comes from the contaminated fit. The admin registry sigmas are not used.

Paired difference, on each graded game:

`delta = CRPS(candidate) - CRPS(base)`

Negative means the candidate is better.

MAE is the mean absolute error of each prediction. It is reported. It is not a gate.

## Exploratory MDE

Before any candidate coefficient is fit, measure `sd(d)` on the frozen market-blind walk-forward replay.

`d_g` is that replay's per-game base CRPS on the games that will be graded for the feature (finite feature, finite base, finite endpoint). `sd(d)` is the sample standard deviation with divisor `n - 1`. It is not re-estimated from the candidate's paired differences. Those differences do not exist until the candidate is fit.

`n` is the number of those games.

Holm's tightest one-sided alpha is `alpha* = 0.025 / 3`.

`beta = 0.5`, so `z_(1-beta) = Φ^{-1}(0.5) = 0`.

`z_alpha* = Φ^{-1}(1 - 0.025/3) = 2.3939798012415405`, from `prediction_dist.norm_ppf`.

`MDE = (z_alpha* + z_(1-beta)) * sd(d) / sqrt(n)`.

The screen bar is `mean(delta) <= -MDE`. Clearing it is an exploratory label. It does not add, drop, or redefine a feature. All three features stay in the January 2027 Holm family either way. The screen is not a ship gate. The January kickoffs are not part of the screen.

`beta = 0.5` is locked here so the formula has a number. It is the power floor from the prior selection bar. `z_(1-beta)` was not given a different power in the QA ruling. A different beta before the first real replay is a correction of this lock. A different beta after a fit is a new preregistration.

## Confirmatory gate

One call scores all three features on the January 2027 kickoffs. The base replays for all three are fit, and each feature's ship MDE is stored, before any candidate coefficient is fit.

Ship MDE for a feature uses the same formula, with `sd(d)` and `n` from the base replay on that feature's January rows. It is reported. It is not the pass rule. The lock-time multiple above uses `n = 31` and does not depend on that replay.

The pass rule, for each feature, is all three of:

1. Holm step-down across the three one-sided p-values, familywise alpha 0.025. Sorted ascending, ties broken by feature name. Rank `i` of `m = 3` uses threshold `0.025 / (m - i + 1)`. The first miss stops the rest.
2. The 97.5th percentile of the season-week cluster bootstrap mean of `delta` is strictly `< 0`. `numpy.quantile(..., 0.975, method="linear")`. 10,000 draws. Seed `20261115`. Clusters are `(season, week)`. A drawn cluster contributes every game in it, and a cluster drawn twice contributes them twice. At least two distinct season-weeks are required.
3. The candidate coefficient on the new column is strictly on the preregistered side. Zero fails.

The one-sided bootstrap p for `mean(delta) < 0` is `(1 + #{bootstrap means >= 0}) / (10000 + 1)`.

A negative upper bound with a failed Holm step is not a pass. MAE is not a pass. The exploratory screen is not a pass.

## What this commit does not do

It does not fit stage-2 on real games. It does not measure `sd(d)` on a real replay. The schedule census did not open a score, a line, or a result. It does not write a results file. Tests use synthetic frames only.
