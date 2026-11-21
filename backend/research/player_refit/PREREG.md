# PREREG: market-blind player-opp refit

```text
status: draft
locked: false
eligible_set_sha256: PENDING
eligible_set_n: PENDING
seed: 20261115
```

This document is a draft. It is not locked. The eligible-set hash is
`PENDING` because this run does not materialize the set, fit a model, or
score a row. Locking means: the season rule below is unchanged, the hash and
`n` have been written into the results artifact before any coefficient is
estimated, and this header's `locked` field is then set true. Until that
happens, nothing in here is a result.

The refit is compared with current `player-opp-v1` as that model actually
runs, market inputs included. That is the incumbent. The refit is not
compared with a market-blind re-estimate of the incumbent.

Market data is used only to replay the incumbent. It is not a feature, a
prior, a blend weight, or a fit or calibration target of the refit. It is
not used to choose the season rule, the scales, or the decision thresholds
in this document.

## Estimands

Both models emit the shipped 11 quantiles
`(0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)`
for attempts, completions, passing yards, carries, rushing yards, targets,
receptions, and receiving yards.

Primary metric, one number:

```text
Δ = mean over eligible rows of ( CRPS(Q_refit, y) − CRPS(Q_inc, y) ) / scale[stat]
```

Negative means the refit is better. `scale[stat]` is frozen below. The mean
is over player-stat rows, not over stats, so a stat with more eligible
players weighs more. Dividing by `scale` is what stops passing yards from
deciding the pool by being on a larger unit.

CRPS is the same functional of both quantile functions. With pinball
`ρ_τ(u) = u · (τ − 1{u < 0})` and `Q` extended to τ = 0 and τ = 1 by the
tail slopes in `player_opportunity_model.mean_from_quantiles`:

```text
CRPS(Q, y) = 2 · ∫_0^1 ρ_τ(y − Q(τ)) dτ
```

The integral is the trapezoid on
`τ ∈ {0, 0.05, 0.10, …, 0.95, 1}`. No normal CRPS on one side and quantile
CRPS on the other.

Secondary metric, not decisive:

```text
Δ_MAE = mean over eligible rows of ( |q50_refit − y| − |q50_inc − y| ) / scale[stat]
```

`q50` is the 0.50 quantile after the shipped monotone rearrangement
(nonnegative, then sorted). A secondary sign that disagrees with Δ is
reported. It does not flip SUCCESS or FAILURE.

### Frozen scales

From the opp-v1 median MAE in `docs/MODEL_V5.md` (the published incumbent
figure, not a number computed for this test):

| Stat | scale |
|---|---|
| receiving_yards | 23.03 |
| receptions | 1.68 |
| rushing_yards | 25.84 |
| passing_yards | 65.41 |

Targets, carries, attempts, and completions have no published MAE. Their
scales are the incumbent's own mean absolute error of `q50` on the hashed
eligible set. Those four numbers are computed once from the incumbent replay
and the actuals, written into the results artifact next to the hash, and
then frozen. They are a unit conversion. They are not a second look at Δ.
The refit is not used to choose them.

## Seasons, eligibility, hash

Scored seasons are the completed regular seasons 2016, 2017, 2019, 2020,
2021, 2022, 2023, 2024, and 2025.

2018 is excluded from the scored set. It is dev-touched: it sits inside the
shipped team-volume fit (commented OLS, 2016–2025), inside the shipped
quantile fit (commented 2016–2024), and inside the v5 hyperparameter tune
window (2016–2020, `docs/MODEL_V5.md`). The repo does not single 2018 out
beyond that. The exclusion is because those windows touch it, which is the
condition this prereg was told to apply. 2018 remains usable as ordinary
pre-week history when a later week is fit. It is not a place to retune
half-lives, gates, features, or this document.

2026 is excluded. As of this draft (2026-11-21) that season is not over, so
its week set is not a fixed population.

A row is eligible when all of the following hold. The actual's magnitude is
not one of them.

1. `season_type` is regular season. Preseason and postseason are out.
2. `season` is one of the nine scored seasons. Week is an integer from 1 to 18.
3. Position is QB, RB, WR, or TE.
4. The stat is one of the eight, and `player_opportunity_model.eligible`
   is true on the profile built from games strictly before `(season, week)`.
   That is the shipped rule: at least two prior games, and the EWMA share
   gate (targets 0.04, carries 0.08, attempts 0.40).
5. A weekly row exists for that player-week and the stat is present and
   finite. A played game with a zero stays in. A player with no row (bye,
   healthy scratch, out) is not eligible. Zeros are not dropped for being
   zeros.

