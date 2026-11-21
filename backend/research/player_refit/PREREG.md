# PREREG: market-blind player-opp refit

```text
status: draft
locked: false
eligible_set_sha256: ccfbb2bee685f3f4e6f3bfb416ed9dd71b195716a63f2c2cc2eb66927c688d23
eligible_set_n: 70094
hyperparameters_sha256: 79a0df469fae4f26d13b46d0f820284a69783ec6e6e4e1f803370af99d948892
seed: 20261115
```

This document is still a draft. QA locks it. Nothing in this revision fits a
coefficient or scores a stat. The only data work is the eligible set below,
built from public nflverse schedules and depth charts.

## How to read a result

The incumbent is `player-opp-v1` as it ships. It is not given a walk-forward
refit. Its team-volume coefficients were fit on 2016–2025 team-games. Its
quantile coefficients were fit on 2016–2024. Those windows overlap the scored
seasons, so the incumbent is partly in-sample. The refit never sees the week
it is scoring.

A refit win counts as evidence even with that bias. The incumbent already saw
many of these outcomes, and the refit still has to beat it.

A refit loss does not count as evidence that the refit is worse. An
INCONCLUSIVE result does not count as evidence that the refit is worse.
Both of those leave the in-sample incumbent in place. They are not a finding
that the market columns are required.

## Estimand

Both arms emit the shipped 11 quantiles for attempts, completions, passing
yards, carries, rushing yards, targets, receptions, and receiving yards.

```text
d = mean over scored stat-rows of ( CRPS(Q_refit, y) − CRPS(Q_inc, y) ) / scale[stat]
```

Negative means the refit is closer to the actual. `scale` is frozen:

| Stat | scale | source |
|---|---:|---|
| receiving_yards | 23.03 | `docs/MODEL_V5.md` opp-v1 median MAE |
| receptions | 1.68 | same |
| rushing_yards | 25.84 | same |
| passing_yards | 65.41 | same |

Targets, carries, attempts, and completions have no published MAE. Their
scales are the incumbent arm's own mean absolute error of the 0.50 quantile
on the scored rows. Those four numbers are written down before the bootstrap
and are not revised. They are a unit. They are not a second look at `d`.

CRPS is the same functional of both quantile functions. With pinball
`ρ_τ(u) = u · (τ − 1{u < 0})` and `Q` extended to 0 and 1 by the tail slopes
in `player_opportunity_model.mean_from_quantiles`:

```text
CRPS(Q, y) = 2 · ∫_0^1 ρ_τ(y − Q(τ)) dτ
```

The integral is the trapezoid on `τ ∈ {0, 0.05, …, 0.95, 1}`.

Secondary, and unable to change the call: the same rows, MAE of the 0.50
quantile, divided by the same scale, refit minus incumbent.

## Walk-forward, one week at a time

The refit is walk-forward by week. It is not fit once per season.

The cutoff for scoring week `(season, week)` is the first regular-season
kickoff in that week, as an America/New_York instant. A missing nflverse
clock is 13:00 Eastern. A row whose kickoff is equal to the cutoff is out.
Every fit window below is strictly earlier than that cutoff.

Three windows, named:

1. **v5 no-market replay.** This is not live v5. Live v5 carries `mkt_d`
   at 0.669 (`team_ratings_v5.MARGIN_COEFS`, `v5.coef_market_prior`). The
   replay is `backend/research/nfl_drive_sim/v5_replay.py` on
   `research/nfl-drive-sim` at commit
   `0c49a45c9b90b6b4635ed248bc271effbe13abfd`. The file sha256 is
   `bee8006b5b3096e7c1bc3ff53afc2cb211b417e2e1b7ca66e47ac442ff9892d3`.
   That branch is not modified. The file is referenced by that commit. It
   is not copied, because it imports the rest of that package.
   `ratings_as_of` and the stage-2 refit in that file use only rows whose
   kickoff is strictly before the cutoff. They do not call
   `build_week_model`, `market_ratings`, or `scripts.backtest_v5`. They do
   not read `MARGIN_COEFS`, `MARGIN_COEFS_NO_MKT`, or `TOTAL_COEFS`. The
   margin design is `epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`. The total
   design is `pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`, plus a column of
   ones. Team points are `(total ± margin) / 2` from that replay. Pace
   (`plays_s`) and efficiency (`epa_s`, `epa_d`) enter only through those
   points.

