# Totals preregistration

Status: locked

This document is a draft for QA to lock. It does not score any game. No sim-minus-v5 total, CRPS, or MAE is computed here. `freeze_week.py` aborts until the line above says `Status: locked`.

The simulator stays on the P2 freeze. `sim_config.py` is not edited. Its sha256 stays `f60cd566e06e87f66d000f8ec8f3afc4bd4e3b8ed91711374c005fe0e3be7ecb`. The yards-per-play layer is off for every projection and every score in this protocol. That is a call-time flag. It is not a change to `sim_config.py`, which still stores the frozen default.

## Primary metric

One metric. d is total-points CRPS, frozen sim minus the walk-forward no-market v5 replay, one number per game.

Score each game by the ranked probability score on integer totals. The grid is every integer from −80 to 80. Mass outside the grid is piled on the endpoints. The sim probability of integer k is the share of the 10,000 draws whose total rounds to k, after that pile-up. v5's probability of integer k is the Normal discretized to integers: Φ((k + 0.5 − mean) / sigma) − Φ((k − 0.5 − mean) / sigma), with the two endpoints replaced by the corresponding tail so the masses sum to 1. The score is

sum over k from −80 through 79 of (F(k) − 1{actual ≤ k})²

where F is the cumulative sum of that integer probability. The actual total is `home_score + away_score` after the game is complete. This is the same integer RPS used for totals in the locked P3 scoring definition. No P3 total result is an input, a threshold, or a reason for a window.

v5 is the no-market path in `v5_replay.py`. Sigma is the sample standard deviation, divisor n − 1, of walk-forward total residuals from completed regular-season games whose kickoff is strictly before that week's cutoff. It is not 10 and not 13.5. A week with fewer than two finite residuals is a model failure. The total design is `pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`, and a column of ones. Stage-2 is ordinary least squares, `numpy.linalg.lstsq` with `rcond=None`, on completed regular-season games in that pre-cutoff window, and only when that window has at least 32 games.

The sim uses `N_SIMS = 10000`. The per-game generator is `numpy.random.default_rng(sim_config.SEED + int(sha256(game_id).hexdigest()[:6], 16))` with `SEED` the frozen 20180907. The yards-per-play layer is off.

## Windows

Both windows are regular season only (`game_type == REG`). Playoffs are not in either window. Kickoff is `gameday` plus `gametime`, read as an America/New_York wall clock. A missing clock would be 13:00 Eastern. None of the pinned week-1 ids needed that fill.

A game is pinned by its identity and its kickoff, not by its score.

### Window 1, 2026 early read

Every 2026 regular-season game whose kickoff is on or after 2026-11-22 00:00 America/New_York, through week 18. That is 121 games, 8 season-weeks (week 11 is partial). The list is `outputs/totals/window1_game_ids.csv`, with a header `game_id,kickoff_et,week`, sha256 `1a935f89a6ae60612c530493095adc1bcb93b99e5059176766dfee7502aa82ee`. `2026_11_ARI_KC` is on the list. `2026_11_IND_HOU` kicked off 2026-11-19 20:15 America/New_York and is not on the list.

`load_window1` pins that count at 121 and that sha256. A mismatch aborts. `freeze_week.py` reads the week's game ids from this file. It does not choose the week by querying the schedule. The schedule supplies kickoff, teams, and scores for ids the file already names.

The schedule's week 12 starts 2026-11-25 20:00 America/New_York (`2026_12_GB_LA`). The games on 2026-11-22 are schedule week 11. The freeze that has to land before the first kickoff on 2026-11-22 is the week-11 remainder: the twelve week-11 games on that list. Thursday's game is already excluded. The week-12 freeze is a later commit, before 2026-11-25 20:00 America/New_York.

Window 1 is descriptive. It does not issue SUCCESS, FAILURE, or INCONCLUSIVE. It is scored once, after week 18, when every pinned game has a final score or a drop reason. The harm check is that single score: flag harm if the 90% lower bound on mean d is positive. A void window is not a harm flag.

### Window 2, the decisive call

The full 2027 regular season. The 2027 schedule is not in the frozen cache. The set is every 2027 regular-season row on the nflverse schedule snapshot committed with the first 2027 weekly freeze, before any 2027 score is read. The planning count is 272, the regular-season count in 2021, 2023, 2024, 2025, and 2026 in the frozen cache. 2022 had 271 and is not the planning count. If the snapshot count is not 272, replace only n in the standard-error formula below. Do not refit the spread or the design effect.

SUCCESS if mean d is negative and the 90% upper bound is negative. FAILURE if the 90% lower bound is positive. Otherwise INCONCLUSIVE. Scored once, after the 2027 regular season is complete.

