# Pre-registration: wind on the total

This file is the holdout contract for one ablation on the frozen v6-blind core.
The form, the threshold, and the slope below were chosen on 2016–23 only.
Nothing in this file is a 2024–25 grade. The holdout is scored once, after
this commit, and it is not used to change the feature.

Publishing stays off. The published fair is unchanged.

## Hypothesis

The wind forecast as of kickoff minus 24 hours lowers the total. That is the
only feature. Precipitation and temperature are not used. There is no margin
term and no pass-rate term.

The prior for this effect is that 15 mph and above is worth about −2 to −5
points on the total. The slope shipped in `dist_model` is 0.22 points removed
per mph above 12. That slope is a reference. It is not the number frozen here.

## Forecast

`research/wind_totals/forecasts.csv`, SHA-256
`1e9e1dc859011979fbe641a6d90e9fccb3eb9d02dd0fa73724441613d97905fa`.

The value is Open-Meteo’s archived JMA GSM forecast,
`wind_speed_10m_previous_day1`, from the historical forecast API, in mph.
The same model is used for every season. The valid hour is the UTC hour of
kickoff (nflverse `gametime` is Eastern). The issue time of that hour is 24
hours earlier, so it is at or before T−24h. A null hour would walk backward to
the latest filled hour at or before kickoff. None of the 3,033 games were null.
Observed weather is not used. `games.csv` columns `temp` and `wind`, and any
`weather_*` column, are not read.

Domes and closed roofs are 0. An open roof keeps the forecast, including
retractable roofs that `weather.py` marks indoor. A missing roof uses that
indoor flag only as a fallback. Neutral sites are 0: `weather.py` has no
coordinates for those stadiums. Coordinates are the current franchise entries
in `weather.py` (`LA`→`LAR`, `OAK`→`LV`, `SD`→`LAC`), which mis-locates the
Los Angeles Coliseum, the Oakland Coliseum, and Qualcomm. That limit is
accepted. See `research/wind_totals/README.md`.

A missing forecast would leave the v6 total unchanged. Outdoor games with a
result had a forecast in both windows: 0 missing in 2016–23 and 0 missing in
2024–25. Those are coverage counts, not grades.

## Feature

`pred_total = v6_pred_total + w * feature`.

`v6_pred_total` is `pred_total` from `backtest/harness_model/model.py`. The
harness is not refit. On the 2016–23 scored games it matches `predictions.csv`
(max absolute gap under 1e-6). Games the feature does not touch keep the v6
total exactly.

The cap is 30 mph, taken from the shipped model and not searched. Applied wind
above 30 is treated as 30 before the transform.

The grid, scored by walk-forward totals CRPS (sigma 10) on the 2,191 games
from 2016–23 that have a result and a close:

| Form | Threshold | Walk-forward CRPS | Frozen slope | Games touched |
| --- | ---: | ---: | ---: | ---: |
| hinge | 10 | 7.9341 | −0.574512 | 348 |
| step | 12 | 7.9359 | −3.589382 | 235 |
| step | 10 | 7.9381 | −2.949311 | 350 |
| hinge | 12 | 7.9431 | −0.702261 | 233 |
| step | 15 | 7.9447 | −4.978986 | 121 |
| hinge | 15 | 7.9618 | −0.844454 | 119 |

Hinge feature = max(0, min(wind, 30) − threshold). Step feature = 1 when
min(wind, 30) is at or above the threshold. The slope is through-origin OLS of
`actual total − v6 total` on that feature, with no intercept, so calm games do
not shift the total. 2015 is not in the fit. The 2016 walk-forward weight is 0
because that season has no prior season in the window. The frozen slope is one
fit on all of 2016–23. It is not the average of the seasonal weights, and the
seasonal weights are not used on the holdout.

Tie-break, if two walk-forward scores were within 1e-6: prefer threshold 12,
then the hinge. The selected hinge at 10 mph leads the next candidate by
0.0018, so the tie-break did not apply.

Selected and frozen:

* form: hinge
* threshold: 10 mph
* slope: −0.574512 points per mph above 10
* walk-forward totals CRPS: 7.9341, against 7.9731 for v6 on the same 2,191 games

At 15 mph the hinge removes 2.87 points, inside the −2 to −5 prior. At 20 mph
it removes 5.75 points, past that prior. The slope was not clipped to the
prior. The shipped 0.22/mph above 12 would remove 0.66 points at 15 mph; that
reference was not the fit.

42 neutral games in 2016–23 are zeroed.

## Bar population

The outdoor wind-affected subset on the 2024–25 scored games is 79 games.
A game is in that subset when it has a result and a close and the hinge is
positive (applied wind strictly above 10 mph). 79 is below 400, so the
pre-registered test is the full totals set: 570 games. The windy subset is
reported beside it and is not the gate. Pushes are removed only when the
plays are counted, not from this 79.

## Holdout bar

One look, 2024–25, totals graded against the close, through the fixed harness,
with the frozen hinge, threshold, and slope.

* Totals ATS (over if `pred_total` is above the close, under if it is below)
  at least 52.5% on at least 400 plays. A play is one game. Pushes
  (`actual` equal to the close) and predictions equal to the close are
  excluded. Equality uses `numpy.isclose`.
* Bootstrap 90% interval on those play indicators, 10,000 draws, seed
  `20241024`, 5th and 95th percentiles. The lower bound must be above 50%.
* CLV above 0 where openers exist. This snapshot has opener n = 0, so CLV is
  unavailable. That is neither a pass nor a fail.
* No totals-CRPS regression against v6-blind on the same games. Sigma is 10
  for both. The ablation CRPS must be less than or equal to the v6 CRPS.
  The close CRPS is reported and is not the gate.
* One-sided binomial test of ATS above 0.50, `p < 0.01`.

The ablation passes only if ATS, the play count, the bootstrap bound, the
CRPS comparison, and the p-value all pass.

2026 is a one-look confirmation after the holdout. It does not move the form,
the threshold, or the slope, and it is not part of the pass/fail.

No retune after the holdout.
