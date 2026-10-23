# v6-blind loss diagnostics versus the close

Diagnostics of the frozen v6-blind predictions in `backtest/harness_model/priors/predictions.csv`.
No hyperparameter moved. Closing lines are the grading target only.

ATS drops pushes (`result` equal to `spread_line`) and games where the prediction equals the close.
A home cover is `result > spread_line`. Excess CRPS is `n * (model CRPS − close CRPS)` and is the concentration measure.
Injury files for 2025–2026 have no `date_modified` and are not in these windows.

## 2016–2023 selection window

Margin sigma is 13.5 for the model and the close. A positive gap means the model is worse.

### Week

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1-4 | 507 | 7.467 | 7.153 | +0.314 | 13.500 | 12.928 | +0.572 | 0.516 | 0.472–0.560 | +159.2 |
| 5-9 | 568 | 7.259 | 7.138 | +0.121 | 12.932 | 12.738 | +0.194 | 0.531 | 0.489–0.572 | +68.7 |
| 10-18 | 1020 | 7.494 | 7.121 | +0.373 | 13.419 | 12.741 | +0.678 | 0.490 | 0.459–0.522 | +380.1 |
| playoffs | 96 | 6.743 | 6.741 | +0.001 | 11.912 | 11.991 | -0.080 | 0.510 | 0.412–0.608 | +0.1 |

Largest excess CRPS in this cut: **10-18** (+380.1 point-games, CRPS gap +0.373, n=1020).

### QB change or Out/Doubtful, versus a normal QB week

A game is in the QB-event slice when either team started a different quarterback than in its previous game, or a QB on that team had a Friday report status of Out or Doubtful with `date_modified` at or before kickoff minus 24 hours. This slice describes where the frozen model lost. It is not a feature.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 1565 | 7.331 | 7.123 | +0.208 | 13.115 | 12.735 | +0.380 | 0.504 | 0.479–0.529 | +325.8 |
| QB change or Out/Doubtful | 626 | 7.551 | 7.100 | +0.451 | 13.584 | 12.793 | +0.790 | 0.516 | 0.477–0.556 | +282.4 |

Largest excess CRPS in this cut: **normal** (+325.8 point-games, CRPS gap +0.208, n=1565).

The same games split into the pieces of that union:

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 1565 | 7.331 | 7.123 | +0.208 | 13.115 | 12.735 | +0.380 | 0.504 | 0.479–0.529 | +325.8 |
| change only | 329 | 7.651 | 7.154 | +0.497 | 13.876 | 12.969 | +0.907 | 0.542 | 0.487–0.596 | +163.5 |
| Out/Doubtful only | 150 | 7.217 | 6.779 | +0.439 | 12.736 | 11.997 | +0.740 | 0.466 | 0.388–0.546 | +65.8 |
| change and Out/Doubtful | 147 | 7.667 | 7.306 | +0.361 | 13.758 | 13.180 | +0.578 | 0.510 | 0.430–0.590 | +53.1 |

Largest excess CRPS in this cut: **normal** (+325.8 point-games, CRPS gap +0.208, n=1565).

### Home favorite versus home dog

`spread_line` is positive when the home team is favored.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| home favorite | 1376 | 7.112 | 6.904 | +0.208 | 12.751 | 12.359 | +0.392 | 0.522 | 0.495–0.549 | +286.3 |
| home dog | 811 | 7.870 | 7.473 | +0.397 | 14.055 | 13.388 | +0.668 | 0.485 | 0.450–0.520 | +321.6 |
| pick'em | 4 | 7.968 | 7.888 | +0.080 | 13.607 | 13.546 | +0.061 | 0.250 | 0.046–0.699 | +0.3 |

Largest excess CRPS in this cut: **home dog** (+321.6 point-games, CRPS gap +0.397, n=811).

### Division games

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| division | 780 | 7.457 | 7.057 | +0.400 | 13.289 | 12.579 | +0.710 | 0.484 | 0.448–0.519 | +311.9 |
| non-division | 1411 | 7.359 | 7.149 | +0.210 | 13.229 | 12.846 | +0.383 | 0.521 | 0.495–0.548 | +296.3 |

Largest excess CRPS in this cut: **division** (+311.9 point-games, CRPS gap +0.400, n=780).

### Spread size

Cuts sit on the key numbers 3 and 7. They were fixed before these tables were read.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| small |close| < 3 | 499 | 7.240 | 7.058 | +0.183 | 13.016 | 12.674 | +0.342 | 0.486 | 0.442–0.530 | +91.2 |
| mid 3 <= |close| < 7 | 1049 | 7.356 | 7.134 | +0.223 | 13.206 | 12.827 | +0.380 | 0.510 | 0.479–0.541 | +233.5 |
| large |close| >= 7 | 643 | 7.574 | 7.133 | +0.441 | 13.501 | 12.690 | +0.811 | 0.522 | 0.482–0.561 | +283.6 |