## Interval

The 90% interval is the Webb wild cluster bootstrap in `webb_mean_interval`. Clusters are `(season, week)`. Weights are −√1.5, −1, −√0.5, √0.5, 1, √1.5, each with probability 1/6 (the last draw absorbs the floating-point remainder). 999 replicates. The generator is `numpy.random.default_rng(20261115)`. Each window draws its own weights from that seed. The 90% bounds are the 5th and 95th percentiles.

There are no interim looks. A window is scored once, after it ends.

## Minimum detectable effect

The spread of d was not computed. No P3 total result is used. The proxy is the committed column `crps_total` on the 256 regular-season rows of `outputs/p2/dev_2018_games_ypp_off.csv` (sha256 `02154deadfc3c79188f7a031f6939e9e373d59cf88a61a6ca2751e4c7176e7fc`). That file is the 2018 dev evaluation of the frozen sim with the yards-per-play layer off. The fit for that run is 2015–2017. Those three seasons are not a second CRPS sample, and they are not re-simulated here. The sample standard deviation of `crps_total`, divisor n − 1, is 6.6103785609178995.

Assume the unseen v5 per-game total CRPS has the same spread, and assume the two series are uncorrelated. A positive correlation would shrink the spread of the difference, so zero correlation is the conservative choice. Then

sd(d) = 6.6103785609178995 × √2 = 9.348487013270438

On those 256 games and 17 weeks, the cluster-robust standard error of the mean of `crps_total`, with the G/(G−1) correction, is 0.3327346198549104. The iid standard error is 0.4131486600573687. The design effect is 0.6486094664277804. The cluster sum is the sum of (value − mean) inside the week, and the standard error is the square root of (G/(G−1)) times the sum of squared cluster sums, divided by n². The design effect is below 1. The formula is not revised because of that.

Apply that design effect and sd(d) to each window. The normal 90% critical value is 1.6448536269514722. This is a normal approximation to the Webb interval, not a bootstrap of d.

| window | n | SE | MDE |
| --- | ---: | ---: | ---: |
| 2026 early read | 121 | 0.6844477266470843 | 1.1258163256341465 |
| 2027 regular season | 272 | 0.4565081322643813 | 0.75088905708791 |

The MDE is not a second decision rule. The calls above do not depend on it.

At about 1.13 total-CRPS points on 121 games and 0.75 on 272, the likely result is INCONCLUSIVE unless the effect is large. The confirmatory window is not extended. The full 2027 regular season stays the decisive window, as Harrison set it. Any extension would be his call.

## Freeze protocol

Each week, `freeze_week.py` writes the sim total distribution and the v5 total mean and sigma for that week's pinned games. The inputs are games whose kickoff is strictly before the cutoff. The cutoff is the first kickoff among the games being frozen, except for the week-11 remainder: that cutoff is the first kickoff on 2026-11-22 (13:00 America/New_York). Thursday's completed game may sit in that history. It is not a projected game.

Commit the projection file, the input-hash file, and the manifest. `inputs.json` records `freeze_timestamp_utc` at the moment the files are written. `manifest.sha256` records the sha256 of `projections.csv`. The commit timestamp and `freeze_timestamp_utc` both have to be strictly before the first kickoff of the games in that file. A game whose projection was not committed before its own kickoff is `late_freeze` and is dropped from both models. Anything published from that week, including a website copy, has to come after that commit.

Do not run `freeze_week.py` for any week until this document says `Status: locked`.

There is no backfill. The script aborts unless the current time is strictly before that slate's first kickoff, America/New_York. It also refuses any individual game whose kickoff is already past. A missed freeze is excluded. It is never backfilled, and a missed week is never rerun.

## Postponements and cancellations

- A cancelled game is excluded. It does not count toward the drop rate or the void rate. A pinned game missing from the schedule is excluded the same way. Either one is logged in `exclusions.csv`, and the other games in that week are still frozen.
- A game rescheduled within the same week stays in that week's slate and keeps that week's freeze, as long as the freeze is strictly before its actual kickoff.
- The slate uses each pinned game's current schedule week and kickoff. A game moved to another week is frozen once, in the slate of the week it is actually played, using that week's pre-week cutoff. If it is played after week 18, it is excluded.
- Only one freeze per game counts: the first valid freeze whose timestamp is strictly before the game's actual kickoff. A game that already has a projection row from an earlier week is skipped and logged, and the rest of that week is still frozen. `write_freeze` aborts without writing if that week's directory already exists, or if the same game would be written twice in this freeze. A later file does not replace the first row. A file written after kickoff does not count.

