# Feature-bundle results

QA lock: commit `2e5e4bb082699fe7d703e63a65d2c1513b8f2374`. `PREREG.md` SHA-256 `8ccea247c2f9fd32a254aa2f7023a2a0d9143cf9f0c33b67cdf4589f1ff88b25`, unchanged. One nested path. No retune after this grade.

**Verdict: FAIL.** Margin CRPS fails. ATS versus the close fails. Close non-inferiority passes. The ESPN BET opener is blank, so opener ATS and CLV are not estimated. A fail ships nothing. Picks stay off. Publishing stays off.

## v6-blind baseline parity

The frozen v6 path (half-life 14 weeks, k = 8/10/12, defense weight 0.55, QB k = 400) was rebuilt from the pinned public release and compared with the stored harness files. The starter audit matches the published v6 report: 6,599 of 6,600 assigned, 1 missing chart, 171 injury overrides of QB1, 1,114 dated depth assignments.

On the 2,458 stored feature rows, seasons 2015–2023:

| Feature | MAE | Max abs | Correlation |
| --- | ---: | ---: | ---: |
| pass_comp | 0.016211 | 0.126652 | 0.992872 |
| rush_comp | 0.009516 | 0.081856 | 0.988068 |
| sr_comp | 0.004480 | 0.031252 | 0.985982 |
| hfa | 0 | 0 | 1 |
| pass_sum | 0.015319 | 0.121788 | 0.993438 |
| rush_sum | 0.008057 | 0.072238 | 0.988915 |
| sr_sum | 0.004052 | 0.029950 | 0.987532 |

Stored coefficients applied to the rebuilt features, same 2,458 games: margin MAE 0.542639, max 3.617127, correlation 0.988911 (absolute-error quantiles 0.362 / 1.314 / 2.485 at the 50th / 90th / 99th). Total MAE 0.170230, max 1.476313, correlation 0.993428.

Regular-season margin CRPS, 2016–2023, n = 2,095, sigma 13.5: rebuilt 7.449804, stored 7.423774, gap +0.026030. All game types in that window, n = 2,191: rebuilt 7.418162, stored 7.393927, gap +0.024234. The stored all-types figure matches the published selection CRPS 7.3939.

The pre-stated material-failure rule was a correlation below 0.95 or a CRPS gap above 0.10. The lowest feature correlation is 0.985982 and the CRPS gap is 0.026030, so the baseline check passed and the nested path was run. The gap is public-release drift against the v6 snapshot (readme data SHA `539b6fb9`). It is a drift report, and the paired v6 number in the bars below is the stored harness prediction.

## Nested path

Cell chosen for each season S by margin CRPS on seasons before S. Inner score is walk-forward for S > 2016. For 2016 the score is in-sample 2015. Tie within 1e-6 would take the higher explosiveness multiplier, then the longer half-life. No tie fired. The cell was not changed after seeing S.

| Season | Cell | Inner margin CRPS | Graded REG games |
| --- | --- | ---: | ---: |
| 2016 | 6×2 | 7.334206 | 256 |
| 2017 | 6×2 | 7.014564 | 256 |
| 2018 | 6×3 | 7.451645 | 256 |
| 2019 | 6×3 | 7.467598 | 256 |
| 2020 | 6×3 | 7.539499 | 256 |
| 2021 | 6×3 | 7.488620 | 272 |
| 2022 | 8×3 | 7.609571 | 271 |
| 2023 | 8×3 | 7.454408 | 272 |
| 2024 | 8×3 | 7.486208 | 272 |
| 2025 | 8×3 | 7.475057 | 272 |

Graded sample: 2,639 regular-season games, `game_type == REG`, seasons 2016–2025. Every row joins a stored v6 prediction. 2026 is absent. Playoff games are absent from the inner CRPS, the primary CRPS, and ATS. Postseason plays remain in the rating history, so the preseason prior still includes them. The searched half-life applies to team-unit weights. The QB decay stays on v6’s week index at half-life 14, with k = 400. Realized turnovers only (interceptions plus lost fumbles). Gate `t` = 3.316.

