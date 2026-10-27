# Feature-bundle preregistration

This amendment is the holdout contract for one market-blind feature bundle on the frozen v6-blind core (`cursor/nfl-v6-blind-59d5`, PR #26). It replaces the single 2015–23 freeze and the 2024–25-only bar. QA has not locked it.

The nested path has not been run. No bundle metric has been computed for any season. No holdout grade in this file was looked at. Publishing stays off. Picks stay off. Any new flag defaults off. This branch is not deployed.

The 2024–25 numbers in [Prior views](#prior-views-of-2024-25) are grades already published by v6, #27, and #28. They are not a grade of this bundle.

## Market blind

Spreads, totals, moneylines, prices, opens, and closes are grading columns. They are not model inputs, priors, seeds, blend weights, or fit targets. Stage-2 least squares is fit to the actual home margin and the actual total points. Sigma stays 13.5 for the margin and 10 for the total. Neither sigma is tuned. ATS, CLV, and the closing line do not enter the loss that picks a grid cell.

The harness stays `backtest/harness_model/model.py`. `predict(hist, target)` returns one row per target row, same order, columns `game_id`, `pred_margin`, `pred_total`, `pred_home_score`, `pred_away_score`. `LA`, `OAK`, `SD`, and `STL` still map to the current franchise before the prior join. A target row with no prior is logged and raised. The row-count check stays. Grading columns may sit on the frame and are not read. A prior file that grows a market column name fails the existing banned-token check.

## Spec

v6 hyperparameters that this bundle does not re-open: pass shrinkage k = 8 games, rush k = 10, success k = 12, defense weight = 0.55, QB shrinkage k = 400 dropbacks, preseason keep = 2/3, continuity = 0.5 · roster continuity + 0.5 · week-1 starter’s share of last year’s dropbacks. The starter rule is unchanged (T−24h depth chart, injury override only when `date_modified` is at or before kickoff minus 24h and the report says Out). `home_qb_id` is not an input.

### Garbage time

A scrimmage play is kept only when win probability is inside [0.10, 0.90]. Plays outside that band, plays with missing win probability, kneels, spikes, and special-teams plays are dropped. This is a hard filter. The v5 down-weight of 0.35 is not used. The band is not searched.

### Recency

Weights are `0.5 ** (age / half_life)`. `age` is the number of that team’s earlier games between the historical game and the game being predicted. A bye is not a game. The half-life grid is exactly `{6, 7, 8}` games. v6’s frozen half-life of 14 weeks is not in the grid.

### Opponent adjustment, early downs and passing downs

Defense is two ratings per metric. Offense stays one rating per metric. The ridge is

`y = ic + off_i + def_early_j · early + def_pass_j · passing + hfa · home`

with the same k and the same 0.55 defense weight as v6 for that metric. Down family is fixed:

- Early down: 1st down, or 2nd down with yards to go ≤ 7.
- Passing down: 2nd down with yards to go ≥ 8, or 3rd or 4th down with yards to go ≥ 5.
- A 3rd or 4th down with yards to go ≤ 4 is in neither family and does not enter that metric’s ridge.

For a future game the two defenses are combined with the offense’s own prior-only share of early-down plays among plays that fell in either family, decayed with the same half-life. A team with no such plays uses the league share from earlier seasons only. The current game’s play mix is not used.

### Explosiveness, separate from success rate

Success rate remains the nflverse `success` flag. It keeps k = 12.

An explosive play is a scrimmage play with `yards_gained` ≥ 20. That is the same cut `analytics_service` already uses for explosive rate. The cut is not searched. Explosive rate is its own opponent-adjusted ridge, with the early/passing defense split above.

Explosiveness is shrunk 2× or 3× harder than success. The multiplier grid is exactly `{2, 3}`, so the explosiveness k is 24 or 36 games. No other multiplier is eligible.

### Turnover prior

Realized turnovers throughout. The rate is interceptions plus lost fumbles, per scrimmage play, for and against (`analytics_service` turnover counts). It is opponent-adjusted with the same early/passing split and with success’s k of 12. It is not searched.

FTN charting is not used. Pressure rate and turnover-worthy plays are not used. There is no 2022 definition change and no fallback between two turnover units.

### Out of this bundle

Drive-finishing features are absent. Luck regression is absent. The QB-transition ablation (#27) and the wind-on-totals ablation (#28) are absent. Rest, lookahead, revenge, primetime, wind, and offensive-line features are absent.

## Hyperparameter grid

Six cells. Everything else stays at the v6 values above.

| Half-life (games) | Explosiveness multiplier | Explosiveness k (games) |
| --- | --- | --- |
| 6 | 2 | 24 |
| 6 | 3 | 36 |
| 7 | 2 | 24 |
| 7 | 3 | 36 |
| 8 | 2 | 24 |
| 8 | 3 | 36 |

No single half-life is frozen before the holdout. The cell is chosen inside the nested path below. A tie within 1e-6 goes to the higher explosiveness multiplier, then to the longer half-life.

## Holdout: 2016–25 nested walk-forward

One path. For each season S from 2016 through 2025, in order:

1. Pick one grid cell by margin CRPS, sigma 13.5, using only seasons strictly before S.
2. Fit that cell’s stage-2 coefficients on scored games in seasons strictly before S.
3. Grade season S out of sample. Do not refit S. Do not change S’s cell after seeing S.

Every grade is regular season only (`game_type == 'REG'`). Playoffs are excluded from all grading: the inner CRPS that picks the cell, the primary CRPS, ATS, CLV, and every other reported grade. The regular-season sample is n = 2,639.

The inner score that picks the cell is itself walk-forward. For each of the six cells, mean per-game margin CRPS is computed on test seasons T with 2016 ≤ T < S, and season T is fit only on seasons before T. The cell with the lowest mean is the one used to grade S. Cells are not averaged. Season S is not in that mean.

When S is 2016 there is no inner test season. The cell is then the one with the lowest in-sample margin CRPS on 2015, the only scored season before 2016. 2014 play-by-play may inform that 2015 fit. 2014 is not graded. This is the only edge case. It is part of the same path, not a second analysis.

2026 is not on this path.

The six cells are compared only on seasons before the season being graded. Each holdout season is graded once, with one cell. That nested rule is the multiplicity control. A table that picks the cell by its grade on S, or that reports a second path with one cell frozen on 2015–23, is not this contract.

The loss that picks a cell is margin CRPS only. ATS, CLV, the opener, and the close do not pick the cell.

## Betting gate

One threshold. There is no grid on `t`. v6’s search over edge cutoffs `{1, 2, 3}` is not used.

`t` is frozen at **3.316** points.

How that number was fixed: on scored games with season in 2015–2023, residual = actual home margin − stored v6-blind predicted margin. `t` = (sample standard deviation of those residuals, divisor n−1) / 4, recorded to three decimals. The divisor 4 is part of the rule. It was not chosen by scanning ATS. The join of `backtest/harness_model/priors/predictions.csv` to `backend/tests/fixtures/v6_blind_design.csv` had 2,458 games, seasons 2015 through 2023 only. Sample SD = 13.265843. `t` = 3.316461, recorded as 3.316. Rows with season ≥ 2024 were removed before that join. 2015’s stored prediction uses the frozen 2015–23 coefficient, so that season is in-sample in the harness file. 2016–2023 in that file are walk-forward.

Prior-seasons-only disclosure: the same SD/4 rule, computed from prior seasons only, falls in **3.24–3.35**. That range is a disclosure. It is not a grid, and it does not replace 3.316. 3.316 lies inside the range.

A play is a game with `|pred_margin − line| > 3.316`. Pushes (`result` equal to the line under `numpy.isclose`) and predictions equal to the line are not plays. The line is the grading line for the metric being scored. It is not a model input.

The primary CRPS comparison uses every nested out-of-sample game. The gate does not shrink that sample. ATS is counted on plays, after the gate.

## Opener source

Single book: **ESPN BET**. DraftKings is not used. No other book is mixed in.

Window: 2023 week 7 through 2025. QA’s count of ESPN BET opener rows on that window is n = 643, before the gate and before the stale drop. This amendment does not re-count them.

Sign matches v6: positive means the home team is favored, the same direction as home margin. The close is nflverse `spread_line`. The open is the ESPN BET number on that sign. A home bet’s CLV is `close − open`. An away bet’s CLV is `open − close`.

Rows whose open equals the close under `numpy.isclose` are stale. They are dropped from every opener metric and listed so QA can audit them. They are not filled in.

Opener plays are counted after that drop and after the gate. QA’s count of those plays is about **113**, which is under 400. That is why ATS versus the close is a primary bar and the opener metrics are directional.

The opener window overlaps 2023. Those weeks are inside the 2016–25 nested holdout. Opener results are not used to pick a cell.

`DATABASE_URL` is unset here, and this checkout has no opener file. nflverse `schedules/games.csv` supplies the closing `spread_line` and `total_line` only. `odds_lines.bookmaker` and `odds_snapshots.book` are The Odds API captures, not a historical ESPN BET open.

## Prior views of 2024–25

These seasons have already been graded by three published looks. Those looks are not a grade of this bundle. They are why a failed path does not authorize a retune, and why a re-attempt needs a fresh preregistration.

### v6-blind, PR #26

Clean holdout, frozen 2015–23 coefficients, every scored game against the close. Source: `docs/MODEL_V6_BLIND.md` and the PR #26 body.

| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | Totals CRPS | Close totals CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 570 | 0.499 | 0.458–0.540 | 7.325 | 7.028 | 13.075 | 12.485 | 7.577 | 7.311 |

Core bar: all three items fail (CRPS within 2% of the close, RMSE within close + 0.3, ATS ≥ 0.50). Opener n = 0 in that snapshot. CLV was unavailable.

### #27, QB-transition

Draft PR, branch `research/nfl-qb-transitions`. One look on knowable games. Verdict: fail. PREREG commit `d86efb9b84300ef914bc079afa1fa6db186e4138` predates the eval. The harness-fix re-run did not retune.

| Check | Result |
| --- | --- |
| ATS | 0.489 on 462 plays |
| Bootstrap 90% CI | 0.450–0.526 |
| CLV | unavailable, opener n = 0 |
| Margin CRPS | 7.236 vs v6 7.237 (close 6.976) |
| One-sided binomial p | 0.696 |

Transition games only: n = 45, ATS 0.489, margin CRPS 8.659 vs v6 8.674 vs close 7.506. Unknowable scored games excluded: 104. The diagnostic grade of frozen v6 on all 570 games matches the v6 table above.

### #28, wind on the total

Draft PR, branch `research/nfl-wind-totals`. Pre-registration commit `5bd63d2fc4ea979b1f18bbed21038bb4ff6364d0`. Holdout score `dfde322d034e44a84399c4f36a705bb2aa174b39`. Verdict: fail. The slope was not refit.

| Check | Result |
| --- | --- |
| Totals ATS | 0.504 (285/566) |
| Plays | 566 |
| Bootstrap 90% CI | 0.470–0.539 |
| CLV | unavailable, opener n = 0 |
| Totals CRPS | 7.560 vs v6 7.577 vs close 7.311 |
| p | 0.450 |

Windy subset, not the gate: 79 games, ATS 0.468 (37/79), bootstrap 0.380–0.557, totals CRPS 7.091 vs v6 7.211 vs close 6.789.

## Pass bar

One path, after QA locks this file and releases the holdout. The concatenation of the out-of-sample seasons 2016 through 2025 is the only grade. A fail ships nothing. The grid, `t`, the down-family cuts, the explosive-yard cut, and the realized-turnover prior are not changed after that grade. A re-attempt needs a fresh preregistration.

v6’s paired number is the stored harness prediction for that game. This bundle’s number is the nested out-of-sample prediction. Sigma for margin CRPS is 13.5 for both.

### Primary: margin CRPS

Every nested out-of-sample regular-season game (`game_type == 'REG'`). Playoffs are excluded. The gate does not drop games from this comparison.

Paired difference `d = CRPS(v6) − CRPS(bundle)` on each game.

- One-sided paired t-test on `d`. The alternative is mean(`d`) > 0. Pass when p < 0.025.
- Paired bootstrap of the mean of `d`. 10,000 resamples of games, seed `20261026`. The true 95% one-sided lower bound, matching p < 0.025, is the 2.5th percentile of those 10,000 bootstrap means (`numpy.percentile`, 2.5). Pass when that bound is > 0.

Both must pass. The t-test threshold and the bootstrap bound are both required as written. They are not collapsed into one calculation.

### Primary: ATS versus the close

In force because opener plays after the gate are about 113, under 400.

Plays are nested 2016–25 regular-season games (`game_type == 'REG'`) with `|pred_margin − spread_line| > 3.316`, pushes removed. Playoffs are excluded. On those plays, both of the following:

- Bundle ATS is at least 1 percentage point above v6-blind ATS on the same plays (`bundle ATS − v6 ATS ≥ 0.01`).
- One-sided paired test, p < 0.025. The test is an exact one-sided binomial on the games where exactly one model covers the close. The null is that each model is equally likely to be the one that covers. The alternative is that the bundle is the one that covers more often.

### Close non-inferiority

Same plays as the close ATS bar. Paired difference `c = cover_bundle − cover_v6` on each play (1 if that model covers, 0 otherwise). Bootstrap the mean of `c`, 10,000 resamples, seed `20261026`, 5th and 95th percentiles. The 90% interval’s lower bound must be ≥ −0.01. That is within 1 percentage point of v6 on the downside. A better ATS does not fail this check.

### Directional: opener ATS and CLV

ESPN BET only, 2023 week 7 through 2025, stale open-equals-close rows dropped, then the gate. About 113 plays. These two numbers are reported with intervals and do not pass or fail the bundle:

- ATS versus the opener, bundle and v6, on the same plays, with a bootstrap 90% interval on the paired cover difference (seed `20261026`, 5th and 95th percentiles).
- Mean CLV (opener to close), with a bootstrap 90% interval on the game-level CLV (seed `20261026`, 5th and 95th percentiles).

### Power

Stated before any bundle grade, so the bars are read against what this sample can detect.

- Margin CRPS: minimum detectable effect about **0.055 ×** the standard deviation of the paired per-game CRPS difference, at n = **2,639** regular-season games.
- ATS versus the close: minimum detectable effect about **±2.7** percentage points.
- Opener subset, directional only: about 113 plays gives a minimum detectable effect of about **±13** percentage points of ATS and about **0.47** points of CLV. These are not bars.

The +1 percentage point close bar is smaller than that ATS minimum detectable effect. The bar is unchanged by that fact.

### What a pass requires

The CRPS t-test passes, the CRPS bootstrap bound passes, the close ATS +1 percentage point test passes, and the close non-inferiority bound passes. Opener ATS and CLV are reported beside that decision.

## Lock rule

No peeking at holdout grades before QA locks this file. The nested path is the holdout, including 2016–23, so a bundle CRPS, ATS, or CLV on any of those seasons is a peek. A later re-attempt, including a change to the grid, to `t`, or to the bars, needs a new preregistration. It does not edit this one after a grade.

## This amendment

- Nested holdout is 2016–25, one path. The cell for season S is chosen and fit only on seasons before S.
- Primary CRPS is a one-sided paired t-test at p < 0.025 and a paired bootstrap 95% lower bound > 0. Seed `20261026`.
- Gate `t` = 3.316, frozen. Prior-seasons-only range disclosed as 3.24–3.35.
- Opener plays after the gate are about 113, so ATS versus the close at +1 percentage point over v6, p < 0.025, is a primary bar. Close non-inferiority at 1 percentage point is required. Opener ATS and CLV are directional.
- DraftKings is out. ESPN BET only. Open-equals-close rows are dropped.
- FTN is out. The realized-turnover prior is used in every season. The explosiveness split stays.
- No bundle metric was computed for any season in this amendment.
