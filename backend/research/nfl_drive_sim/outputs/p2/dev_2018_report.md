# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267. Sims per game: 10000. YPP layer: True.
Runtime per game: 0.586 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the simulated scrimmage means. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 6.858 | 0.331 | 6.927 | 0.881 |
| plays per game | 6.828 | 0.662 | 7.168 | 1.761 |
| drives per team | 1.353 | 0.545 | 1.305 | 0.314 |
| seconds per play | 2.002 | 0.763 | 2.022 | -0.386 |
| plays per drive | 0.772 | -0.307 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.854, 90% 0.925.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 45.896 |
| total sd | 14.753 | 13.304 |
| margin mean | 2.139 | 2.212 |
| margin sd | 14.197 | 13.569 |
| home win rate | 0.592 | 0.560 |
| overtime rate | 0.064 | 0.037 |
| drives per game | 22.367 | 23.457 |
| drives sd | 3.208 | 3.486 |
| share of margin 3 | 0.169 | 0.076 |
| share of margin 7 | 0.094 | 0.076 |

Margin CRPS: 7.335.
Total CRPS: 7.925.
Home-win Brier: 0.218.
Total CRPS with oracle play counts: 8.178.
Total CRPS difference (predicted pace minus oracle pace): -0.253.
The oracle uses this game's scrimmage plays per drive and seconds per play,
scaled across outcomes so a touchdown drive stays longer than a punt.
It does not look at each drive's own length.

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
