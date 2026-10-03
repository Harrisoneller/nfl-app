# Model v5 — QB-aware game ratings + median-first player projections

Written 2026-10-01. Replaces `epa-elo-ctx-v4` (games) and upgrades
`player-proj-v2` (players). Every number below is **out of sample**: walk-forward,
each prediction built only from data available before kickoff, scored against
results and against nflverse closing lines. Reproduce the game-model numbers
with `python -m scripts.backtest_v5`.

## TL;DR

| | v4 (shipped) | v5 | closing line |
|---|---|---|---|
| Margin RMSE, 2021–26 | 13.11 | **12.95** | 12.67 |
| Margin MAE, 2021–26 | 10.19 | **10.06** | 9.78 |
| Correlation with closing spread | 0.857 | **0.912** | — |
| Slope vs closing spread (1.0 = right scale) | 0.72 | **0.90** | — |
| Total RMSE, 2021–26 | 13.57 | **13.36** | 13.12 |
| Total correlation with closing total | 0.648 | **0.824** | — |

**What this does and does not mean.** v5 is a materially better *projection*:
its numbers track the market's scale and ordering much more closely, and its
disagreements are smaller and better grounded. It is **not** a closing-line
beater on NFL sides or totals. Against 2021–26 closes, ATS was ~48–51% at
every disagreement threshold, and the realized share of a model-vs-close
disagreement (regressing `result − line` on `model − line`) was ≈0. For
2017–20 it was 0.27 (ATS 54.5% at 2+ points, t≈2.7) — the market has since
absorbed box-score and QB information. The product now says so (see "Edge
honesty" below).

Where real edge plausibly lives, in order:
1. **Player props.** Books are much softer there, and the biggest error in the old
   pipeline was distributional (mean vs median), not informational.
2. **Early lines / CLV.** A model that agrees with the *close* is valuable
   against the *open*. We have no historical openers, so this can't be
   backtested yet. `odds_snapshots` already accumulates line history. Re-run the realization fit against openers once a season is stored.
3. **Information the close hasn't priced yet**: late QB news (the v5 QB layer
   prices a starter change the moment the schedule or injury feed shows it),
   and wind.

## Production bugs found along the way (all fixed)

These hurt live accuracy more than any modelling choice:

1. **Player stats stuck in 2024.** `nfl_data_py.import_weekly_data` and
   `import_seasonal_data` read `player_stats/player_stats_{y}.parquet`.
   nflverse stopped publishing that file after 2024, so 2025 and 2026 return
   404. The adapter swallowed the error and `player_gamelog` walked back to
   2024: `/players/{id}/gamelog?season=2026` served the player's **2024**
   games. → `adapters/data/nflverse_stats.py` reads
   `stats_player/stats_player_week_{y}.parquet` and maps it to the legacy
   schema. The seasonal frame is rebuilt with nfl_data_py's exact share math.
2. **Live-season PBP never loaded.** `import_pbp_data` defaults to
   `include_participation=True` and treats a missing participation file as "no
   season". 2026 participation isn't published, so every in-season PBP load
   returned nothing, and the board ran on **2025** EPA aggregates.
   → `_import_pbp_lean` reads the release parquet directly. The library is now a
   fallback, with participation off.
3. **In-season player games ignored.** `obs_season = season if season <=
   latest_completed_season()` is False for the entire live season, so
   projections were pure priors until February. Defense factors were pinned to
   last season. → `_season_has_weekly()` / `_defense_season()`.
4. **Elo never saw the current season.** Rebuilds ran `latest-5 … latest`
   (completed seasons only). → now run through `current_or_upcoming_season()`
   (scheduler, rerun service, admin route). Elo is now only a fallback and
   season-sim input, but it should still be right.

## Game model: `qb-ridge-v5`

`services/team_ratings_v5.py` holds the pure math, shared with the backtest
script. `services/game_model_v5_service.py` holds the orchestration.

1. **Team-game rows from PBP.** Scrimmage snaps only; kneels and spikes
   dropped. Snaps outside 10–90% win probability are weighted 0.35, and
   4th-quarter blowouts (≥17) 0.20. EPA is winsorized at ±4.5. Special-teams EPA
   is kept separately. The primary passer is recorded per team-game. Rows are
   artifact-cached per season (30 days for completed seasons, 3 hours for the
   live season).
2. **One decayed ridge across three seasons**, instead of season-to-date plus a
   bolted-on prior. Weight = `0.5^(age_weeks/10) · 0.6^(season_gap)`. The
   penalty is in pseudo-games and shrinks toward league average. Metrics:
   EPA/play, pass EPA/dropback, points/game, ST EPA/game, plays/game.
   Opponent adjustment comes from fitting every offense jointly with every
   defense it faced.
