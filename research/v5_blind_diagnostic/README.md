# v5-blind diagnostic

Market-blind qb-ridge-v5 against the v6-blind design from PR #32, on the same 2,639 regular-season games, 2016–2025. This branch does not change a serving path, a production model, or PR #32.

## Definition

v5-blind uses an allowlist. The fit may contain only `epa_d`, `pts_d`, `st_d`, `qb_d`, and `hfa`. A column outside that list, including `mkt_d` or `spread_line`, raises before the regression.

`qb_d` is the home-minus-away QB rating delta for the depth-chart starter 24 hours before kickoff:

- 2014–2024: that week's depth-chart QB order, replaced only when an injury row with `date_modified` at or before kickoff minus 24 hours says Out.
- 2025: the latest depth-chart snapshot whose timestamp is at or before kickoff minus 24 hours. Those injury files have no `date_modified`, so they are not applied.
- Kickoff clock is read as UTC.
- A missing chart contributes 0 on that side. It does not fall back to last week's starter, and it does not use `home_qb_id` or `away_qb_id`.

Coefficients are refit by least squares on every game type in seasons at least 2015 and before the test season. Features for a week come from games strictly before that week. The closing line grades ATS and is not a design column.

The full v5 row below is the PR #32 reference: the same ridges, the market prior `mkt_d`, and the QB who actually played. v6 is the shipped `prod/v6-blind-main` file `backtest/harness_model/priors/week_features.csv` with the published `by_season` margin coefficients. Its columns are `pass_comp`, `rush_comp`, `sr_comp`, and `hfa`. `pass_comp` already adds the starter delta to the pass ridge, and one coefficient prices both.

Margin CRPS uses a normal with sigma 13.5. Differences are paired bootstrap means, 10,000 resamples, seed 20261026, a fresh generator for each metric. A positive CRPS difference means the first model is worse.

On the 2,639 scored games, 2,638 have a T-24h starter on both sides. The T-24h starter matches the QB who played on both sides in 2,179 games. The depth chart assigned a starter on 6,599 of 6,600 team-games in the build, with 171 cases where an eligible Out report moved the start off QB1.

## Head-to-head

| Model | CRPS | MAE | ATS vs the close |
| --- | ---: | ---: | --- |
| v5, market prior and actual starter | 7.232400 | 10.023255 | 1291/2574 = 0.501554 |
| v5-blind, T-24h starter, allowlist | 7.313068 | 10.106648 | 1284/2574 = 0.498834 |
| v6-blind, shipped coefficients | 7.397671 | 10.208046 | 1301/2574 = 0.505439 |

| Contrast | CRPS | MAE | ATS |
| --- | --- | --- | --- |
| v6-blind minus v5-blind | +0.084603 [0.028125, 0.141440] | +0.101398 [0.003134, 0.198308] | +0.006605 [−0.014375, 0.027584] |
| v5-blind minus v5 | +0.080668 [0.041792, 0.120351] | +0.083394 [0.016730, 0.151343] | −0.002720 [−0.019814, 0.013986] |

n = 2,639 for CRPS and MAE. ATS drops pushes and pick-em ties, n = 2,574. Intervals are 95 percent paired bootstrap intervals.

QA's review of PR #32 reported that this blind v5 beats v6-blind by 0.088 CRPS with interval [0.031, 0.145]. This run does not reproduce that triple. The gap on the same games and the same seed is 0.084603 [0.028125, 0.141440]. The interval excludes zero, so v5-blind is better than shipped v6-blind on CRPS. The point and both ends of the interval differ from the reported figures in the third decimal. The starter rule was left as specified after the result was in hand.

One scored side has no chart. Filling only that side with last week's starter changes v6-minus-v5 CRPS from 0.084603 to 0.083967. That sensitivity is not v5-blind.

Full v5, which uses the market prior and the QB who played, remains better than v5-blind by 0.080668 CRPS [0.041792, 0.120351]. That row is the reference from PR #32. Its CRPS, MAE, and ATS match the published v5 line exactly, and the shipped v6 line matches as well.

## Ablations

