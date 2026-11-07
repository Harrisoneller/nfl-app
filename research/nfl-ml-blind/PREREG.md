# NFL ML market-blind retrain — lock candidate

Status: awaiting QA re-audit. This file is not permission to fit or score. The lock commit is the git commit that contains this tree. Its SHA is not written into the source. A later run must be given that SHA from outside the repo.

SHIP, if the gates below pass, means eligible for further QA and Harrison review only. It does not enable `include_ml`, and it does not authorize a merge or a deploy. The ML layer stays off.

Harrison approved these five recommendations on 2026-11-07: walk-forward v5 incumbent, close-CRPS 90% upper bound ≤ +0.25, ATS guard 90% lower bound ≥ −2.0 percentage points, keep z ≥ 2 and power ≥ 0.5, Platt inside training folds only.

## Production, and what is not the gate

Railway service `nfl-app`, deployment `2d608399-7172-4007-874e-17c8ed19bfa5`, is `main` at `389498b7a060c8e62619b3369515f14ac00b72ff`. `include_ml` defaults to false. No Railway variable turns it on. The served model name is `qb-ridge-v5`.

The served board is not the incumbent used for the gates:

- Production applies conditional sigma when `dist.conditional_sigma_enabled` is on (the code default is on) and blends the headline number to the live consensus when that consensus exists.
- Flat sigma 13.5 is only the scoring convention for CRPS and for turning a margin into a reported win probability. It is not the production distribution.
- The shipped stage-2 coefficients in `team_ratings_v5.MARGIN_COEFS` were fit on 2015–2026 week 3. Replaying those frozen coefficients on 2016–2025 is not an honest historical walk-forward. That replay may be stored later as a descriptive column. It is not the gate incumbent.
- `home_qb_id` / `away_qb_id` on the nflverse schedule are the starters who played. They are hindsight, not a T−24h depth chart. The incumbent still uses them because that is how the v5 feature row is built. Games dropped because a team is missing from the week model, and games scored with the no-market coefficient vector, are counted when the script eventually runs. Those counts are not known in this commit.

## Point-in-time features

A result on date `d` may enter a result-derived feature of a target on date `D` only when `d <= D - 2 days`. Games on `D` and on `D - 1` are out. Games on `D - 2` or earlier are in. This is not a ban on every same-week result: a Monday game is three days before Thursday, so Monday is eligible for Thursday. Week-1 self-inclusion is out. If a team has no game on or before the cutoff, EPA is 0. It is never the same-week mean or a full-season mean.

Season carry for Elo is applied for the target season before that row is read. The season opener has no new-season result to ingest, so the carry cannot wait for one. Carry is 0.75 toward 1500, once per season boundary. K = 20 and home-field = 55 are hardcoded.

Rest uses the schedule only (`d < D`). Roof is indoor or not. Realized wind, temperature, and precipitation are not features. Market columns are not features. The allowlist and the trainer constants are `spec_document()` and `TRAIN_SPEC.json`.

Pre-fit, and only after the external SHA matches, the script asserts: 2011 schedule and EPA history are present, every team id is known, and every REG 2012–2025 team-game has EPA from a game on or before its cutoff. That assertion is implemented and tested on synthetic fixtures. It is not run on the real seasons in this commit.

## Targets

Spread target: `home_score - away_score`. Win target: 1 if that margin is positive, else 0. Ties are 0. No market residual and no market probability label.

Platt is fit only on inner-season out-of-fold probabilities. For training seasons sorted `S1 < S2 < ... < Sk`, each `Si` with `i > 1` is predicted by a win model fit on `{S1, ..., S(i-1)}` only. `S1` is not a calibration row. The calibration season is not in its own training fold. Evaluation-season predictions are not used. In-sample probabilities from the model fit on all training rows are not used. The win model that is later applied to a test season is fit on all of that season's training rows; the Platt map applied to it was not fit on those in-sample probabilities.

## Samples that do not overlap

Final evaluation seasons: regular season 2016 through 2025. These seasons are not a selection sample.

Selection seasons: regular season 2013, 2014, and 2015. For selection season `C`, fit on regular seasons with `2012 <= season < C`. Season 2012 is training history for the 2013 fold, not a selection grade, because it has no earlier season inside the training floor. Season 2011 is feature history only.

