# NFL ML market-blind retrain — preregistration

Status: locked for QA. No holdout has been scored. No booster has been fit on NFL games. Publishing stays off. `include_ml` stays at its deployed default, false.

This file is the contract. `research/nfl-ml-blind/TRAIN_SPEC.json` is the same contract in machine-readable form and is checked against the code constants by tests. If those disagree, the evaluation does not run.

## What was searched before this lock

No branch named `research/nfl-ml-blind-retrain` existed. The workspace, `/tmp`, and this agent's store had no prereg, results file, CRPS table, or fitted booster for this retrain. Nothing here is a second look at a market-blind ML grade.

These already-published looks are not this grade and are not rerun:

- PR #41 (open, not merged) scored a spread-residual refit against the negated residual. That look is spent for the sign fix.
- `docs/MODEL_V6_BLIND.md`, `research/nfl-feature-bundle`, `research/nfl-qb-transitions`, and `research/nfl-wind-totals` score other models. The feature-bundle file says QA has not locked it. Its gates are not substituted for the gates below.

## Production incumbent

Checked against the live Railway service, not against an assumption.

- Project `genuine-appreciation` (`8df62a39-e948-4a7d-be06-ddf98458200d`), service `nfl-app` (`906d3263-561b-4455-a5c7-583c0c3b36c3`), production.
- Deployment `2d608399-7172-4007-874e-17c8ed19bfa5`, success, created 2026-11-01T23:37:27Z.
- Source: `Harrisoneller/nfl-app`, branch `main`, commit `389498b7a060c8e62619b3369515f14ac00b72ff`.
- That commit's parent `ff94d2c8f29650fd067336dd855ede84d26b1ffe` (#43) is an ancestor. `predictions.games` defaults `include_ml` to false. The web client defaults `includeML` to false. The Railway variable list has no ML-enable flag.
- `PREDICTION_MODEL_VERSION` on that commit is `qb-ridge-v5`.
- `prod/v6-blind-main` is not the deployed source. v6 is not the incumbent.

The incumbent margin is `team_ratings_v5.WeekModel.predict` home margin using the shipped `MARGIN_COEFS`, including the past-line `mkt_d` prior that production v5 already uses. Features for that model stay on the production rule: games with `week` strictly before the target week. Stage-2 coefficients are the shipped constants (the docstring records an OLS fit through 2026 week 3). They are not refit. That look-ahead in the coefficients is a property of the deployed artifact.

Not part of the incumbent number:

- `market_service.apply_market_blend` (live consensus for the current slate; it is not a stored 2016–2025 board, and blending toward the close would make ATS vacuous).
- The context-layer point shift and the game-conditional sigma. Both are live transforms. The locked noise model for this comparison is flat sigma 13.5, which is `dist.margin_sigma`'s code default and the sigma already used in PR #41 and the v6 write-up.

Incumbent home win probability is `prediction_dist.win_prob(margin, 13.5)`.

## Leak this retrain closes

In `ml_predictions_service._build_features_for_season` at `389498b`:

- Pass/run EPA is averaged by team-week, then rolled four weeks.
- The pre-game lookup is `target_week = max(week - 1, 1)`.
- For week 1 that index is 1, so the feature reads week 1, including the target game and every other game that week.
- `_team_prev_metric` keeps `week <= target_week`, so the same week is inside the recency feature.
- There is no prior-season EPA. The early-season fallback is the same week's number, or 0 only when that team has no week-1 row.
- Elo is one database snapshot from the previous season, reused for every week. It does not read the target week, and it also does not move within the season.

## Point-in-time rule (T−24h)

nflverse `gametime` is not treated as a UTC kickoff. The cutoff is calendar dates.

A prior game on date `d` may enter any result-derived feature of a target on date `D` only when `d <= D - 2 days`. Same-day games and next-day games are out. That removes week-1 self-inclusion and same-week Thursday/Sunday/Monday results.

Result-derived features are Elo and the EPA features (and the proxies computed from those EPA features). Rest days read the schedule only: the most recent team game with `d < D`. The result of that game is not read for rest.

Early-season fallback: if a team has no game on or before `D - 2 days`, offensive and defensive EPA and their recency values are 0. They are never the same-week mean, the week-1 row, or a full-season mean. Season `S - 1` is loaded as history so a week-1 game can see last season when those dates clear the cutoff. Preseason rows are not history and are not labels. Postseason rows may inform features. They are not training labels and they are not graded.