2. **Player team volume.** Three OLS fits, one each for team targets, team
   carries, and team pass attempts. Training rows are team-games whose
   kickoff is strictly before the cutoff. The design is

   ```text
   volume = a + b·team_ewm + c·team_points_no_mkt + d·opp_points_no_mkt
   ```

   `team_points_no_mkt` and `opp_points_no_mkt` are the points from window 1
   for that past game, taken from the replay as of that past game's own
   week. They are not refit with the scoring week in the sample.

3. **Quantile coefficients.** For each of the eight stats and each of the
   11 levels, `Q(τ) = a_τ + b_τ · hat`, pinball loss, on player-stat rows
   whose kickoff is strictly before the cutoff. `hat` uses the team-volume
   coefficients from window 2 and profiles built only from earlier games.
   The shipped monotone rearrangement is applied after the fit. There is no
   prop anchor and no market prior.

2018 may sit in windows 1–3. It is not scored. Half-lives, role gates,
quantile levels, the column lists, and `DEFAULT_PARAMS` are not estimated
in any window. They are the file
`backend/research/player_refit/hyperparameters.json`. Its sha256 is
`79a0df469fae4f26d13b46d0f820284a69783ec6e6e4e1f803370af99d948892`.
`DEFAULT_PARAMS` itself hashes to
`f2e0072102c421144df23dceec743c7b6eab51d17858be4279709ad5238eb22a`
(sorted keys, compact separators). The `mkt_*` keys are in that dict. The
replay does not read them.

Weather, injury, and admin levers are off on both arms.

## Eligible set

A player-game is eligible when the pre-kickoff roster rule puts that player
on that team for that game. The rule is the quarterback rule
(`pregame_quarterback` plus `resolve_qb` on the drive-sim commit above),
applied to every QB, RB, WR, and TE the chart lists.

1. A weekly depth chart for `(season, week, team, game_type)` is the roster.
   It is treated as known at kickoff minus one hour. It has no publication
   timestamp. Special-teams-only rows (returner listings) are not a second
   person. One row per player. The lower depth rank wins.
2. If that week has no chart, the roster is the latest snapshot whose `dt`
   is strictly before kickoff. 2025 is a snapshot file. 2015–2024 are weekly
   files.
3. If neither exists, the roster is the resolved roster of that team's most
   recent completed game with an earlier kickoff. The prior player is the
   charted player, not the player who took the snap.
4. If a chart exists but has no QB1, the prior charted QB1 is added. That is
   the quarterback fallback. Eighteen rows in this set took it.

The scored seasons are the completed regular seasons 2016, 2017, 2019, 2020,
2021, 2022, 2023, 2024, and 2025. 2018 is dev-touched (it is inside both
shipped fit windows and inside the v5 tune window) and is not in the file.
2026 is not in the file. 2015 and 2018 are walked only so a later week has a
prior roster. Playoff games are walked for the same reason and are not
written out.

Team codes are the franchise ids (`LA`→`LAR`, `OAK`→`LV`, `SD`→`LAC`). The
nflverse `game_id` is unchanged.

The set has **n = 70094** player-games, 2383 games, 4766 team-games, and 0
team-games with an empty roster. Sources: 60172 weekly depth-chart rows,
9904 snapshot rows, 18 prior-start rows. Positions: 11825 QB, 15834 RB,
26670 WR, 15765 TE.

The key file is `backend/research/player_refit/eligible/eligible_keys.txt`.
Each line is `season|week|game_id|team|player_id|position`. Lines are sorted
lexicographically. The sha256 of the file bytes is
`ccfbb2bee685f3f4e6f3bfb416ed9dd71b195716a63f2c2cc2eb66927c688d23`.
The same rows with depth and source are in
`eligible_player_games.csv`. Input file hashes are in
`eligible_summary.json`. The builder is
`materialize_eligible.py`. It does not fit and does not score.

