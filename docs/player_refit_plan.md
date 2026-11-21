# Player refit plan

Status: scope only. No fit, no backtest, no scoring run. The decision rule
lives in `backend/research/player_refit/PREREG.md` (draft, unlocked). This
note says what the eventual run is allowed to change and what it is not
allowed to touch.

## Output

The refit publishes the same object as `player-opp-v1`:

- Positions QB, RB, WR, TE.
- Stats: attempts, completions, passing yards, carries, rushing yards,
  targets, receptions, receiving yards.
- The same 11 quantile levels: 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60,
  0.70, 0.80, 0.90, 0.95.
- Headline is the median. The mean is the integral of the quantile function.
  There is no prop anchor and no market prior anywhere in that object.

## Team volume

Replace the shipped regression

```text
a + b·EWMA + c·spread_team + d·imp_pts
```

with a market-blind regression locked in the prereg:

```text
a + b·team EWMA + c·team points (v5, no market) + d·opponent points (v5, no market)
```

Team points and opponent points come from `MARGIN_COEFS_NO_MKT` and
`TOTAL_COEFS` in `backend/app/services/team_ratings_v5.py`. Margin is
`team − opponent`. The total's `plays_s` term is the pace feature. The
`epa_s` / `epa_d` terms are the efficiency features. They enter only through
that no-market score. `market_ratings`, `mkt_d`, and `v5.coef_market_prior`
are not inputs.

Player volume stays the shipped structure: pre-week EWMA share times that
team volume. Efficiency stays the shipped 24-game shrink toward the position
average. Half-lives, role gates, and the 11 quantile levels are not retuned.
Quantile coefficients are refit, walk-forward, on the new structural hat.
They are not copied from `QUANTILE_COEFS`, because those were fit on a hat
that included the market columns.

## Incumbent

The comparison is against `player-opp-v1` as shipped. It is not refit. Its
team-volume fit window is 2016–2025 and its quantile fit window is 2016–2024,
so it is partly in-sample on the scored seasons. A refit win still counts.
A refit loss or an inconclusive interval does not count as evidence that the
refit is worse.

The refit is walk-forward by week. Its team points come from the no-market
`v5_replay.py` on `research/nfl-drive-sim` at
`0c49a45c9b90b6b4635ed248bc271effbe13abfd`. Live v5, which carries `mkt_d`
0.669, is the incumbent's input, not the refit's. The prop-anchor shift is
off on the incumbent arm: the repo has the snapshot table and its timestamps,
and no historical pre-kickoff rows. The live site can still shift a week
that has snapshots. That difference is disclosed in the prereg. No Odds API
call.

The eligible set is materialized. It is the pre-kickoff skill roster, the
same depth-chart rule as the quarterback rule, with the prior charted player
when the chart is missing. n = 70094. sha256 of
`backend/research/player_refit/eligible/eligible_keys.txt` is
`ccfbb2bee685f3f4e6f3bfb416ed9dd71b195716a63f2c2cc2eb66927c688d23`.
The decision rule, the drop rule, and the frozen hyperparameter hash are in
the prereg. The prereg is still a draft.

## What this branch does not do

- No coefficient files, no fitted artifacts, no backtest output.
- No edit to `player_opportunity_model.py` or the prediction service. The
  production path stays wrong until a later change that the prereg has locked.
- No deploy, no Railway change, no environment change.
- No work on `research/nfl-drive-sim` or `feature/sim-panel`.

## Cost of the eventual run

Planning estimate, not a measurement. Nothing in this run was timed.

The scored set is about 150 week clusters (the prereg's season rule). Each
week refits 3 team-volume OLS models (a few thousand team-games, 4 columns)
and 88 one-predictor quantile regressions (8 stats × 11 levels) on the
pre-week player rows. In numpy that is a few seconds per week. The v5
no-market week model is the same order of work as `backend/scripts/backtest_v5.py`,
whose header budgets 2–3 minutes for the game-model walk-forward.

| | |
|---|---|
| Expected | 20–40 CPU-minutes, one core, under 2 GB |
| Stop | 4 CPU-hours. A statsmodels `QuantReg` loop can land near 2–3 hours (150 weeks × ~1 minute). That is the slow implementation, not a second experiment. |
| Bootstrap | Under a second once week scores are aggregated. 9999 Webb replicates on ~150 clusters. |
| Money | $0. Public nflverse schedules and player weeks only. No Odds API, no other paid call. |
| GPU | None. |

The 4-hour stop is a ceiling on a single implementation. Hitting it is a
reason to switch solvers, not a reason to shrink the season rule or the
bootstrap.