`n` is the number of eligible `(season, week, player_id, stat)` keys. It is
not chosen to hit a target. The hash is SHA-256 of those keys sorted
lexicographically as UTF-8 `season|week|player_id|stat`, one key per line,
with a trailing newline after the last key. The hash and `n` are written
before any refit coefficient is estimated. This draft does not contain them.

The incumbent was fit on seasons that overlap this set (team volume through
2025, quantiles through 2024). The refit never sees the week it is scoring.
The comparison is therefore kinder to the incumbent than a refit-versus-refit
would be. That is intentional: the incumbent is the shipped model. A SUCCESS
has to beat a model that already saw many of these outcomes. A FAILURE does
not, by itself, prove the market columns are required.

## Walk-forward

Weeks are processed in `(season, week)` order. For a scored week, every
coefficient the refit uses is estimated on rows strictly before that week.

Refit team volume, separately for targets, carries, and pass attempts:

```text
volume = a + b·team_ewm + c·team_points_no_mkt + d·opp_points_no_mkt
```

`team_points_no_mkt` and `opp_points_no_mkt` are the v5 no-market score:
`MARGIN_COEFS_NO_MKT` and `TOTAL_COEFS`, with `market_ratings` not attached
and `mkt_d` not in the feature vector. Pace (`plays_s`) and efficiency
(`epa_s`, `epa_d`, and the points and QB terms already in those coefficient
vectors) enter only through that score. No extra pace or efficiency column
is added beside it. Those columns are collinear with the score, and adding
them would be a specification search.

Player share, efficiency shrinkage, half-lives, and role gates stay at the
shipped constants. They are not re-estimated. The structural hat is share
times team volume, times the shipped efficiency rate, matching
`structural()`.

Quantile coefficients are `Q(τ) = a_τ + b_τ · hat`, fit by pinball loss on
pre-week rows that pass `eligible` for that stat. The same monotone
rearrangement as `quantiles()` is applied after the fit. There is no prop
anchor and no market prior.

The incumbent replay uses the shipped `TEAM_VOLUME` and `QUANTILE_COEFS`
unchanged. Its `imp_pts` and `spread_team` are the production inputs: v5
margin and total with the shipped market prior (`MARGIN_COEFS`, including
`mkt_d` from closes of games strictly before the week), converted to team
points the way `predict_game` converts them. This week's close is not an
input. The prop-anchor shift is applied only when a pre-kick consensus
snapshot for that player and stat is already in the repository. There is no
such archive today. The replay must not call the Odds API, and must not fill
the gap with this week's line. A week with no snapshot is an unshifted
incumbent, which is what production does in that case.

Profiles for both models are built from games strictly before the week.
Weather, injury, and admin levers are off in both. They are not in the
historical feature set this test can build the same way for every week, and
leaving them off on both sides keeps them from becoming a second treatment.

## Decision rule

Cluster is the season-week. One Webb weight per cluster.

Webb six-point weights, each with probability 1/6:

```text
−√1.5, −1, −√0.5, +√0.5, +1, +√1.5
```

Generator: `numpy.random.Generator(numpy.random.PCG64(20261115))`.
Replicates: 9999. Draw order: clusters in sorted `season|week` order, and
inside a replicate the six-point draw is the next call on that generator.
No other random draw shares the generator.

Let `u_c` be the sum of per-row `(CRPS_refit − CRPS_inc) / scale` in cluster
`c`, and `n_c` the row count. `N = Σ n_c`. `Δ = Σ u_c / N`.

Replicate `b` reweights clusters:

```text
Δ*_b = Σ_c (w_{c,b} · u_c) / Σ_c (w_{c,b} · n_c)
```

The 90% interval is the basic bootstrap interval:

```text
[ 2Δ − q_0.95(Δ*),  2Δ − q_0.05(Δ*) ]
```

`q_0.05` and `q_0.95` are the 5th and 95th percentiles of the 9999
replicates, linear interpolation (NumPy default).

- **SUCCESS.** The entire interval is below 0.
- **FAILURE.** The entire interval is above 0.
- **INCONCLUSIVE.** The interval contains 0. Also inconclusive, regardless of
  the interval, if the void rate exceeds 2% of `n`, or if the hashed set has
  fewer than 5,000 rows or fewer than 40 clusters. Those floors are a broken
  extract, not a result.

No minimum effect size is added on top of the interval. The MDE below is the
honesty check on what this design can see. It is not a second threshold.

## MDE

This MDE was not estimated by running the bootstrap. It is a planning number
from the design, and it is the number this draft stands behind.

Assumptions, stated so they can be thrown out when the run replaces them:

- About 150 week clusters after empty weeks drop out (4 seasons × 17 week
  numbers in the 16-game era, 5 seasons × 18 in the 17-game era, minus weeks
  with no eligible row).