Each row drops one piece and refits the remaining columns on the same walk-forward. Delta is the reduced model minus the full model of that family. A positive delta means the reduced model is worse. Because the remaining coefficients are refit, a small delta means the other columns absorb the dropped one. It is not a claim that the column is idle inside the full additive fit. The group rows are diagnostics. They are not candidate models, and nothing here was selected or retuned.

v5-blind ablation baseline is the blind refit, CRPS 7.313068.

| Dropped from v5-blind | CRPS | Delta CRPS | Delta MAE |
| --- | ---: | --- | --- |
| epa_d | 7.302309 | −0.010759 [−0.026422, 0.004663] | −0.011220 [−0.038966, 0.017321] |
| pts_d | 7.330986 | +0.017918 [−0.001446, 0.037472] | +0.020502 [−0.014328, 0.055273] |
| st_d | 7.319545 | +0.006477 [−0.007823, 0.020799] | +0.013224 [−0.011038, 0.037995] |
| qb_d (T-24h) | 7.338406 | +0.025338 [0.001440, 0.048814] | +0.047061 [0.006191, 0.087658] |
| hfa | 7.368379 | +0.055311 [0.015090, 0.095833] | +0.085928 [0.016854, 0.156953] |
| epa_d, pts_d, and st_d together | 7.920984 | +0.607916 [0.489851, 0.727468] | +0.841451 [0.654674, 1.029699] |

v6 ablation baseline is an unrounded refit of the shipped columns, CRPS 7.398805. The head-to-head uses the published coefficients, CRPS 7.397671. The 0.001 difference is the published three-decimal rounding. Ablation deltas are against the refit.

| Dropped from v6-blind | CRPS | Delta CRPS | Delta MAE |
| --- | ---: | --- | --- |
| pass_comp (pass ridge and starter delta) | 7.422273 | +0.023468 [−0.009100, 0.056215] | +0.046530 [−0.010963, 0.102620] |
| rush_comp | 7.396138 | −0.002667 [−0.013726, 0.008415] | −0.015212 [−0.034833, 0.004711] |
| sr_comp | 7.434349 | +0.035543 [0.004585, 0.065930] | +0.066474 [0.013610, 0.118790] |
| hfa | 7.455817 | +0.057012 [0.017148, 0.096885] | +0.093613 [0.024642, 0.163275] |
| pass, rush, and success together | 7.951649 | +0.552843 [0.438385, 0.667244] | +0.794382 [0.611908, 0.977636] |

A one-configuration rebuild with the frozen v6 hyperparameters does not reproduce the shipped features on these 2,639 games (pass correlation 0.9922, MAE 0.0180; rush 0.9858, MAE 0.0111; success 0.9845, MAE 0.0050). The starter delta stays inside shipped `pass_comp` and is not given its own ablation.

## What drives the gap

v5-blind's advantage is the opponent-adjusted team block. Removing `epa_d`, `pts_d`, and `st_d` together costs 0.608 CRPS [0.490, 0.727]. v6's pass, rush, and success block costs 0.553 CRPS [0.438, 0.667] inside v6. Home field costs about 0.055 CRPS in v5-blind and 0.057 in v6, so it does not account for the difference between the models.

Inside v5-blind, the T-24h QB delta is the non-market term whose own interval excludes zero: +0.025 CRPS [0.001, 0.049]. Points, special teams, and EPA do not separate cleanly once the others are refit. EPA's single-column interval includes zero and leans slightly toward a better score without it. That is absorption by the points ridge, and the group drop shows the block is the model. The allowlist stays intact. v5-blind is the NFL blind base for new features.

Inside v6, success rate is the team term whose own interval excludes zero (+0.036 CRPS). The pass column, which bundles the team pass ridge and the starter delta, has an interval that includes zero after the refit. Rush does too. Those single-column results describe redundancy with what remains. They do not split the QB out of the shipped file.

Rest, injuries beyond the T-24h Out override, weather, the context layer, the live blend, and prop or ADP anchors are absent from both walk-forward designs. They are not the source of this gap.

`results.json` holds the full floats, coefficients, and starter audit. `per_game.csv` holds one row per scored game.
