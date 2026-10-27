# Success, explosiveness, and havoc preregistration

This is the contract for one market-blind feature set on the v5-blind base. QA has not locked it. The 2024–25 holdout has not been fit or scored. No success, explosiveness, or havoc coefficient has been estimated. Publishing stays off. This branch is not deployed.

A failed go/no-go does not authorize a retune. Any re-attempt, including a change to a feature definition, the allowlist, the walk-forward, or a bar, needs a fresh preregistration. It does not edit this file after a grade.

## Prior views of 2024–25

These seasons have already been graded by other looks. Those looks are not a grade of this feature set.

- PR #29, the feature bundle on v6-blind, nested 2016–25, one look, result fail. Locked prereg `2e5e4bb`.
- PR #32, shipped v6-blind against qb-ridge-v5 on the 2016–25 regular season.
- PR #33, v5-blind (T−24h starter, no market term) against that same v6-blind on the same games.

PR #34 preregistered rest and travel on this same base, ran 2016–23 selection only, and returned NO-SHIP. It did not fit or score 2024–25. That selection is not a 2024–25 look and it is not a grade of this feature set.

## Market blind

Spreads, totals, moneylines, prices, opens, and closes are grading columns. They are not model inputs, priors, seeds, blend weights, or fit targets. The design is fit by least squares to the actual home margin. Margin sigma stays 13.5. It is not tuned. ATS and the closing line do not enter the fit.

## Shared definitions

Success rate and explosiveness are taken from `features/nfl/defs.py` on `features/nfl-defs` at commit `b68470fa18119e7b443c13cdf2278bcadebfd686`. The file is not edited. `build_features` supplies the team-game ratings. The constants it freezes are not searched: win-probability band [0.10, 0.90], explosive yards 20, success and explosive shrinkage k = 12, defense weight 0.55, preseason keep 2/3, ridge epsilon 1e-4. The feature bundle’s 2× or 3× explosiveness search is not used.

A team-game rating is as of the start of that week. It uses earlier weeks of that season only, plus two-thirds of the previous season’s end-of-season rating. The current game’s plays are not in the rating. Plays with missing win probability, plays outside the band, kneels, spikes, and special-teams plays are out, which is the module’s scrimmage filter. `season_type` outside `REG` and `POST` is out.

`sr_off_adj` and `sr_def_adj` are the module’s success ridge. Success is the nflverse `success` flag. Explosive plays are not removed from that flag. `xpl_off_adj` and `xpl_def_adj` are the separate explosive ridge: share of those scrimmage plays with `yards_gained` >= 20. On both ridges a higher defensive rating means more of that event allowed to the opposing offense.

## Havoc

Havoc is not a column in `defs.py`. It uses the same play filter, the same `ridge_team` equation, and the same as-of-week panel as success (`_ridge_panel` in that file), with k = 12 (`K_SUCCESS`). That k is not searched.

On each scrimmage play kept by the module, the offense suffered a havoc event if any of the following is true. The play counts once:

- `sack` equals 1
- `tackled_for_loss` equals 1
- `fumble_forced` equals 1
- `interception` equals 1
- `pass_defense_1_player_id` or `pass_defense_2_player_id` names a player, using the module’s `_player_present`

The team-game rate is that count divided by the number of scrimmage plays in the same team-game. The ridge outcome is that rate. A higher offensive rating means the offense suffered more havoc. A higher defensive rating means opponents suffered more havoc.

Pressure rate is not in the nflverse play-by-play schema used here. No column name contains `pressure` or `hurry`. `qb_hit` is present and is not substituted. No other havoc component is added.

## Features

The margin model has no intercept beyond these columns. Each new term is one matchup differential. `DEF_WEIGHT` is the module constant 0.55.

`edge(off, def) = (off_home − off_away) + 0.55 · (def_away − def_home)`

| Column | Definition |
| --- | --- |
| `sr_d` | `edge(sr_off_adj, sr_def_adj)` |
| `xpl_d` | `edge(xpl_off_adj, xpl_def_adj)` |
| `havoc_d` | `edge(havoc_off, havoc_def)` from the havoc ridge above |

The least-squares coefficient on each column is free. The sign of `havoc_d` is not flipped after the selection result. Features are not standardized. Last-four rates, percentiles, early-down splits, passing-down splits, pass-only and rush-only rates, and the luck columns in `defs.py` are not in the model.

A team-game missing from `build_features` is not filled. Both teams must be present. A week whose ridge history is empty keeps the module’s prior, including a zero prior when there is no previous season.

Play-by-play used to build ratings for the selection comes from seasons 2014 through 2023 only. `play_by_play_2024` and `play_by_play_2025` are not read.

## Allowlist

The augmented design may contain only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`, `sr_d`, `xpl_d`, `havoc_d`

v5-blind, the baseline, may contain only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`

A duplicate column, or any column outside the list being fit, raises before the regression. `mkt_d`, `spread_line`, moneylines, and totals are outside both lists. The guard is an allowlist. It is not a blocklist.

`qb_d` is the home-minus-away QB rating delta for the depth-chart starter 24 hours before kickoff, the same rule as PR #33:

- 2014–2024: that week’s depth-chart QB order, overridden only when an injury row with `date_modified` at or before kickoff minus 24 hours has report status Out.
- 2025: the latest depth-chart snapshot with `dt` at or before kickoff minus 24 hours. Those injury files have no `date_modified` and are not applied.
- Kickoff for that rule is compared as UTC, matching the v6 starter builder.
- A missing chart is 0 on that side. It is not last week’s starter and it is not `home_qb_id` or `away_qb_id`.

`epa_d`, `pts_d`, and `st_d` are the main qb-ridge-v5 team ridges from games strictly before the week. `hfa` is 1 when `location` is not Neutral, else 0.