A selection row must not be a 2016–2025 game. A final-evaluation row must not be a 2013–2015 game. The script checks both before it writes.

## Design conflict, reported rather than waived

The original one-look treated all of 2016–2025 as the graded sample and 2012–2015 as training history inside every later fit. The selection window above does not grade 2016–2025. Those selection games do remain inside the training window of every final-evaluation season. The decision to open 2016–2025 is therefore informed by games that later train the final models. There is no earlier walk-forward fold: 2012 has no prior training season under this floor.

Splitting 2016–2025 into selection and holdout would consume the intended final sample. That split is not used. z ≥ 2 and power ≥ 0.5 are not waived. If QA rejects the training-set overlap, this design cannot host both an untouched 2016–2025 holdout and a prior selection sample.

## Selection rule, before the final sample is scored

On the 2013–2015 rows only, let `d` be candidate CRPS minus close CRPS, sigma 13.5. Cluster on `(season, week)`.

- Cluster-robust SE of the mean: with cluster totals `T_c`, sizes `n_c`, `n` games, `C` clusters, and mean `m`, `SE = sqrt(C / (C - 1) * sum (T_c - n_c * m)^2) / n`. `C` must be at least 2.
- `z = (0.25 - mean(d)) / SE`. Require `z >= 2`.
- Project the final-sample SE as `SE_final = SE * sqrt(C_selection / C_final)`. `C_final` is the number of distinct regular-season `(season, week)` keys in 2016–2025 on the schedule. It is not a model result and it is not computed in this commit.
- Projected power at a true difference of zero is `Phi(0.25 / SE_final - 1.6448536269514722)`. Require power ≥ 0.5.
- The minimum detectable margin is 0.25 CRPS points. It was not estimated from outcomes.

If either floor fails, the script stops. It does not score 2016–2025 and it does not write `RESULTS.json`.

## Final-evaluation gates

Bootstrap: 10,000 resamples, seed `20261026`, `numpy.random.default_rng`. The gate interval is the season-week cluster bootstrap. Its 90% interval is the 5th and 95th percentiles of the resampled means. A game-level bootstrap may be stored and is descriptive only.

Close non-inferiority, hard no-ship: on regular-season games with a close, `d = CRPS(candidate) - CRPS(close)`. The 95th percentile of the cluster-bootstrap mean must be ≤ 0.25. If it is above 0.25, the verdict is NO-SHIP regardless of ATS.

ATS guard: `closing_line.ats_grade`. The close is nflverse `spread_line` as a home margin (positive means the home team is favored). It is not negated. Pushes and exact model-equals-close no-bets are out. The comparison is games where both the candidate and the walk-forward incumbent have a bet. The difference is candidate cover minus incumbent cover. The 5th percentile of the cluster-bootstrap mean must be ≥ −2.0 percentage points (−0.02 in rate units).

The incumbent margin is the per-season stage-2 OLS refit. For season `S`, the design is fit on rows with `season < S` only. Features are the v5 margin features, including `mkt_d` when the week model built it from earlier closes. The game's own close is not a regressor. Rows without `mkt_d` use a second refit that omits `mkt_d`. Frozen production coefficients are not this fit.

Brier, log loss, ECE, and CRPS against the incumbent are reported. They are not extra ship gates.

A passing verdict is the token SHIP with `enables_ml` false and `merge_authorized` false. It means review only.

## Fit guard

`fit_market_blind_models`, `POST /admin/ml/train`, the ML backtest compute path, and `backend/scripts/eval_nfl_ml_blind.py` refuse unless all of the following hold:

- An externally supplied 40-character SHA (`NFL_ML_BLIND_LOCKED_SHA` for the script, `locked_sha` for the routes).
- That SHA equals `git rev-parse HEAD`.
- `git status --porcelain --untracked-files=no` is empty.
- `TRAIN_SPEC.json` equals `spec_document()`.
- `research/nfl-ml-blind/RESULTS.json` does not exist. The writer refuses if it does, and it does not overwrite.

The run records SHA-256 of the schedule file, of each play-by-play parquet, and of the code and spec snapshot. None of that is computed in this commit.
