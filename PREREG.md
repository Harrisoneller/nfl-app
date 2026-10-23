# QB-transition ablation preregistration

Weights are frozen in this commit. The 2024–25 holdout and the 2026 confirmation have not been scored. `research/qb_transition/frozen.json` has `holdout_scored: false`. Nothing in this file is a 2024, 2025, or 2026 result.

This is one ablation on the frozen v6-blind model. v6 hyperparameters stay where they were: half-life 14, pass k 8, rush k 10, success k 12, defense weight 0.55, v6 quarterback k 400. Margin sigma stays 13.5. Total sigma stays 10. Publishing stays off.

## Starter, point in time

Pre-2025, the expected starter is the previous game's actual starter, unless the nflverse weekly injury report's last row with `date_modified` at or before kickoff minus 24 hours says Out or Doubtful for that quarterback. In that case the expected starter is the next quarterback on that week's depth chart who is not themselves Out or Doubtful. Questionable and a note do not move the starter.

The incumbent is the starter of the team's most recent completed regular-season or playoff game. Bye weeks are skipped. Preseason is not an incumbent. Week 1 uses the last game of the previous season. A team with no incumbent is not a transition and is not unknowable.

A starter change counts as known only when that report shows it. A side is **UNKNOWABLE** when the actual starter differs from the incumbent and the report did not say Out or Doubtful. If the report did fire and a different quarterback started than the next name on the chart, the change was on the report: the side stays knowable and is scored. A game is excluded from scoring when either side is unknowable.

2025 and 2026 keep the timestamped depth chart cut at kickoff minus 24 hours. Those injury files have no `date_modified`, so they are not applied. A side is unknowable when the actual starter differs from the T−24h depth-chart starter. If there is no snapshot and the actual starter differs from the incumbent, the side is unknowable.

`home_qb_id` and `away_qb_id` are the actual starter. They label unknowable games and, once that game has been played, the incumbent of a later game. They are not the expected starter of the current game.

## Feature and the no-double-count rule

The feature is the expected starter's shrunk EPA per dropback minus the quarterback value already baked into the team pass rating. Shrinkage is toward the draft and experience prior. The feature is applied only on a side that is transitioning. Every other game keeps the v6 number.

On a transitioning side the quarterback piece already inside the v6 prediction is removed and the new weight replaces it:

```
pred_margin = v6_margin
    - pass_coef * (old_dh * home_transition - old_da * away_transition)
    + w_margin * f_margin

pred_total = v6_total
    - total_pass_coef * (old_dh * home_transition + old_da * away_transition)
    + w_total * f_total
```

`pass_coef` and `total_pass_coef` are the coefficients already inside that season's stored v6 prediction: `by_season` for 2016–2023, and the frozen full-sample coefficient for 2024–2026. `old_dh` and `old_da` are the v6 starter deltas at v6's quarterback k of 400. `f_margin` and `f_total` use the ablation k and are zero on a side that is not transitioning.

## How k and the weights were chosen

Chosen on knowable 2016–23 games only.

- Quarterback k grid: 300 and 400. The selection metric is walk-forward margin CRPS. A tie within 1e-6 goes to the higher k.
- Both weights are through-origin least squares, with no intercept. An intercept would shift games the feature does not touch.
- 2015 is not a training row. Its stored v6 margin used the full-sample coefficient, and there is no 2015-only entry in `by_season`.
- The 2016 walk-forward weight is 0. No earlier season is in the training window.
- The weight that will be applied to the holdout is one fit on all knowable 2016–23 games. It is not the average of the walk-forward weights. Those seasonal weights are recorded below so the instability is visible before the holdout is opened. They are not the holdout weight.

| k | Walk-forward margin CRPS | Frozen margin weight | Frozen total weight | Selected |
| --- | --- | --- | --- | --- |
| 300 | 7.3376 | 8.961043 | 36.612236 | yes |
| 400 | 7.3385 | 8.626883 | 35.482641 | no |

Walk-forward margin weights at k 300, the selected k. 2016 is scored at weight 0.

| Season | n | Walk-forward margin weight | Ablation CRPS | v6 CRPS |
| --- | --- | --- | --- | --- |
| 2016 | 230 | 0 | 7.003 | 7.040 |
| 2017 | 221 | −53.843318 | 7.773 | 7.758 |
| 2018 | 228 | −34.469340 | 7.175 | 7.153 |
| 2019 | 221 | −14.945028 | 7.468 | 7.456 |
| 2020 | 224 | −2.603010 | 7.187 | 7.195 |
| 2021 | 228 | −11.320331 | 8.097 | 8.071 |
| 2022 | 233 | 1.962119 | 6.564 | 6.564 |
| 2023 | 230 | 3.207483 | 7.468 | 7.462 |

Of the 2191 scored 2016–23 games that have a close:

- Knowable, and therefore eligible to be scored: 1815
- UNKNOWABLE, excluded from scoring: 376
- Knowable transition games: 102

Feature file SHA-256: `68a4065109b24a587c788d062e317f9672991cd70d2d748b6c93fd114a572957`.

## Bar

Scored once, on the 2024–25 holdout, against the close, on every knowable game. A play is one decided game: pushes (`result` equal to `spread_line`) and games where the prediction equals the close are not plays. The same frozen weights are used. They are not refit.

| Check | Bar |
| --- | --- |
| ATS | ≥ 52.5% on ≥ 400 plays |
| Bootstrap | 90% interval lower bound > 50%. 10,000 resamples of the game-level cover indicator, seed 20241023, 5th and 95th percentiles |
| CLV | > 0 where an opener exists. This snapshot has no opener file, so opener n is 0 and CLV is unavailable. Unavailable is neither a pass nor a fail |
| CRPS | Ablation margin CRPS ≤ v6-blind margin CRPS on the same knowable games. Sigma 13.5 |
| p-value | One-sided binomial test that ATS exceeds 0.50, p < 0.01 |

The ablation passes only if ATS, the play count, the bootstrap bound, CRPS, and the p-value all pass.

Transition games are reported on their own. That slice is not the bar.

2026 is a one-look confirmation on games that have already been played. It is not used to change k or either weight.

If the bar fails, k and both weights stay at the values in this file.