Realized temperature, wind, and precipitation are not features. Roof is known before kickoff: indoor (`closed`, `dome`, `indoor`) codes as 0, anything else as 1. `weather_is_bad` is locked at 0.

## Targets and market ban

- Spread model target: `home_margin = home_score - away_score`.
- Win-probability model target: `home_win = 1` if `home_margin > 0`, else 0. Ties are 0.
- No `market_baseline`. No `home_margin - spread_line`. No `home_win - market_home_win_prob`.
- Market columns are not inputs, priors, blend weights, sample weights, or calibration targets.
- Platt scaling is fit on the training fold's own predicted probabilities against actual `home_win`. It is not fit on the test season and not fit on a price.
- Closing lines are joined only in the evaluation script, after prediction, for ATS. That script does not run in this phase.

The model matrix is the allowlist below. `assert_feature_allowlist` raises if a requested column is off the list or if its name contains a market token (`spread`, `moneyline`, `odds`, `price`, `market`, `vegas`, `implied`, `closing`, `opener`, `total_line`, `juice`, `vig`, `clv`). `assert_no_market_columns` raises if any of those tokens appear on the frame passed to fit or predict.

## Feature allowlist

Order is the matrix order.

| Column | Definition |
| --- | --- |
| `home_elo`, `away_elo` | Pre-game Elo. K = 20, home-field = 55 Elo points, initial = 1500, season carry = 0.75 toward 1500, 538-style margin multiplier with those constants hardcoded. Neutral when `location == "Neutral"`. Updated only from cutoff-eligible REG/POST results. Not the admin registry and not the database snapshot. |
| `home_off_epa`, `away_off_epa` | Unweighted mean EPA/play of that team's last 4 cutoff-eligible team-games. Pass and run plays only. 0 if none. |
| `home_def_epa`, `away_def_epa` | Same window on EPA allowed (plays where the team is `defteam`). |
| `*_epa_recency` | Same window, weights `0.5 ** (age / 3.5)` with the newest game at age 0. |
| `rest_days_home`, `rest_days_away` | Days since the previous scheduled game with `d < D`, clipped to [3, 21]. 7 if none. |
| `short_week_home`, `short_week_away` | 1 if rest days ≤ 6, else 0. |
| `away_travel_proxy` | 0.2 same division, 0.6 same conference otherwise, 1.0 cross-conference, 0 if either team is unknown. |
| `weather_class_code` | 0 indoor roof, else 1. |
| `weather_is_bad` | 0. |
| `qb_change_proxy_*` | `min(1, abs(off_epa - off_epa_recency) * 1.2)`. |
| `offense_continuity_*` | `max(0, 1 - min(1, abs(off_epa - off_epa_recency)))`. |
| `ol_injury_proxy_*` | 0.25 if `week <= 2`, else 0.1. Not an injury feed. |
| `is_division_game` | 1 when conference and division match. |
| `week_number` | Schedule week. Known before kickoff. |

## Trainer (one spec, no grid)

Seed `20261026`. `n_jobs = 1`, `tree_method = hist`.

Shared tree settings, taken from the existing `train()` call and not searched: `n_estimators = 260`, `max_depth = 4`, `learning_rate = 0.05`, `subsample = 0.85`, `colsample_bytree = 0.85`, `min_child_weight = 1`, `reg_lambda = 1`.

- Spread: `XGBRegressor`, `objective = reg:squarederror`, `random_state = 20261026`.
- Win probability: `XGBClassifier`, `objective = binary:logistic`, `eval_metric = logloss`, `random_state = 20261026`.
- Platt: 300 epochs, learning rate 0.08, fit only when the training fold has at least 20 rows. Otherwise identity.
- Sample weight: recency half-life 6 on `season * 100 + week`, times `1 + 0.15 * short_week_home + 0.10 * short_week_away + 0.08 * weather_is_bad + 0.10 * qb_change_proxy_home + 0.10 * qb_change_proxy_away`. No market term. `weather_is_bad` is 0, so that term does not move the weight. The coefficient stays so the formula is not quietly retuned.

