# Rest, bye, travel, and body-clock preregistration

This is the contract for one market-blind feature set on the v5-blind base. QA has not locked it. The 2024–25 holdout has not been fit or scored. No rest, bye, travel, time-zone, or body-clock coefficient has been estimated. Publishing stays off. This branch is not deployed.

A failed go/no-go does not authorize a retune. Any re-attempt, including a change to a feature definition, the allowlist, the walk-forward, or a bar, needs a fresh preregistration. It does not edit this file after a grade.

## Prior views of 2024–25

These seasons have already been graded by other looks. Those looks are not a grade of this feature set.

- PR #29, the feature bundle on v6-blind, nested 2016–25, one look, result fail. Locked prereg `2e5e4bb`.
- PR #32, shipped v6-blind against qb-ridge-v5 on the 2016–25 regular season.
- PR #33, v5-blind (T−24h starter, no market term) against that same v6-blind on the same games.

## Market blind

Spreads, totals, moneylines, prices, opens, and closes are grading columns. They are not model inputs, priors, seeds, blend weights, or fit targets. The design is fit by least squares to the actual home margin. Margin sigma stays 13.5. It is not tuned. ATS and the closing line do not enter the fit.

nflverse `home_rest` and `away_rest` are not design columns. Rest in this contract is computed from the schedule rule below.

## Venue table

Committed at `research/nfl_rest_travel/venues.csv` before any grade.

SHA-256 of that file, bytes as committed:

`d6f426dd638aeeef90600b1fe8a83cf0ab77cb776859863e6f5f8c5030c1fcf3`

Columns: `stadium_id`, `stadium_name`, `lat`, `lon`, `tz`, `country`, `label`.

- `lat` and `lon` are WGS84 degrees, four decimal places. They are published stadium locations, not fit to margins.
- `tz` is an IANA time zone.
- `country` is an ISO-like code (`US`, `CA`, `GB`, `DE`, `FR`, `ES`, `MX`, `BR`, `AU`).
- A row with `stadium_name` equal to `*` is the default for that `stadium_id`.
- A row with any other `stadium_name` is an exact-name override. Lookup uses the override when `games.csv` `stadium` equals that name, and the `*` row otherwise.
- The only overrides are `JAX00` / `Tottenham Hotspur Stadium` (London, not Jacksonville) and `MEX00` / `Estadio Banorte` (same building as Estadio Azteca; the sponsored name shares the Azteca coordinates).
- A stadium id with no row fails the build. Coordinates are not imputed.
- `label` is a name for audit. It is not a join key.

## Schedule rules

Team codes are canonicalized with the existing aliases: `JAC→JAX`, `WSH→WAS`, `ARZ→ARI`, `LA→LAR`, `OAK→LV`, `SD→LAC`, `STL→LAR`, `HST→HOU`, `BLT→BAL`, `CLV→CLE`.

A game is played when `home_score` and `away_score` are both finite. A cancelled, postponed-and-unplayed, or otherwise unscored game is not a previous game, is not a training row, and is not scored. The played game keeps the gameday on which it was actually played.

Kickoff is `gameday` plus `gametime`, interpreted as America/New_York. A missing `gametime` is 13:00. That instant is the only clock used for offsets and body time.

Franchise home venue for a team-season is the modal `*` or override venue among that team’s `location == Home` games in that season whose venue country is `US`. Ties break toward the earliest gameday. This uses the published schedule, not scores. Buffalo’s Rogers Centre games are country `CA` and do not become the franchise home. If a team has no US home game on that season’s schedule, the previous season’s franchise home is used, then the modal US home venue of all earlier seasons.

Previous played game for a team is the latest played game with an earlier kickoff, any `game_type` in the file.

Days of rest are calendar days, `current gameday − previous gameday`. If the previous played game is in a different season, or the team has no previous played game, days of rest are 7. The offseason is not a rest advantage and is not a bye.

Bye is 1 when the previous played game is in the same season and `current week − previous week >= 2`. Otherwise 0. Week numbers are the file’s `week`. Week 1 is not a bye.

## Features

The margin model has no intercept beyond these columns. Team features are home minus away. Game features are 0 or 1.

| Column | Definition |
| --- | --- |
| `rest_d` | Home days of rest minus away days of rest. |
| `bye_d` | Home bye indicator minus away bye indicator. |
| `thursday` | 1 when `weekday` is Thursday, else 0. This is the short-week term. It is game-level because both teams play that Thursday. |
| `intl` | 1 when the current venue’s country is not `US`, else 0. Canada is international. |
| `travel_d` | Home trip miles minus away trip miles. |
| `tz_east_d` | Home signed hours east minus away signed hours east. |
| `tz_cross_d` | Home absolute hours crossed minus away absolute hours crossed. |
| `body_d` | Home body-clock hour minus away body-clock hour. |

Trip rule, the same origin for miles and time zones:

- If the previous played game is in the same season and days of rest are 8 or fewer, the origin is that game’s venue.
- Otherwise the origin is the franchise home venue. A bye, a week 1, and any gap longer than 8 days starts from home.

Miles are the great-circle distance from origin to the current venue. Haversine, Earth radius 3958.7613 statute miles. A team playing at its origin has 0 miles.

Signed hours east are the current venue’s UTC offset minus the origin venue’s UTC offset, in hours, at the kickoff instant, so daylight saving is the offset then in effect. East is positive. Absolute hours crossed are the absolute value of that signed difference. A same-zone trip is 0.

Body-clock hour is the kickoff expressed in the franchise-home time zone, as hour plus minute/60. It does not use the opponent’s zone and it does not use the current venue’s zone. Two Eastern teams in London both have an early body-clock hour, so `body_d` is about 0; `intl` carries the game-level fact.

No other rest, travel, roof, weather, or weekday column is eligible. Friday, Saturday, Monday, and Sunday are the Thursday baseline. There is no separate “days of rest ≤ 5” column. There is no eastbound indicator beyond the sign of `tz_east_d`. Features are not standardized.

## Allowlist

The augmented design may contain only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`, `rest_d`, `bye_d`, `thursday`, `intl`, `travel_d`, `tz_east_d`, `tz_cross_d`, `body_d`

v5-blind, the baseline, may contain only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`

A duplicate column, or any column outside the list being fit, raises before the regression. `mkt_d`, `spread_line`, moneylines, totals, and the raw nflverse rest fields are outside both lists. The guard is an allowlist. It is not a blocklist.

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

v5-blind and the augmented model are fit on the same seasons, each on its own columns. The same games are compared. Training may include the playoffs. Grading is regular season only (`game_type == REG`).

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

The selection script scores no game from 2024 or 2025. It does not choose a subset of the allowlist after seeing `z`. It does not add a column that is not in the table above.
