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

## Time zone

Every cutoff is `America/New_York`. A naive timestamp is an Eastern wall clock, the same reading as `scores_service._parse_kickoff`. An aware timestamp is converted to Eastern before the date is read. A UTC instant that is still December 31 in New York is before the cutoff. `game_type` is required on every row. Allowed values are `REG`, `WC`, `DIV`, `CON`, and `SB`.

`sd(d)` is descriptive. Before any candidate is fit, add one preregistered structured shift of +1 point to the base prediction and take the sample standard deviation of `CRPS(base + 1) - CRPS(base)`. It is not a pure-noise placebo and it is not estimated from a candidate. Reported power is 80 percent. An earlier draft used `beta = 0.5`, which zeros the power quantile. That footnote is not the reported MDE.

Inference is a wild cluster bootstrap. Clusters are `(season, week)`. Weights are Webb's six-point distribution, each with probability 1/6: `-sqrt(1.5), -1, -sqrt(0.5), sqrt(0.5), 1, sqrt(1.5)`. Draws: 10,000. Seed: `20261115`. The documented alternative, not the gate, is a cluster-robust t with `df = C - 1`.

## Stage A — exploratory screen

Runs first, only after this file is the QA lock. Label: exploratory. The seasons were touched.

Eligible kickoffs are those strictly before `2027-01-01` America/New_York, from the 2015 season through December 2026. January 2027 games are excluded. For test season `S`, stage-2 is refit for the base and for the candidate on seasons `2015 <= season < S`. The market column stays out. The 2015 fold has no earlier season inside that floor, so 2015 is training history for 2016 and is graded only when earlier rows exist.

Paired difference: `d = CRPS(candidate) - CRPS(base)` on the feature's endpoint.

A feature continues only when both of the following hold. There is no retune after a miss.

1. `mean(d) < 0`, and every walk-forward coefficient on the new column matches the preregistered sign.
2. The one-sided wild-cluster 90% upper bound of `mean(d)` is `< 0`. That bound is the 90th percentile of the unrestricted wild-cluster replicates.

Anything else is dropped. A dropped feature does not enter stage B or stage C.

On the same pre-candidate base replay, report `sd(d)` from the +1 point shift and the descriptive 80% MDE ` (z_{0.025/3} + z_{0.80}) * sd(d) / sqrt(n) `. It does not decide retention.

## Stage B — January 2027 harm check

Games whose NFL season is 2026 and whose Eastern kickoff falls in January 2027. Membership is that rule, not a frozen list of game ids. The expectation, counting a full week-17/18 January slate plus the wild-card, divisional, and conference rounds, is about 43 games in 5 season-week clusters. The Super Bowl is in February and is not in this check. Week 18 rest and tanking remain a known bias inside the window: teams sit starters and teams out of the seeding race tank, which moves pressure, pace, and who is listed at quarterback and on the line.

Each survivor is frozen on kickoffs before `2027-01-01`. January outcomes are not in that fit.

Harm passes when the wild-cluster 90% upper bound of `mean(d)` is `<= +0.25` CRPS points. A pass means no detectable harm. It is not a benefit claim. A miss stops the feature. The feature does not continue, and it is not retuned.

## Stage C — 2027 regular season

`game_type == REG`, weeks 1 through 18. Expect about 272 games and 18 week clusters. One look, at the end of the season, and only for features that passed A and B.

The base and the candidate are refit on every kickoff strictly before the first 2027 regular-season kickoff. That fit may include the January 2027 games and Super Bowl LXI. It may not include a 2027 regular-season game. Hyperparameters and the three feature definitions stay the ones in this lock.

Holm is one-sided across the `k` features that reached this stage. Familywise alpha is 0.025. The tightest threshold is `0.025 / k`. The p-value is the wild-cluster bootstrap under the null that `mean(d) = 0`.

The closing line is a grading column only. For a margin feature it is `spread_line` (positive means the home team is favored). The candidate bets the side of its margin relative to that line. For the total feature it is `total_line`, with the same cover rule. Pushes are out. The contrast is cover rate minus 0.5. Its 90% lower bound is the 5th percentile of the unrestricted wild-cluster replicates. The guard requires that lower bound `>= -0.02` (minus 2.0 percentage points).

A feature ships only when Holm rejects it and the ATS guard holds and the coefficient sign still matches. A miss is `INCONCLUSIVE`. It does not ship.

Honest 80% MDE, at the expected 272 games and at the pre-result family `k = 3`:

`MDE = (Φ^{-1}(1 - 0.025/3) + Φ^{-1}(0.80)) * sd(d) / sqrt(272)`

`Φ^{-1}(1 - 0.025/3) = 2.3939798012415405`. `Φ^{-1}(0.80) = 0.8416212335729143`. At `k = 3` and `n = 272` the multiple is `0.19618712978541816`, so `MDE = 0.19618712978541816 * sd(d)`. If fewer than three features reach this stage, recompute with `0.025/k`. The iid multiple is descriptive. The gate is the wild cluster bootstrap, and 18 week clusters are the dependence unit.

## Walk-forward stage-2

For each exploratory test season, and once for each later frozen origin:

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

## What this commit does not do

It does not fit stage-2 on real games. It does not measure `sd(d)` on a real replay. It does not open scores. It does not write a results file. Tests use synthetic frames only. PFR pressure that arrives as a season table, with no game id or no positive week, raises and stops. Average time to throw and the quarterback ids used for a QB-change check have to be timestamped strictly before kickoff.
