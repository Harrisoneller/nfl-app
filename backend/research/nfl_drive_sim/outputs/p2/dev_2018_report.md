# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267 (256 regular season + 11 playoffs). Sims per game: 10000. YPP layer: True.
Runtime per game: 0.600 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the simulated scrimmage means. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 6.850 | 0.254 | 6.927 | 0.881 |
| plays per game | 6.802 | 0.508 | 7.168 | 1.761 |
| drives per team | 1.332 | 0.481 | 1.305 | 0.314 |
| seconds per play | 2.089 | 1.066 | 2.022 | -0.386 |
| plays per drive | 0.766 | -0.284 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.854, 90% 0.918.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 45.785 |
| total sd | 14.753 | 13.230 |
| margin mean | 2.139 | 2.195 |
| margin sd | 14.197 | 13.465 |
| home win rate | 0.592 | 0.561 |
| overtime rate | 0.064 | 0.041 |
| drives per game | 22.367 | 23.328 |
| drives sd | 3.208 | 3.412 |
| share of margin 3 | 0.169 | 0.079 |
| share of margin 7 | 0.094 | 0.081 |

Margin CRPS: 7.330.
Total CRPS: 7.930.
Home-win Brier: 0.218.
Total CRPS with the game-average pace oracle: 8.192.
Gap, predicted pace minus that oracle: -0.262.
The oracle is a handicapped benchmark: it uses this game's average
scrimmage pace, scaled across outcomes, and not each drive's own length.
On the previous run the gap was -0.253 with a week-cluster 95% interval
[-0.644, +0.112], so it is not evidence that the predicted pace is better.

These scores are for the 2018 sample only. There is no comparison to the served rating model.

Before (36884a2) versus this run, yards layer as in this file:

| metric | 36884a2 | this run |
| --- | ---: | ---: |
| margin-3 share | 0.076 | 0.079 |
| margin-7 share | 0.076 | 0.081 |
| overtime rate | 0.037 | 0.041 |
| total sd | 13.304 | 13.230 |
| drives per game | 23.457 | 23.328 |
| plays per team MAE | 6.858 | 6.850 |
| plays per game MAE | 6.828 | 6.802 |
| drives per team MAE | 1.353 | 1.332 |
| seconds per play bias | 0.763 | 1.066 |
| coverage 80 | 0.854 | 0.854 |
| coverage 90 | 0.925 | 0.918 |
| margin CRPS | 7.335 | 7.330 |
| total CRPS | 7.925 | 7.930 |
| Brier | 0.218 | 0.218 |

Deviations:
- Coefficients are fit once per week on kickoffs before that week's first game.
- 2018 overtime is 10 minutes. A first-possession touchdown or safety ends it. After both teams have possessed, the next score wins. A regular-season tie stands. A tied playoff starts another period.
- Two-minute endings come from a training table of score, yardline, time left, and timeouts, shrunk toward coarser cells.
- Timeouts are a drive-model feature. The sim does not spend them.
- Pressure is qb_hit from play-by-play. Next Gen Stats are not used.
- Scrimmage plays are passes, runs, kneels, and spikes. Punts, kicks, field goals, and extra points take their own runoff.
- Incompletions, spikes, and out-of-bounds plays use the stopped-clock mean. Running-clock plays are shifted so the mixture matches the pace model's seconds per scrimmage play.
- Plays per drive follow the training length of that outcome, plus the team's pace-model offset. The offset uses the opponent's allowed pace, not the offense's own defense.
- The drive-outcome fit is gradient ascent. The previous one-class Newton step, with a penalty of 1, could not reproduce the 2015-17 two-minute end-of-half rate.
- The ridge and softmax fits are numpy.
- Schedule abbreviations are canonicalized to the play-by-play codes (OAK to LV, STL to LA, SD to LAC) for the team join. The drive table keeps the abbreviation on the play.
- The quarterback prior is the pre-game depth-chart quarterback's dropback EPA and CPOE, shrunk to the mean of earlier dropbacks.