## Primary bars

Sigma for every margin CRPS is 13.5. The CSV `sigma` column is that margin sigma. The total sigma stays 10 and is not in the loss. Bootstrap seed `20261026`, 10,000 resamples, `numpy.percentile` linear. The draw is on `per_game.csv` in its committed order (season, week, game_id). The same CRPS draw on the frame before that sort was −0.096765. Both bounds are below zero.

### Margin CRPS — FAIL

Paired `d = CRPS(v6) − CRPS(bundle)`. Pass requires a one-sided paired t-test (`mean(d) > 0`) with p < 0.025 and a 2.5th-percentile bootstrap bound > 0.

| | |
| --- | ---: |
| n | 2,639 |
| Mean CRPS, bundle | 7.456378 |
| Mean CRPS, stored v6 | 7.397671 |
| Mean d | −0.058707 |
| SD of d (n−1) | 0.980872 |
| t statistic | −3.074680 |
| One-sided p | 0.998936 |
| Bootstrap 2.5th percentile of mean(d) | −0.096473 |

The bundle’s mean CRPS is higher than the stored v6 mean. The t-test p is 0.998936. The 2.5th percentile is −0.096473. Both pieces fail.

### ATS versus the close — FAIL

Plays are the 2,639 games with `|bundle pred_margin − spread_line| > 3.316`, after dropping pushes (`numpy.isclose`) and games where either prediction is equal to the line. Both models are scored on that same set. Pass requires bundle ATS − stored-v6 ATS ≥ 0.01 and an exact one-sided binomial test on discordant games (null 0.5, bundle covers more often) with p < 0.025.

| | |
| --- | ---: |
| Plays | 973 |
| Bundle covers | 501 / 973 = 0.514902 |
| Stored v6 covers | 500 / 973 = 0.513875 |
| Difference | 0.001028 |
| Discordant games | 17 |
| Bundle only | 9 |
| v6 only | 8 |
| Binomial p | 0.500 |

The ATS gap is 0.10 percentage points, short of 1. The discordant p-value is 0.500. Both pieces fail.

### Close non-inferiority — PASS

Same 973 plays. `c = cover_bundle − cover_v6`. The 90% bootstrap interval is the 5th and 95th percentiles of the mean. Pass requires the lower bound ≥ −0.01.

| | |
| --- | ---: |
| 5th percentile | −0.006166 |
| 95th percentile | 0.008222 |

The lower bound is −0.006166, which is above −0.01. This bar passes. It does not rescue the two bars that fail.

## Opener (directional)

ESPN BET is the only book in the contract. `games.csv` has no ESPN BET open, and the `espn_data` release is QBR rather than lines. `espn_bet_open` is blank on all 2,639 rows. Opener rows after the stale drop: 0. Opener plays after the gate: 0. Opener ATS and mean CLV are not estimated. That is a missing public column, and it is not a pass/fail bar. The preregistered directional MDE at about 113 plays is about ±13 percentage points of ATS and about 0.47 points of CLV.

## Per-game file

`backtest/feature_bundle/per_game.csv`

SHA-256 `064d80ad1765f8d805e6a81cfa6c812d1f86fba7e729ade1fce29199f631fda3`

2,639 rows. Columns: `game_id`, `season`, `week`, `game_type`, `cell`, `pred_margin_v6`, `pred_total_v6`, `pred_margin_bundle`, `pred_total_bundle`, `sigma`, `close_spread`, `close_total`, `espn_bet_open`, `actual_margin`, `actual_total`, `crps_v6`, `crps_bundle`. The close is nflverse `spread_line` / `total_line`, positive when the home team is favored. The cell is the one chosen for that season (`6x2` is half-life 6, explosiveness multiplier 2).

`backtest/feature_bundle/bars.json` holds the same bar numbers and the six-cell inner scores.

## Inputs

Release pins and file SHA-256s are in `DATA_MANIFEST.md`. Market columns on the schedule were withheld from the feature path and used only here, for the close. 2014 play-by-play informs the 2015 prior and is not graded.
