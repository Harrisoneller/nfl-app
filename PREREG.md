# Feature-bundle preregistration

This commit locks the protocol for one market-blind feature bundle on the frozen v6-blind core (`cursor/nfl-v6-blind-59d5`, PR #26). It is the holdout contract. The 2015–23 walk-forward that freezes the half-life and the explosiveness multiplier has not been run. No game from 2024 or later has been scored. The holdout stays sealed until QA releases it.

Publishing stays off. Picks stay off. Any new flag defaults off. This branch is not deployed.

Nothing below is a 2024, 2025, or 2026 model grade. The 2024–25 numbers in [Prior views](#prior-views-of-2024-25) are the grades already published by v6, #27, and #28.

## Market blind

Spreads, totals, moneylines, prices, opens, and closes are grading columns. They are not model inputs, priors, seeds, blend weights, or fit targets. Stage-2 least squares is fit to the actual home margin and the actual total points. Sigma stays 13.5 for the margin and 10 for the total. Neither sigma is tuned. ATS, CLV, and the closing line do not enter the loss.

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

The fallback prior is the realized turnover rate already in the repo: interceptions plus lost fumbles, per scrimmage play, for and against (`analytics_service` turnover counts). It is opponent-adjusted with the same early/passing split and with success’s k of 12. It is not searched.

Where FTN charting covers the season, that realized rate is replaced by the charted turnover-worthy rate. The nflverse FTN dictionary (`dictionary_ftn_charting`, read 2026-10-27) says the `season` field is available from 2022 onwards. The turnover-worthy field in that dictionary is `is_interception_worthy` (FTN `intw`): the share of charted dropbacks marked interception-worthy. The dictionary has no fumble-worthy field and no pressure boolean. `n_pass_rushers`, `n_blitzers`, and `is_qb_fault_sack` are not treated as pressure. Pressure is not imputed. A season before 2022, or a 2022+ season whose charting join is missing, keeps the realized-turnover prior.

FTN coverage locked from that dictionary: 2022 onwards. This commit did not download the charting files and did not count plays.

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

## Selection on 2015–23

The loss is walk-forward margin CRPS, sigma 13.5.

- Test seasons are 2016, 2017, 2018, 2019, 2020, 2021, 2022, and 2023.
- A test season’s coefficients are fit only on earlier seasons.
- 2015 is the first training season. It is not a test season. 2014 play-by-play may inform the 2015 prior. It is not scored.
- The reported selection score is the mean of the per-game CRPS values, not the mean of the seasonal means.
- A tie within 1e-6 goes to the higher explosiveness multiplier, then to the longer half-life.
- After that choice is frozen, one fit on all scored 2015–23 games produces the coefficient that would later be applied to a holdout. That fit is not a holdout grade.
- No game with season ≥ 2024 enters the loss, the fit, a coefficient, or a printed metric.

The chosen half-life and multiplier are blank until that run is committed. They are not chosen in this file.

| Choice | Value |
| --- | --- |
| Half-life | not yet run |
| Explosiveness multiplier | not yet run |
| Selection CRPS | not yet run |

## Betting gate

One threshold. There is no grid on `t`. v6’s search over edge cutoffs `{1, 2, 3}` is not used.

Rule, fixed before the number below was read off: on scored games with season in 2015–2023, residual = actual home margin − stored v6-blind predicted margin. `t` = (sample standard deviation of those residuals, divisor n−1) / 4, recorded to three decimal places. The divisor 4 is part of the rule. It was not chosen by scanning ATS.

The stored predictions are `backtest/harness_model/priors/predictions.csv`. Actual margins are `backend/tests/fixtures/v6_blind_design.csv`, joined through `week_features.csv` on the blind feature columns. Rows with season ≥ 2024 were removed before the join. The join has 2,458 games, seasons 2015 through 2023 only, maximum season 2023. Sample SD = 13.265843. `t` = 3.316461, recorded as **3.316** points.

A play is a game with `|pred_margin − line| > 3.316`. Pushes (`result` equal to the line under `numpy.isclose`) and predictions equal to the line are not plays. The line in that comparison is the grading line for the metric being scored (the opener, or the close). It is not a model input.

2015’s stored prediction uses the frozen 2015–23 coefficient, so that season is in-sample in the harness file. 2016–2023 are walk-forward. The same SD on 2016–2023 alone (n = 2,191) is 13.253380, which would record 3.313. The locked value is 3.316, from the 2015–23 rule. The 2016–23 figure is disclosed so the in-sample 2015 year is visible. It is not a second candidate.

The primary CRPS comparison uses every holdout game. The gate does not shrink that sample. ATS and CLV are counted on plays.

## Opener source

Single book: **ESPN BET**.

QA’s count of ESPN BET opener rows on 2023 week 7 through 2025 is **n = 643**. This commit does not re-count them. `DATABASE_URL` is unset in this environment, `NFLVERSE_DIR` is unset, and this checkout has no opener file. nflverse `schedules/games.csv` supplies the closing `spread_line` and `total_line` only. `odds_lines.bookmaker` and `odds_snapshots.book` are The Odds API captures, not a historical ESPN BET open. 2026 DraftKings opens stay out, as v6 already treats them as unreliable.

Sign matches v6: positive means the home team is favored, the same direction as home margin. The close used for CLV is nflverse `spread_line`. The open is the ESPN BET number on that sign. A home bet’s CLV is `close − open`. An away bet’s CLV is `open − close`.

Stale rows are those whose open equals the close under `numpy.isclose`. They are flagged for QA, dropped from ATS, and dropped from CLV. They are not filled in.

Graded opener-subset `n` is the count after that drop, plus any DraftKings row that passes the check below. QA’s 643 is the pre-drop ESPN BET count. 643 is at least 400, so the opener bars are the ones in force at this count. If the graded `n` after the stale drop (and any admitted DraftKings rows) is under 400, the close bar below replaces them. This commit does not apply the drop and does not recompute `n`.

### DraftKings look-ahead check

Pre-specified. Not run in this commit. No DraftKings row is in the 643.

The bettable window is the T2–T4 span in `sparky_service._snapshot_label`: from 72 hours before kickoff through and after kickoff. Kickoff is `gameday` plus `gametime`, parsed as UTC, the same parse as v6.

A DraftKings spread may be mixed in for a game that has no ESPN BET open only when both of these hold:

1. The open timestamp is present and strictly earlier than the bettable window (more than 72 hours before kickoff).
2. The open is not equal to the close.

A row that fails either check stays out of the grade and is listed for QA. Passing the check does not reopen the half-life, the multiplier, or `t`.

## Prior views of 2024–25

These seasons have already been graded by three published looks. This bundle does not add a fourth look in this commit. The history is the reason a later fail does not authorize a retune.

### v6-blind, PR #26

Clean holdout, frozen 2015–23 coefficients, every scored game against the close. Source: `docs/MODEL_V6_BLIND.md` and the PR #26 body.

| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | Totals CRPS | Close totals CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 570 | 0.499 | 0.458–0.540 | 7.325 | 7.028 | 13.075 | 12.485 | 7.577 | 7.311 |

Core bar: all three items fail (CRPS within 2% of the close, RMSE within close + 0.3, ATS ≥ 0.50). Opener n = 0. CLV unavailable. No opener file was in that snapshot.

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

One look, after QA releases the holdout. A fail ships nothing. The half-life, the explosiveness multiplier, `t`, the down-family cuts, the explosive-yard cut, and the FTN fallback are not refit. 2026 is not part of the bar and is not scored to choose a feature.

### Primary

2024–25 scored games, every game, margin CRPS against v6-blind on the same games. Sigma 13.5 for both.

Paired difference `d = CRPS(v6) − CRPS(bundle)` on each game. Bootstrap 10,000 resamples of games, seed `20241027`, 5th and 95th percentiles. The bundle passes this gate only when the 90% interval lies entirely above 0.

### Opener metrics

Graded only on the 2023 week 7–2025 subset defined above. QA’s ESPN BET count is n = 643.

Both of the following, and neither estimate negative:

- ATS versus the opener at least 1 percentage point above v6-blind ATS on the same plays (`bundle ATS − v6 ATS ≥ 0.01`), and bundle ATS ≥ 0.50.
- Mean CLV (opener to close) with a bootstrap 90% interval, 10,000 resamples of the game-level CLV, seed `20241027`, 5th and 95th percentiles. The lower bound must be above 0. The point estimate must be above 0.

### If the graded opener-subset n is under 400

The two opener metrics are not the bar. In their place, ATS versus the close on the 2024–25 plays must be at least 1 percentage point above v6-blind, and a one-sided paired test must have p < 0.025. The test is an exact one-sided binomial on the games where exactly one model covers the close. The null is that each model is equally likely to be the one that covers. The alternative is that the bundle is the one that covers more often. Bundle ATS must also be ≥ 0.50. At QA’s pre-drop count of 643 this replacement is not in force.

### What a pass requires

The primary CRPS interval passes, and the opener pair passes (or, only when graded opener n < 400, the close ATS test passes). A negative ATS edge or a negative mean CLV fails the bundle even if another number clears.

## This commit

- Protocol locked. Betting-gate `t` = 3.316.
- Opener book locked as ESPN BET. QA count n = 643 on 2023 week 7–2025. DraftKings check specified and not run.
- FTN charting coverage locked as 2022 onwards from the nflverse dictionary. Pressure is not a field in that dictionary and is not imputed.
- Half-life and explosiveness multiplier not selected.
- No game from 2024 or later scored. No 2024+ row entered the residual calculation.