The week-11 remainder is fully identified from the locked schedule and the locked derived plays. Every game before 2026-11-22 13:00 America/New_York has a score and plays. A later freeze aborts if any scheduled game before its cutoff lacks a score or lacks plays. The missing week is supplied with `--update`, a directory of `games.parquet` and `pbp.parquet`. That directory is hashed in the freeze inputs. It is not written into `.cache` or `outputs/p3`. Feature rows for those games are the rows the earlier freeze committed, not a refit after they were played. The 2027-01-01 screen used by the cache study is off for this protocol. The only cutoff is the week's own.

## Scoring source

Score a window only from the `projections.csv` files whose sha256 lines are in the freeze manifests committed on this branch. Do not score from a website, a published copy, or any other checkout. That includes `games.json` on nfl-app pull request 54. A file that is not the committed bytes is not the freeze, even if the numbers look the same.

## Publishing

A week is published all or none. The published set is every row in that week's `projections.csv`, including rows with a drop reason. No game is added, removed, or swapped after the freeze. This rule binds the publisher. It does not change the scoring set, which remains the committed file.

## Freeze file format `totals-freeze-v1`

`projections.csv` has these columns, in this order, and no others. The website importer matches this version exactly. A new column or a new unit is a new version string, not a silent edit.

| column | unit | definition |
| --- | --- | --- |
| format_version | text | `totals-freeze-v1` on every row |
| game_id | text | schedule game id |
| season | season | integer |
| week | week | integer NFL week |
| kickoff_et | timestamp | ISO 8601 with the America/New_York offset |
| home_team | text | canonical team abbreviation |
| away_team | text | canonical team abbreviation |
| home_qb | text | pre-game quarterback id, or empty |
| away_qb | text | pre-game quarterback id, or empty |
| drop_reason | text | empty, `missing_qb`, or `model_failure` |
| sim_margin_mean | points | mean of the 10,000 draws, home minus away |
| sim_total_mean | points | mean of the 10,000 draws, home plus away |
| sim_margin_pmf | probability | 161 masses on integers −80 through 80, index 0 is −80, space-separated |
| sim_total_pmf | probability | same grid and index as the margin pmf |
| v5_margin | points | no-market v5 mean, home minus away |
| v5_total | points | no-market v5 mean, home plus away |
| v5_sigma_margin | points | walk-forward sample standard deviation |
| v5_sigma_total | points | walk-forward sample standard deviation |
| n_sims | count | 10000 |
| seed | integer | the per-game seed |
| ypp_on | boolean | false |

Margin is home minus away. A positive margin means the home team is ahead. Total is home plus away. Both are in NFL points. The integer grid is the same grid the score uses.

## Publishing and the market

Publishing the sim's numbers could, in theory, move a market line. This test does not score against a line. If a later report ever scores these freezes against closing lines or closing line value, that report has to say so in its first paragraph.

## Drops and voids

Both models are scored on the intersection.

- `missing_qb`: either side has no depth-chart QB1 and no prior-start quarterback. The depth-chart QB1 is the pre-game chart. The prior start is that team's charted quarterback on the most recent completed game whose kickoff is strictly before this kickoff. Do not use `passer_player_id` or any other in-game passer.
- `model_failure`: either model raises, or returns a non-finite margin, total, or sigma, or a probability vector that does not sum to 1.
- `late_freeze`: the projection commit is not strictly before that game's kickoff.
- `not_completed`: at the single scoring time, the pinned game still has no finite pair of scores.
- A cancellation, and a move to after week 18, is an exclusion. It is removed from the drop numerator and the drop denominator.

The drop rate is dropped games divided by pinned games in that window. A rate above 5% voids the window. A void window gets no SUCCESS, FAILURE, INCONCLUSIVE, or harm flag. Drop counts are reported by reason.

## Market-blind

No line, odds, prop, or prediction-market price is an input, a prior, a weight, or a target. Market columns are stripped before a frame is used, and a remaining market column aborts the freeze. No Odds API call and no other paid call.

These paths are not imported and not called:

- `build_week_model`
- `market_ratings`
- `scripts.backtest_v5` and `backtest_v5.py`
- shipped `MARGIN_COEFS`, `MARGIN_COEFS_NO_MKT`, and `TOTAL_COEFS`

`v5_replay.py` is the allowed ratings path. `fit_ratings` and `build_qb_book` receive the frozen `DEFAULT_PARAMS`. The `mkt_*` keys in that dict are not read.

## Files

Weekly, before kickoff, `freeze_week.py` writes a new directory under `outputs/totals/freezes/`. It does not write `outputs/p3`, it does not write `.cache`, and it does not score.

After a window ends, and only then, a later run may write that window's score. This document does not authorize that run.
