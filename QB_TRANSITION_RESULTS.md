# QB-transition ablation results

Scored once from the frozen file. Weights were not refit on these seasons.

Selected on 2016–23: qb k 300, margin weight 8.961043, total weight 36.612236.
Walk-forward selection CRPS was 7.3376.

UNKNOWABLE scored games excluded: 104 in 2024–25, 18 in 2026.

## Bar, 2024–25 knowable games versus the close

| Check | Result |
| --- | --- |
| ATS ≥ 52.5% on ≥ 400 plays | FAIL |
| bootstrap 90% CI lower bound > 50% | FAIL |
| CLV > 0 where openers exist | UNAVAILABLE |
| no margin-CRPS regression versus v6-blind | PASS |
| one-sided binomial p < 0.01 | FAIL |

Overall: **FAIL**.

CLV is unavailable because this snapshot has no opener file. That item is not counted as a pass or a fail.

### 2024–25 holdout, knowable games

| n | decided | ATS | bootstrap 90% | p (ATS > .50) | Margin CRPS | v6 CRPS | Close CRPS | RMSE | v6 RMSE | Close RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 466 | 462 | 0.489 | 0.450–0.526 | 0.6956 | 7.236 | 7.237 | 6.976 | 12.900 | 12.905 | 12.399 |

Totals CRPS 7.622 versus v6 7.594 and the close 7.373.

CLV unavailable. Opener n = 0.

### 2024–25 transition games only

| n | decided | ATS | bootstrap 90% | p (ATS > .50) | Margin CRPS | v6 CRPS | Close CRPS | RMSE | v6 RMSE | Close RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 45 | 45 | 0.489 | 0.356–0.600 | 0.617 | 8.659 | 8.674 | 7.506 | 15.415 | 15.459 | 13.132 |

Totals CRPS 8.115 versus v6 7.817 and the close 7.955.

CLV unavailable. Opener n = 0.

### 2026 live check, knowable games

| n | decided | ATS | bootstrap 90% | p (ATS > .50) | Margin CRPS | v6 CRPS | Close CRPS | RMSE | v6 RMSE | Close RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 76 | 72 | 0.514 | 0.417–0.611 | 0.4531 | 6.533 | 6.534 | 6.413 | 11.471 | 11.471 | 11.224 |

Totals CRPS 8.300 versus v6 8.302 and the close 7.410.

CLV unavailable. Opener n = 0.

### 2026 transition games only

| n | decided | ATS | bootstrap 90% | p (ATS > .50) | Margin CRPS | v6 CRPS | Close CRPS | RMSE | v6 RMSE | Close RMSE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 12 | 12 | 0.333 | 0.083–0.583 | 0.927 | 8.735 | 8.736 | 7.728 | 14.907 | 14.910 | 13.342 |

Totals CRPS 6.158 versus v6 6.168 and the close 5.328.

CLV unavailable. Opener n = 0.

The holdout was not used to change k or either weight.

