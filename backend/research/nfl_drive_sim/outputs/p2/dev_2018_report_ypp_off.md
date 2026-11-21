# P2 dev report, 2018 only

Fit: walk-forward by week. Coefficients use kickoffs before the week's first game. A later game that week still sees earlier kickoffs from that week in its features.

Games: 267 (256 regular season + 11 playoffs). Sims per game: 10000. YPP layer: False.
Runtime per game: 0.732 seconds (predicted-pace sim only).

Seconds per play and plays per drive are the simulated scrimmage means. Plays per team and drives per team are the mean of the simulations. The baseline is an equal-weight team mean shrunk to the league mean, with no opponent.

| metric | model MAE | model bias | baseline MAE | baseline bias |
| --- | ---: | ---: | ---: | ---: |
| plays per team | 7.437 | -3.375 | 6.927 | 0.881 |
| plays per game | 8.414 | -6.750 | 7.168 | 1.761 |
| drives per team | 1.269 | -0.454 | 1.305 | 0.314 |
| seconds per play | 2.179 | 1.302 | 2.022 | -0.386 |
| plays per drive | 0.742 | -0.149 | 0.737 | -0.067 |

Game-play interval coverage: 80% 0.899, 90% 0.933.

| check | actual | simulated |
| --- | ---: | ---: |
| total mean | 46.551 | 43.170 |
| total sd | 14.753 | 14.122 |
| margin mean | 2.139 | 1.938 |
| margin sd | 14.197 | 13.160 |
| home win rate | 0.592 | 0.555 |
| overtime rate | 0.064 | 0.043 |
| drives per game | 22.367 | 21.458 |
| drives sd | 3.208 | 3.931 |
| share of margin 3 | 0.169 | 0.084 |
| share of margin 7 | 0.094 | 0.087 |

Margin CRPS: 7.337.
Total CRPS: 8.140.
Home-win Brier: 0.218.
The oracle is a handicapped benchmark: it uses this game's average
scrimmage pace, scaled across outcomes, and not each drive's own length.
On the previous run the gap was -0.253 with a week-cluster 95% interval
[-0.644, +0.112], so it is not evidence that the predicted pace is better.

These scores are for the 2018 sample only. There is no comparison to the served rating model.

Before (36884a2) versus this run, yards layer as in this file:

| metric | 36884a2 | this run |
| --- | ---: | ---: |
| margin-3 share | 0.076 | 0.084 |
| margin-7 share | 0.076 | 0.087 |
| overtime rate | 0.037 | 0.043 |
| total sd | 13.304 | 14.122 |
| drives per game | 23.457 | 21.458 |
| plays per team MAE | 6.858 | 7.437 |
| plays per game MAE | 6.828 | 8.414 |
| drives per team MAE | 1.353 | 1.269 |
| seconds per play bias | 0.763 | 1.302 |
| coverage 80 | 0.854 | 0.899 |
| coverage 90 | 0.925 | 0.933 |
| margin CRPS | 7.335 | 7.337 |
| total CRPS | 7.925 | 8.140 |
| Brier | 0.218 | 0.218 |

From yards-on 8ac7801 to this file: drives per game 21.458 (was 23.328), overtime rate 0.043 (was 0.040).

## Disclosures

- 2018 is dev-touched.
- Margin-3 and margin-7 key-number mass is low. There is no fourth-quarter endgame model yet. Later work covers clock-killing on one-score leads, playing for a field goal before the two-minute mark, score-dependent late outcomes, and spending timeouts. That work is accepted on a later season against pre-registered checks.
- Safeties are about 0.87% of drives against 0.17% actual.
- Two-minute drives are short. The causes are truncation, a slow runoff, and timeouts that are never spent.
- Seconds per play bias is about +1.30 (1.302 in dev_2018_report_ypp_off.json).
- Drives per game run about -0.89 (both halves about -0.9, overtime unchanged). The cause is end-of-half correctly ending the half, short two-minute drives (2.49 vs 3.52 plays), and the slow clock.
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