The live v5 reference, reported and not part of the go/no-go, adds `mkt_d` and replaces `qb_d` with `qb_d_actual` from `home_qb_id` and `away_qb_id`. That reference is outside the blind allowlist on purpose. Its columns are exactly `mkt_d`, `epa_d`, `pts_d`, `st_d`, `qb_d_actual`, `hfa`.

## Walk-forward

For each test season S:

1. Fit ordinary least squares on played games with season at least 2015 and strictly before S, every game type, rows with finite design columns and a finite home margin.
2. There is no intercept argument. Home field is the `hfa` column.
3. Predict season S. Do not refit S.

v5-blind and the augmented model are fit on the same seasons, each on its own columns. The same games are compared. Training may include the playoffs. Grading is regular season only (`game_type == REG`). The as-of ratings for a game in season S may use earlier seasons and earlier weeks of S. They may not use week S or a later season.

## Selection, 2016–23

This is the only analysis run before QA locks the holdout. Test seasons are 2016 through 2023. The paired sample is regular-season games in those seasons with a finite result, a finite closing `spread_line`, and finite predictions from both blind models.

Paired difference on each game:

`d = CRPS(v5-blind) − CRPS(augmented)`

Positive `d` means the augmented model has the lower CRPS. CRPS is the closed-form normal score with sigma 13.5.

- `n` is the number of those games.
- `mean` is the sample mean of `d`.
- `SD` is the sample standard deviation of `d`, divisor `n − 1`.
- Paired `z = mean / (SD / sqrt(n))`. This is the paired t statistic. The threshold below is on this `z`, not on a p-value.

## Go / no-go

The holdout may be run only after QA locks this file, and only when both of the following are true on the 2016–23 selection:

1. Paired `z >= 2`.
2. Projected holdout power `>= 0.50`.

Otherwise the verdict is NO-SHIP. The 2024–25 holdout stays unspent. This preregistration is not edited into a second try.

Holdout size `n_h` is the count of 2024 and 2025 regular-season games with a finite score and a finite `spread_line`. That count is a sample-size input. It is not a grade. The selection script must not fit, predict, or score any game with season 2024 or later.

Projected power uses the selection `mean` and `SD`, and a normal approximation. The critical value is `Φ⁻¹(0.975) = 1.959963984540054`.

`se_h = SD / sqrt(n_h)`

`power = 1 − Φ(1.959963984540054 − mean / se_h)`

`Φ` is the standard normal cdf. If `SD` is 0, power is 1 when `mean > 0` and 0 otherwise.

MDE is the smallest positive mean CRPS gap the holdout detects at 80 percent power, one-sided, at this alpha:

`MDE = (1.959963984540054 + 0.8416212335729143) × SD / sqrt(n_h)`

`0.8416212335729143` is `Φ⁻¹(0.80)`.

The selection report states `n`, `mean`, paired `z`, `SD`, `n_h`, `MDE`, `power`, and the verdict. It may also state 2016–23 CRPS levels for the augmented model, v5-blind, and live v5. Those levels do not change the verdict. The verdict uses only `z` and `power`.

## Holdout, 2024–25, not run in this pass

Run only if the verdict was GO and QA has locked this file. One look. Seasons 2024 and 2025, regular season, same walk-forward, same paired `d`.

### Primary

Both of the following, on the holdout games:

- One-sided paired t-test of `d`. The alternative is `mean(d) > 0`. Pass when `p < 0.025`. Degrees of freedom `n − 1`. The statistic is the same `z` formula as selection.
- Paired bootstrap of the mean of `d`. 10,000 resamples of games, seed `20261026`, a new `numpy.random.default_rng(20261026)` for this contrast. Pass when the 2.5th percentile, `numpy.quantile(means, 0.025)`, is `> 0`.

Both must pass. The comparison that gates is against v5-blind, not against live v5.

Also report, and do not gate on, the same paired CRPS difference and the same t and bootstrap against live v5: `CRPS(live v5) − CRPS(augmented)`. Positive means the augmented model has the lower CRPS.

### Secondary, directional

These do not pass or fail the feature set.

Paired ATS versus the close, augmented minus v5-blind, on holdout games that are not pushes and not pick-em ties. A push is `result` equal to `spread_line` under `numpy.isclose`. The side the model likes is the side where its margin differs from the close. Home covers when `result > spread_line`. The close is nflverse `spread_line`, positive when the home team is favored.

Bootstrap the paired cover difference, 10,000 resamples, seed `20261026`, a new generator. The 90 percent interval is the 5th and 95th percentiles, `numpy.quantile` at 0.05 and 0.95.

Non-inferiority within 1 percentage point: the lower end of that 90 percent interval is `>= −0.01`.

`n < 400` rule: `n` here is the number of decided ATS games. If that `n` is under 400, the ATS interval and the non-inferiority comparison are marked underpowered. They stay directional either way. ATS does not become a primary bar.

### Openers

Single book: ESPN BET, and only where an opener row exists for that holdout game. No other book is substituted. This checkout has no historical ESPN BET open file. If none is supplied with the holdout, opener ATS and CLV are not estimated.

Sign matches the close: positive means the home team is favored. A home bet’s CLV is `close − open`. An away bet’s CLV is `open − close`. Rows whose open equals the close under `numpy.isclose` are stale and are dropped from opener metrics. Opener ATS and mean CLV are directional, each with a bootstrap 90 percent interval, seed `20261026`. They do not gate.

## What this pass will not do

The selection script scores no game from 2024 or 2025. It does not choose a subset of the allowlist after seeing `z`. It does not add a column that is not in the table above. It does not search k, the explosive-yard cut, the win-probability band, or the defense weight.
