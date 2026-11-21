# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267. Sims per game: 10000. YPP layer: False.
Runtime per game: 0.528 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the neutral-situation forecast. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 7.122 | 2.431 | 6.927 | 0.881 |
| plays per game | 8.324 | 4.861 | 7.168 | 1.761 |
| drives per team | 1.273 | 0.184 | 1.305 | 0.314 |
| seconds per play | 2.480 | 1.930 | 2.022 | -0.386 |
| plays per drive | 0.760 | 0.160 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.581, 90% 0.704.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 46.377 |
| total sd | 14.753 | 12.705 |
| margin mean | 2.139 | 2.135 |
| margin sd | 14.197 | 13.592 |
| home win rate | 0.592 | 0.559 |
| overtime rate | 0.064 | 0.036 |
| drives per game | 22.367 | 22.736 |
| drives sd | 3.208 | 2.654 |
| share of margin 3 | 0.169 | 0.075 |
| share of margin 7 | 0.094 | 0.074 |

Margin CRPS: 7.316.
Total CRPS: 7.930.
Home-win Brier: 0.217.

These scores are for 2018 only. There is no comparison to the served rating model.

Deviations:
- Coefficients are fit once per week on kickoffs before that week's first game.
- 2018 overtime is one 10-minute period. A touchdown on the first possession wins immediately. Otherwise both teams possess, and the next score wins. A tie stands if the clock expires. A playoff game that is still tied gets one more sudden-death period and then stops.
- Timeouts are a drive-model feature. The sim does not spend them.
- Pressure is qb_hit from play-by-play. Next Gen Stats are not used.
- Scrimmage plays are passes, runs, kneels, and spikes. Punts, kicks, field goals, and extra points take their own runoff.
- Incompletions, spikes, and out-of-bounds plays use the stopped-clock mean. Running-clock plays are shifted so the mixture matches the pace model's seconds per scrimmage play.
- Plays per drive follow the training length of that outcome, plus the team's pace-model offset. The offset uses the opponent's allowed pace, not the offense's own defense.
- The drive-outcome fit is gradient ascent. The previous one-class Newton step, with a penalty of 1, could not reproduce the 2015-17 two-minute end-of-half rate.
- The ridge and softmax fits are numpy.
- Schedule abbreviations are canonicalized to the play-by-play codes (OAK to LV, STL to LA, SD to LAC) for the team join. The drive table keeps the abbreviation on the play.
- The quarterback prior is the pre-game depth-chart quarterback's dropback EPA and CPOE, shrunk to the mean of earlier dropbacks.
