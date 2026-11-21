# P6 preregistration

Status: draft

This document is a draft. It does not score a player-game outside the 2018 dev season, and the 2018 numbers below are the spread used for the minimum detectable effect. They are not the P6 result.

## Primary metric

One metric. For each gated player-game-stat, d is the absolute error of the sim mean minus the absolute error of the player-opp-v1 mean. The sim mean is the mean of the frozen draws. The player-opp-v1 mean is `mean_from_quantiles` on that model's quantile grid. Stats are then divided by the 2018 scale in the table below, averaged within the player-game, and the reported number is the mean of those player-game scores.

d uses only these stat lines: attempts, completions, and passing yards for QB; carries and rushing yards for RB; targets, receptions, and receiving yards for RB, WR, and TE. Touchdowns are allocated and reported on the 2018 dev run. They are not in this metric.

A stat line is gated when the player has at least two prior games and the pre-week share clears the player-opp-v1 role floor: attempts share 0.40 for a quarterback, carry share 0.08 for a running back, target share 0.04 for a running back, wide receiver, or tight end.

SUCCESS if the mean d is negative and the 90% upper bound is negative. FAILURE if the 90% lower bound is positive. Otherwise INCONCLUSIVE.

## Interval

The 90% interval is the Webb wild cluster bootstrap in `webb_mean_interval`. Clusters are `(season, week)`. Weights are −√1.5, −1, −√0.5, √0.5, 1, √1.5, each with probability 1/6. 999 replicates. The generator is `numpy.random.default_rng(20261115)`. The 90% bounds are the 5th and 95th percentiles.

## Minimum detectable effect

The spread of d was not computed. No season outside 2018 was scored. Each 2018 absolute sim error is divided by that stat's scale, the scales are the sample standard deviations below, and the player-game score is the mean of those scaled errors. The sample standard deviation of that score is 0.9012058371602234. The design effect on those 6293 player-games is 1.7147017571482344. Assume player-opp-v1 has the same spread and correlation 0 with the sim error. Then sd(d) = 1.274497517401787.

The eligible set has n = 44736. SE = 0.00789050347490249. The normal 90% critical value is 1.6448536269514722. The minimum detectable |mean d| is 0.012978723259166557 scale-units. This is a normal approximation to the Webb interval, not a bootstrap of d. The call does not use the MDE as a second rule.

| stat | 2018 n | scale |
| --- | ---: | ---: |
| attempts | 844 | 7.919426873056551 |
| completions | 844 | 5.333967488229941 |
| passing_yards | 844 | 66.09428612717566 |
| carries | 1368 | 3.8045639106802724 |
| rushing_yards | 1368 | 21.940320338089172 |
| targets | 4944 | 1.9521408590205456 |
| receptions | 4944 | 1.4665992517917101 |
| receiving_yards | 4944 | 21.03790194146438 |

## Eligible player-games

Regular-season player-games whose game is in the frozen primary set, which excludes 2018 and stops at the latest completed game in that set. Position is QB, RB, WR, or TE. The role gate above is already applied. n = 44736. The file is `outputs/p5/eligible_player_games.csv`, sha256 `51b57dc255688ec47bfc27325e327979f20033c4a86f62b1fa6a8eac60adc3e4`. It has no actual stat from the game being pinned.

## Drops and voids

A team-game is dropped when the depth chart and the prior-start quarterback fallback leave it without a quarterback, or without a running back, or when the allocation does not sum to the team total. The prior start is the charted quarterback, not `passer_player_id`. A model failure drops the game. The drop rate is dropped games divided by pinned player-games' games. A rate above 2% voids the run. No SUCCESS, FAILURE, or INCONCLUSIVE call is issued on a void. Failing the role gate is not a drop.

## Walk-forward

For each week, the cutoff is the first regular-season kickoff that week, America/New_York. Shares, efficiencies, team pass rate, and sack rate use only player-games whose kickoff is strictly before that cutoff. An equal kickoff is out. The depth chart is that week's chart. The quarterback fallback is the existing prior-start rule. The sim card is the frozen yards-per-play-off card for that week. `N_SIMS` is 10000. The per-game seed is the frozen `sim_seed`. 2018 is not in this set.

When the comparison is run, `backend/app.services/player_opportunity_model.py` is called through `prepare_weekly`, `player_profiles`, `project`, and `mean_from_quantiles` only. `project` receives the frozen sim's own pre-week mean points and mean margin. It does not receive a line.

## Market-blind

No line, odds, prop, or prediction-market price is an input, a prior, a weight, or a target. These paths are not imported and not called:

- `build_week_model`
- `market_ratings`
- `backtest_v5.py`
- shipped margin and total coefficients
- `player_predictions_service` anchor shift
- `player_props`
- `over_prob`
- any Odds API call

The opponent model's stored team-volume coefficients were fit with a spread and implied points. Those inputs are replaced by the sim's own means. The coefficients are not refit on a line.