Versions: `ml-spread-raw-v1`, `ml-win-raw-v1`, feature set `ml-blind-features-v1`.

## Walk-forward (specified, not run)

Regular season only (`game_type == REG`). Graded seasons 2016 through 2025 inclusive. For test season `S`, fit on REG games with `2012 <= season < S` and both scores present. Season 2011 may inform 2012 features through the date cutoff. Do not refit `S` after seeing `S`. No hyperparameter grid and no second path.

Playoffs are excluded from the fit and from every grade. 2026 is not on this path.

Grading uses the unrounded margin and the unrounded win probability. API rounding is display only.

## Scoring

Sigma is 13.5 for the candidate margin, the incumbent margin, and a reported close-as-point-forecast. It is not tuned. CRPS is `prediction_dist.crps_normal`. Lower is better.

Paired margin gain on game `i`:

`d_i = CRPS(incumbent_i) - CRPS(candidate_i)`

Positive `d` means the candidate is better. The CRPS sample is every graded REG game with both scores and both predictions. A missing close does not drop CRPS.

Bootstrap, for every paired gain below: 10,000 resamples of games with replacement, `numpy.random.default_rng(20261026)`, 90% interval = 5th and 95th percentiles of the bootstrap means (`numpy.percentile`).

### Margin CRPS

- No-worse gate: mean(`d`) ≥ 0.
- A "beats" sentence is allowed only when the 5th percentile of the bootstrap means is > 0.
- The 90% interval is reported either way.

### Win probability

`y = home_win` as defined above.

- Brier gain = incumbent Brier − candidate Brier. No-worse when the mean gain ≥ 0.
- Log loss uses `prediction_dist.log_loss`. Same no-worse rule.
- "Beats" for either score only when that gain's 5th percentile is > 0.
- ECE is `task_modeling_service.expected_calibration_error` with 10 bins, on the test seasons. Reported. Not a gate.

### ATS versus the close, and close non-inferiority

The close is nflverse `spread_line` as a home margin. Positive means the home team is favored. It is not negated. Grading calls `closing_line.ats_grade(model_margin, close, actual)`:

- The model takes home when its margin is greater than the close, and away when it is smaller.
- Exact equality of model and close is not a bet.
- Exact equality of the actual margin and the close is a push and is not a win or a loss.

The comparison set is the graded games where both models have a bet (neither returned "no bet" or "push"). On that set:

`cover_diff = 1` if the candidate covers and the incumbent does not, `-1` if the reverse, `0` if they agree.

`ATS_candidate - ATS_incumbent = mean(cover_diff)`.

Close non-inferiority gate: that difference ≥ −0.005 (not worse by more than 0.5 percentage points). This is the point estimate. The 90% bootstrap interval of `mean(cover_diff)` is reported and is not an extra pass/fail rule.

### Ship rule

SHIP only if all four point-estimate gates pass: CRPS no-worse, Brier no-worse, log loss no-worse, ATS non-inferiority. Otherwise NO-SHIP. Either verdict leaves `include_ml` false. This phase records neither verdict. The script exits unless `NFL_ML_BLIND_EVAL_CONFIRM` is `QA-LOCK`.

## Unresolved questions for QA

These are not extra gates. The rules above stand until QA changes them in a new prereg commit before the look.

1. ATS non-inferiority is the approved 0.5 percentage-point point estimate. The unlocked feature-bundle note used a 1 percentage-point floor on a 90% interval, on a different model and a different play set. Should that interval rule replace the point estimate?
2. Bootstrap length is 10,000, matching the seed's other research notes. PR #41 reported 2,000 resamples and a 95% interval for a different, already spent comparison. Keep 10,000 and 90%?
3. Incumbent margin ignores the live market blend and the conditional sigma. Confirm that `qb-ridge-v5` `predict` margin plus flat 13.5 is the incumbent to grade.
4. Shipped v5 coefficients were fit through later seasons. Confirm they stay frozen rather than walk-forward refit.
5. v5's own `mkt_d` prior uses past closes. It stays inside the incumbent because it is in the deployed model. Confirm the candidate still does not receive it.
6. Booster bytes are not fit in this phase. The frozen artifacts are this file, `TRAIN_SPEC.json`, and the code. Confirm that fitting the ten season models is the start of the single look, after QA lock, and not a second look.