Largest excess CRPS in this cut: **large |close| >= 7** (+283.6 point-games, CRPS gap +0.441, n=643).

### Sides versus totals

Sides are the home margin. Totals use sigma 10. The ATS column on the total row is the over-hit rate: bet over when the model total is above the close.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sides (margin) | 2191 | 7.394 | 7.116 | +0.278 | 13.251 | 12.752 | +0.499 | 0.508 | 0.487–0.529 | +608.2 |
| totals | 2191 | 7.973 | 7.573 | +0.400 | 13.880 | 13.302 | +0.578 | 0.499 | 0.478–0.520 | +877.1 |

Sides excess CRPS +608.2. Totals excess CRPS +877.1.

## 2024–25 holdout

Margin sigma is 13.5 for the model and the close. A positive gap means the model is worse.

### Week

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1-4 | 128 | 7.382 | 7.338 | +0.044 | 13.299 | 13.245 | +0.054 | 0.496 | 0.411–0.582 | +5.6 |
| 5-9 | 145 | 7.538 | 7.105 | +0.433 | 13.441 | 12.579 | +0.862 | 0.500 | 0.419–0.581 | +62.7 |
| 10-18 | 271 | 7.128 | 6.790 | +0.339 | 12.659 | 11.962 | +0.696 | 0.496 | 0.437–0.556 | +91.8 |
| playoffs | 26 | 7.911 | 7.566 | +0.344 | 14.126 | 13.404 | +0.722 | 0.538 | 0.355–0.712 | +9.0 |

Largest excess CRPS in this cut: **10-18** (+91.8 point-games, CRPS gap +0.339, n=271).

### QB change or Out/Doubtful, versus a normal QB week

A game is in the QB-event slice when either team started a different quarterback than in its previous game, or a QB on that team had a Friday report status of Out or Doubtful with `date_modified` at or before kickoff minus 24 hours. This slice describes where the frozen model lost. It is not a feature.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 432 | 6.920 | 6.798 | +0.122 | 12.293 | 12.099 | +0.194 | 0.488 | 0.441–0.536 | +52.7 |
| QB change or Out/Doubtful | 138 | 8.592 | 7.749 | +0.843 | 15.267 | 13.622 | +1.645 | 0.533 | 0.450–0.614 | +116.4 |

Largest excess CRPS in this cut: **QB change or Out/Doubtful** (+116.4 point-games, CRPS gap +0.843, n=138).

The same games split into the pieces of that union:

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | 432 | 6.920 | 6.798 | +0.122 | 12.293 | 12.099 | +0.194 | 0.488 | 0.441–0.536 | +52.7 |
| change only | 116 | 7.765 | 7.367 | +0.398 | 13.864 | 12.995 | +0.869 | 0.583 | 0.491–0.669 | +46.2 |
| Out/Doubtful only | 10 | 13.425 | 10.801 | +2.624 | 21.391 | 17.970 | +3.421 | 0.200 | 0.057–0.510 | +26.2 |
| change and Out/Doubtful | 12 | 12.564 | 8.900 | +3.665 | 20.997 | 15.243 | +5.754 | 0.333 | 0.138–0.609 | +44.0 |

Largest excess CRPS in this cut: **normal** (+52.7 point-games, CRPS gap +0.122, n=432).

### Home favorite versus home dog

`spread_line` is positive when the home team is favored.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| home favorite | 335 | 7.355 | 7.121 | +0.234 | 13.092 | 12.608 | +0.484 | 0.511 | 0.457–0.564 | +78.4 |
| home dog | 235 | 7.283 | 6.897 | +0.386 | 13.051 | 12.308 | +0.743 | 0.483 | 0.420–0.547 | +90.7 |
| pick'em | 0 |

Largest excess CRPS in this cut: **home dog** (+90.7 point-games, CRPS gap +0.386, n=235).

### Division games

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| division | 197 | 7.063 | 6.602 | +0.461 | 12.648 | 11.733 | +0.916 | 0.503 | 0.433–0.572 | +90.8 |
| non-division | 373 | 7.464 | 7.254 | +0.210 | 13.295 | 12.865 | +0.430 | 0.497 | 0.447–0.548 | +78.3 |

Largest excess CRPS in this cut: **division** (+90.8 point-games, CRPS gap +0.461, n=197).

### Spread size

Cuts sit on the key numbers 3 and 7. They were fixed before these tables were read.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| small |close| < 3 | 140 | 7.126 | 6.967 | +0.160 | 12.862 | 12.427 | +0.435 | 0.436 | 0.356–0.518 | +22.4 |
| mid 3 <= |close| < 7 | 280 | 7.058 | 6.787 | +0.271 | 12.626 | 12.136 | +0.490 | 0.509 | 0.450–0.568 | +75.8 |
| large |close| >= 7 | 150 | 8.009 | 7.536 | +0.473 | 14.060 | 13.162 | +0.898 | 0.540 | 0.460–0.618 | +70.9 |