- About 90,000 eligible rows. That is an order of magnitude: nine seasons,
  a few hundred player-stat rows per week after the role gates. It is not `n`.
- Intra-week correlation of the row-level standardized CRPS difference
  `ρ = 0.20`. Rows in a week share game scripts. Treating 90,000 rows as
  independent would invent a much smaller MDE than the week clustering can
  support. `ρ = 0.20` is the conservative end of a 0.05–0.20 band, not a
  measured ICC.
- Row-level SD of the standardized difference `σ = 0.20`. This is the soft
  assumption. It is a guess that the gap between two similar forecasts moves
  around by about a fifth of the incumbent MAE. It was not computed.

```text
m     = 90000 / 150 = 600
deff  = 1 + (600 − 1) · 0.20 = 120.8
n_eff = 90000 / 120.8 ≈ 745
SE    = 0.20 / sqrt(745) ≈ 0.0073
```

The 90% interval excludes 0 about half the time when the true |Δ| is
`1.645 · SE ≈ 0.012`. The effect at which a two-sided 10% test has about
80% power is

```text
MDE_80 = (1.645 + 0.842) · SE ≈ 2.49 · 0.0073 ≈ 0.018
```

**Honest planning MDE: 0.018 standardized CRPS units, about 1.8% of the
incumbent's MAE scale.** In the published units that is roughly 0.41
receiving yards, 0.030 receptions, 0.47 rushing yards, and 1.2 passing
yards. The optimistic end of the same formula (`ρ = 0.05`, same `σ`) is
about 0.009. This draft does not use the optimistic end.

The run replaces this number with `2.49 · SE_boot`, where `SE_boot` is the
SD of the 9999 `Δ*` replicates. If that realized MDE is larger than 0.03,
the design is weaker than planned. The decision is still the interval rule.
A wide interval that covers 0 is INCONCLUSIVE, not evidence that the two
models are the same.

## Drops and voids

A drop is a player-week that never enters the hashed set. Drops are not in
the denominator. Bye weeks, preseason, postseason, 2018, 2026, failed role
gates, fewer than two prior games, and missing weekly rows are drops.

A void is a key that was hashed and then cannot be scored. Either projection
non-finite, a duplicate key, or an actual that is no longer finite. Voids
are counted. They are not imputed and not silently deleted. Void rate above
2% of `n` forces INCONCLUSIVE.

Large residuals are not dropped. A player who records 0 or who records 300
yards stays in if the pre-week profile was eligible.

## Banned in the refit

The refit, including every coefficient it estimates and every feature it
builds, does not call or read:

- `TEAM_VOLUME`'s `spread_team` and `imp_pts` columns, and any replacement
  that puts a spread, total, moneyline, closing line, or implied team total
  on the right-hand side or on the left-hand side.
- `market_ratings`, `mkt_d`, `MARGIN_COEFS["mkt_d"]`, `v5.coef_market_prior`.
- `_market_anchors`, `_bulk_market_anchors`, `_anchor_target`,
  `market_implied_mean`, `poisson_rate_from_over_prob`.
- The quantile shift in `_project_stat_opportunity` and the mean shift in
  the posterior branch of `_project_stat_for_game`.
- `props.anchor_weight_per_book`, `props.anchor_weight_cap`,
  `props.anchor_weight_cap_scoring`, `props.price_shift_cap_sd`.
- `over_prob` as a target, a weight, or a calibration series. After both
  projections exist it may price a line for a side column. That column is
  not Δ.
- `fantasy_market_service.apply_adp_anchor` and ADP weights.
- `model_lab.player_model.apply_market_anchor`.
- The Odds API, or any other paid odds or prediction-market feed.

`backend/scripts/backtest_v5.py` may be read to see how the no-market week
model is built. It is not run as the player test, and its market-prior fit
is not copied into the refit.

## Market data, incumbent only

Market data is used only to replay the incumbent.

- Past games' `spread_line` values, from the nflverse schedules the game
  model already reads, build `mkt_d` for the incumbent's v5 score. Games at
  or after the scored week are not in that fit.
- A stored pre-kick prop consensus, if one exists in the repo, is the
  incumbent's anchor for that player-stat. None exists for this historical
  window. No fetch is made to create one.
- This week's close may be joined after both projections are stored, to
  report an edge beside Δ. It is not a feature, not a target, and not a
  reason to drop a row.

The refit never sees those fields.

## Out of scope for the run this document describes

No production change, no deploy, no new parameter, no edit to the shipped
coefficients. A later implementation PR can add code under
`backend/research/player_refit/`. It does not add fitted artifacts, and it
does not run until this draft is locked.