A player-game the frozen role gate rejects is not scored and is not a drop.
The gate is the shipped `eligible()`: at least two prior games, and the
share floor in `hyperparameters.json`, evaluated only on games before the
cutoff. The actual from the scoring week is not an input to the gate.

## Incumbent arm

The incumbent uses the shipped `TEAM_VOLUME` and `QUANTILE_COEFS`. Those
numbers are not re-estimated.

For each scoring week its `imp_pts` and `spread_team` are the production
inputs, built strictly before the cutoff: v5 margin and total with the
shipped market prior (`mkt_d` weight 0.669) from closing spreads of earlier
games only. This week's close is not an input. This is the live path's team
volume. It is not the no-market replay. The no-market replay is the refit
only.

The prop-anchor quantile shift is **off** on this arm.

Checked for a historical pre-kickoff prop archive and did not find one. The
table `player_prop_snapshots` has `captured_at` and `commence_time`
(`backend/app/models/player_prop_snapshot.py`, lines 33–34), so a stored row
could be kept only when `captured_at` is strictly before kickoff. Nothing in
the repository is such a row. There is no csv, parquet, or json dump of
snapshots. Tests build snapshots in memory. No Odds API call was made, and
the production database was not read. Eighteen prior-start roster rows are
not prop rows.

The live site can still shift a quantile when a two-book snapshot is in the
database. That shift is not in this comparison. A result here does not
describe a week on which that live shift fired.

## Decision

Cluster is `(season, week)`. The interval is `webb_mean_interval` in the
v5 replay file named above: Webb weights −√1.5, −1, −√0.5, √0.5, 1, √1.5,
each with probability 1/6 and the last weight absorbing the remainder so the
six sum to 1, 999 replicates, generator `numpy.random.default_rng(20261115)`.
The 90% bounds are the 5th and 95th percentiles of that bootstrap. One draw
per analysis. The generator is not reused for anything else.

- **SUCCESS.** The mean `d` is negative and the 90% upper bound is negative.
  This is evidence for the refit, including against the in-sample incumbent.
- **FAILURE.** The 90% lower bound is positive. This is not evidence that
  the refit is worse.
- **INCONCLUSIVE.** Every other interval, including a negative mean whose
  upper bound is not negative. This is not evidence that the refit is worse.

The in-run minimum detectable effect is `2.49 × SE`, where `SE` is the
standard deviation of the 999 bootstrap draws of the mean. It is reported
next to the interval. It does not enter the decision. The planning figure
from the previous draft does not enter the decision either.

## Drops and voids

Same rule as P3. A missing input or a failed model drops the player-game
from both arms. The arms are then scored on the intersection.

The denominator is the eligible player-games the role gate keeps. A drop is
one of those player-games where either arm is missing an input it needs or
returns a non-finite quantile for a stat the gate kept. The drop rate is
drops divided by that denominator. A rate above 2% voids the run. A voided
run issues no SUCCESS, no FAILURE, and no INCONCLUSIVE call.

Failing the role gate is not a drop. A large residual is not a drop. An
actual of zero stays.

## Market data

Market data is used only to replay the incumbent, and only as the past-close
`mkt_d` described above. The refit does not read it. This week's close may
be joined after both projections exist, as an edge column. That column is
not `d`. The Odds API is not called. Prop prices are not a feature, a
target, or a weight on either arm in this test.

## Bans

`backend/tests/test_player_refit_arms.py` monkeypatches the banned calls so
they raise, then runs each arm on synthetic rows.

The refit must not call `over_prob`, the prop-anchor shift, any odds feed,
or a spread / `imp_pts` market input, and it must not call live v5
(`team_ratings_v5` while `mkt_d` is in its margin coefficients). Its team
points come only from the pinned `v5_replay`. The incumbent must not call
the prop-anchor shift or odds. The incumbent arm runs with the prop anchor
off.

The fit window is still the one above: strictly earlier than the first
regular-season kickoff of the scored week, in Eastern time. A row at that
exact kickoff is excluded.

## Cost

The run stops at 4 CPU-hours. Spend is $0. The Odds API is not called.
Hitting the stop means switching solvers. It does not mean dropping a
season, a week, a stat, a row, or a bootstrap replicate. The replicate count
stays 999.