Largest excess CRPS in this cut: **mid 3 <= |close| < 7** (+75.8 point-games, CRPS gap +0.271, n=280).

### Sides versus totals

Sides are the home margin. Totals use sigma 10. The ATS column on the total row is the over-hit rate: bet over when the model total is above the close.

| Slice | n | Model CRPS | Close CRPS | CRPS gap | Model RMSE | Close RMSE | RMSE gap | ATS | Wilson 95% | Excess CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sides (margin) | 570 | 7.325 | 7.028 | +0.297 | 13.075 | 12.485 | +0.590 | 0.499 | 0.458–0.540 | +169.1 |
| totals | 570 | 7.577 | 7.311 | +0.266 | 13.340 | 12.916 | +0.424 | 0.508 | 0.467–0.549 | +151.4 |

Sides excess CRPS +169.1. Totals excess CRPS +151.4.

## Where the gap sits

Excess CRPS weights the per-game gap by sample size. A wide per-game gap in a small slice and a modest gap in a large slice can both matter. Both are below.

**2016–2023.**

- Weeks 10–18: n=1020, CRPS gap +0.373, RMSE gap +0.678, ATS 0.490, excess CRPS +380.1
- Weeks 5–9: n=568, CRPS gap +0.121, RMSE gap +0.194, ATS 0.531, excess CRPS +68.7
- Weeks 1–4: n=507, CRPS gap +0.314, RMSE gap +0.572, ATS 0.516, excess CRPS +159.2
- Playoffs: n=96, CRPS gap +0.001, RMSE gap -0.080, ATS 0.510, excess CRPS +0.1
- QB event: n=626, CRPS gap +0.451, RMSE gap +0.790, ATS 0.516, excess CRPS +282.4
- Normal QB week: n=1565, CRPS gap +0.208, RMSE gap +0.380, ATS 0.504, excess CRPS +325.8
- Home dog: n=811, CRPS gap +0.397, RMSE gap +0.668, ATS 0.485, excess CRPS +321.6
- Home favorite: n=1376, CRPS gap +0.208, RMSE gap +0.392, ATS 0.522, excess CRPS +286.3
- Division: n=780, CRPS gap +0.400, RMSE gap +0.710, ATS 0.484, excess CRPS +311.9
- Non-division: n=1411, CRPS gap +0.210, RMSE gap +0.383, ATS 0.521, excess CRPS +296.3
- Large close: n=643, CRPS gap +0.441, RMSE gap +0.811, ATS 0.522, excess CRPS +283.6
- Small close: n=499, CRPS gap +0.183, RMSE gap +0.342, ATS 0.486, excess CRPS +91.2

**2024–25.**

- Weeks 10–18: n=271, CRPS gap +0.339, RMSE gap +0.696, ATS 0.496, excess CRPS +91.8
- Weeks 5–9: n=145, CRPS gap +0.433, RMSE gap +0.862, ATS 0.500, excess CRPS +62.7
- Weeks 1–4: n=128, CRPS gap +0.044, RMSE gap +0.054, ATS 0.496, excess CRPS +5.6
- Playoffs: n=26, CRPS gap +0.344, RMSE gap +0.722, ATS 0.538, excess CRPS +9.0
- QB event: n=138, CRPS gap +0.843, RMSE gap +1.645, ATS 0.533, excess CRPS +116.4
- Normal QB week: n=432, CRPS gap +0.122, RMSE gap +0.194, ATS 0.488, excess CRPS +52.7
- Home dog: n=235, CRPS gap +0.386, RMSE gap +0.743, ATS 0.483, excess CRPS +90.7
- Home favorite: n=335, CRPS gap +0.234, RMSE gap +0.484, ATS 0.511, excess CRPS +78.4
- Division: n=197, CRPS gap +0.461, RMSE gap +0.916, ATS 0.503, excess CRPS +90.8
- Non-division: n=373, CRPS gap +0.210, RMSE gap +0.430, ATS 0.497, excess CRPS +78.3
- Large close: n=150, CRPS gap +0.473, RMSE gap +0.898, ATS 0.540, excess CRPS +70.9
- Small close: n=140, CRPS gap +0.160, RMSE gap +0.435, ATS 0.436, excess CRPS +22.4

On the holdout the widest per-game margin gap is the QB-event slice, and that slice also holds most of the excess CRPS. Home dogs, division games, and large closes have wider per-game gaps than their complements. Weeks 5–9 have a wider per-game gap than weeks 10–18; weeks 10–18 still contribute the most excess CRPS among week buckets. Weeks 1–4 sit near the close. Totals and sides both trail the close. The selection window has the same directional shape: a QB change, a home dog, a division game, and a large close have wider per-game gaps than their complements, and weeks 10–18 carry the most excess CRPS.

