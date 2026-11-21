# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267 (256 regular season + 11 playoffs). Sims per game: 10000. YPP layer: True.
Runtime per game: 0.687 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the simulated scrimmage means. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 7.421 | -3.352 | 6.927 | 0.881 |
| plays per game | 8.392 | -6.704 | 7.168 | 1.761 |
| drives per team | 1.268 | -0.444 | 1.305 | 0.314 |
| seconds per play | 2.174 | 1.294 | 2.022 | -0.386 |
| plays per drive | 0.741 | -0.152 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.895, 90% 0.929.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 43.087 |
| total sd | 14.753 | 14.086 |
| margin mean | 2.139 | 2.112 |
| margin sd | 14.197 | 13.117 |
| home win rate | 0.592 | 0.559 |
| overtime rate | 0.064 | 0.043 |
| drives per game | 22.367 | 21.478 |
| drives sd | 3.208 | 3.927 |
| share of margin 3 | 0.169 | 0.084 |
| share of margin 7 | 0.094 | 0.086 |

Margin CRPS: 7.333.
Total CRPS: 8.147.
Home-win Brier: 0.218.
Total CRPS with the game-average pace oracle: 8.021.
Gap, predicted pace minus that oracle: 0.126.
The oracle is a handicapped benchmark: it uses this game's average
scrimmage pace, scaled across outcomes, and not each drive's own length.
On the previous run the gap was -0.253 with a week-cluster 95% interval
[-0.644, +0.112], so it is not evidence that the predicted pace is better.

These scores are for the 2018 sample only. There is no comparison to the served rating model.

Before (36884a2) versus this run, yards layer as in this file:

| metric | 36884a2 | this run |
| --- | ---: | ---: |
| margin-3 share | 0.076 | 0.084 |
| margin-7 share | 0.076 | 0.086 |
| overtime rate | 0.037 | 0.043 |
| total sd | 13.304 | 14.086 |
| drives per game | 23.457 | 21.478 |
| plays per team MAE | 6.858 | 7.421 |
| plays per game MAE | 6.828 | 8.392 |
| drives per team MAE | 1.353 | 1.268 |
| seconds per play bias | 0.763 | 1.294 |
| coverage 80 | 0.854 | 0.895 |
| coverage 90 | 0.925 | 0.929 |
| margin CRPS | 7.335 | 7.333 |
| total CRPS | 7.925 | 8.147 |
| Brier | 0.218 | 0.218 |

From yards-on 8ac7801 to this file: drives per game 21.478 (was 23.328), overtime rate 0.043 (was 0.040).

## Disclosures

- 2018 is dev-touched.
- Margin-3 and margin-7 key-number mass is low. There is no fourth-quarter endgame model yet. Later work covers clock-killing on one-score leads, playing for a field goal before the two-minute mark, score-dependent late outcomes, and spending timeouts. That work is accepted on a later season against pre-registered checks.
- Safeties are about 0.87% of drives against 0.17% actual.
- Two-minute drives are short. The causes are truncation, a slow runoff, and timeouts that are never spent.
- Seconds per play bias is about +1.07.
- The overtime gap comes from how often regulation ends tied.
- Predicted pace versus the game-average oracle is not significant. The oracle is a handicapped benchmark.
- P3 must not be used as a fair ATS or key-number comparison against v5 until an endgame model is accepted.

## Note for P3

P3 is not started. When it is, pre-register margin-3 share, margin-7 share, the regulation-tie rate, the overtime-entry rate, and seconds-per-play bias as tracked metrics, and commit a margin-at-5:00-left series in the P3 outputs.

Deviations:
- Coefficients are fit once per week on kickoffs before that week's first game.
- 2018 overtime is 10 minutes. A first-possession touchdown or safety ends it. After both teams have possessed, the next score that breaks the tie wins. A field goal that only ties the game does not. A regular-season tie stands. A tied playoff starts another period.
- An end-of-half outcome expires the rest of that half. The second half then starts with a kickoff. In the fourth quarter or overtime it ends the game, or overtime if the score is tied.
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