3. **QB layer.** Each QB is rated on his own opponent-adjusted pass EPA per
   dropback (half-life 40 weeks), shrunk with 250 pseudo-dropbacks toward a
   prior that **slides with career volume**: replacement level (−0.10) at 0
   dropbacks, league average at 600+. The model prices
   `rating(this week's starter) − dropback-weighted rating of the QBs whose
   snaps built the team's numbers`, at ~35 pts per EPA/db. Starters come from
   nflverse `games.csv` (`home_qb_id`/`away_qb_id`). This one term is the
   largest single gain: correlation with the close went 0.85 → 0.90.
4. **Market prior.** Power ratings least-squares-fit to **previous** games'
   closing spreads (half-life 6 weeks), never the line being predicted. A test
   enforces this. Worth ~0.04 RMSE. Set `v5.coef_market_prior = 0` for a fully
   independent model.
5. **Stage 2.** OLS of final margin on the feature differences, and of final
   total on the feature sums plus the league scoring environment. Fit on
   2015–2026 wk3. HFA fits to **1.8 pts**, not v4's 2.2.

**Context layer interplay.** If v5 sees a starter change for a team, the context
layer's `qb` component for that team is dropped so the change isn't charged
twice. If v5 sees no change (for example, the schedule still lists the injured
starter), the injury-feed QB component stays.

**Rollback:** `game.v5_enabled = 0` in Admin → Parameters. The whole board
returns to v4 instantly. Any v5 data failure also falls back to v4 per request.

Hyperparameter validation (2016–20 tune, 2021–26 holdout) was flat across
half-life 6–16, carry 0.4–0.8, ridge 2–10 and the QB params. The defaults sit
in that plateau, so retuning them is unlikely to move results.

## Edge honesty

- `market.w_base` 0.30 → **0.80**, `w_per_source` 0.10 → **0.02**, `w_cap`
  0.85 → **0.92**. The walk-forward optimal model share against closing lines
  was ~10% (2017–26) and ~0% (2021–26). Note: if these params have DB
  overrides in prod, the overrides still win. Check Admin → Parameters.
- `pred.edge` gains `spread_expected_pts` / `total_expected_pts` = raw
  disagreement × `market.edge_beta` (0.10) / `edge_beta_total` (0.0). The
  frontend chip now reads "Model: KC +4.0 (exp 0.4)" with an explanatory
  tooltip.

## Player model: `player-opp-v1` (on top of `player-proj-v2`)

`services/player_opportunity_model.py`, wired through
`_project_stat_for_game(..., opp=...)`.

- **Opportunity × efficiency.** Team volume (targets, carries, pass attempts) is
  a regression on the team EWMA, expected margin and implied points; the game
  model drives it, so favourites run and trailing teams throw. Player volume is
  the player's EWMA share (half-lives: targets 4, carries 2, attempts 3 games).
  Efficiency (yards per target/carry/attempt, catch rate, completion rate) uses
  the last 24 games, shrunk toward the position average.
- **Median-first.** Linear quantile regressions at 11 levels map the structural
  projection to a full distribution. `predicted` is the **median**, and `mean`
  (for fantasy) is integrated from the quantile function. P(over) interpolates
  the fitted CDF (`engine.projection_over_prob`), and every prop surface uses it.
- **Defense-vs-position adjustments were tested and dropped.** An opponent-
  adjusted residual version added nothing out of sample (rec yds MAE 23.03 with
  vs 22.97 without). Matchup grades stay informational only.
- Falls back to the v2 posterior path for TDs/INTs, players with fewer than 2
  games or below the role floor, and players with an admin input lever (the
  admin's hand on usage wins).

Out-of-sample, 2021–26 (same rows for every model; v4 replica given *correct*
data, which prod did not have):

| stat | v2 as shipped: MAE / P(actual > proj) | v2 recalibrated to median | **opp-v1 median** |
|---|---|---|---|
| receiving yds | 24.05 / 0.40 | 23.25 / 0.51 | **23.03 / 0.50** |
| receptions | 1.72 / 0.42 | 1.69 / 0.50 | **1.68 / 0.49** |
| rushing yds | 27.32 / 0.41 | 26.46 / 0.51 | **25.84 / 0.50** |
| passing yds | 67.21 / 0.44 | 65.18 / 0.48 | **65.41 / 0.48** |

"P(actual > proj) = 0.40" means v2's shipped number sat above the median, so a
prop priced off it leaned *over* on 60/40 coin flips. Calibration on 2025–26:
80% intervals cover 80–84% and medians split 45–51% (integer stats tie).

## Not done / next

- Season Monte Carlo still runs on Elo. Next step: feed it
  `game_model_v5_service.predict_any`.
- Fit `market.edge_beta` against **opening** lines once a season of
  `odds_snapshots` exists. That is the test that matters for a betting product.
- The opportunity model's role gates use EWMA shares. A depth-chart change
  (injury return) lags by a game or two. Feed `context/availability` into the
  shares.
- Refit coefficients each offseason: `python -m scripts.backtest_v5`.
