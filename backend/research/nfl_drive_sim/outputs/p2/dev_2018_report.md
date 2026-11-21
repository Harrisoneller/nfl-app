# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267. Sims per game: 10000. YPP layer: True.
Runtime per game: 0.236 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the neutral-situation forecast. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 6.991 | -1.662 | 6.927 | 0.881 |
| plays per game | 7.137 | -3.325 | 7.168 | 1.761 |
| drives per team | 2.352 | -2.310 | 1.305 | 0.314 |
| seconds per play | 2.895 | 1.606 | 2.658 | -0.590 |
| plays per drive | 1.529 | 1.466 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.558, 90% 0.760.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 34.730 |
| total sd | 14.753 | 12.443 |
| margin mean | 2.139 | 0.687 |
| margin sd | 14.197 | 12.410 |
| home win rate | 0.592 | 0.516 |
| overtime rate | 0.064 | 0.052 |
| drives per game | 22.367 | 17.747 |
| drives sd | 3.208 | 2.004 |
| share of margin 3 | 0.169 | 0.098 |
| share of margin 7 | 0.094 | 0.106 |

Margin CRPS: 7.622.
Total CRPS: 10.871.
Home-win Brier: 0.233.
Total CRPS with oracle play counts: 8.488.
Total CRPS difference (predicted pace minus oracle pace): 2.383.

These scores are for 2018 only. There is no comparison to the served rating model.

Deviations:
- Coefficients are fit once per week on kickoffs before that week's first game.
- 2018 overtime is one 10-minute period. A touchdown on the first possession wins immediately. Otherwise both teams possess, and the next score wins. A tie stands if the clock expires. A playoff game that is still tied gets one more sudden-death period and then stops.
- Timeouts are a drive-model feature. The sim does not spend them.
- Pressure is qb_hit from play-by-play. Next Gen Stats are not used.
- The ridge and softmax fits are numpy. Seconds per play is the game-clock span of a drive's scrimmage plays divided by plays minus one, then applied to every play.
- Schedule abbreviations are canonicalized to the play-by-play codes (OAK to LV, STL to LA, SD to LAC) for the team join. The drive table keeps the abbreviation on the play.
- The quarterback prior is the pre-game depth-chart quarterback's dropback EPA and CPOE, shrunk to the mean of earlier dropbacks.
