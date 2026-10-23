# Wind on totals

Verdict on the pre-registered 2024–25 bar: **FAIL**.

This is the first holdout look. The form, threshold, and slope were not refit. 2026 is a one-look confirmation and is not part of the bar.

Form `hinge`, threshold 10.0 mph, frozen slope -0.574512 points per feature unit (negative removes points from the total). Walk-forward 2016–23 totals CRPS was 7.9341. Bar population: `full_totals`.

The close CRPS is context. The CRPS gate is against v6-blind on the same games, sigma 10.

## 2024–25 bar

### Full totals set (bar)

| Gate | Value | Result |
| --- | --- | --- |
| Totals ATS | 0.504 (285/566) | False |
| Plays | 566 (games in block 570) | True |
| Bootstrap 90% CI | 0.470–0.539 | False |
| CLV | unavailable (opener n = 0) | n/a |
| Totals CRPS | ablation 7.5600 v6 7.5766 close 7.3110 | True |
| p (one-sided binomial) | 0.4498 | False |

Feature nonzero on 79 games in this block.

### Windy subset (report)

| Gate | Value | Result |
| --- | --- | --- |
| Totals ATS | 0.468 (37/79) | False |
| Plays | 79 (games in block 79) | False |
| Bootstrap 90% CI | 0.380–0.557 | False |
| CLV | unavailable (opener n = 0) | n/a |
| Totals CRPS | ablation 7.0913 v6 7.2112 close 6.7894 | True |
| p (one-sided binomial) | 0.7501 | False |

Feature nonzero on 79 games in this block.

Overall: **FAIL**. The gated block is `holdout_full_totals`.

## 2026 one-look

### Full totals set (report)

| Gate | Value | Result |
| --- | --- | --- |
| Totals ATS | 0.372 (35/94) | False |
| Plays | 94 (games in block 94) | False |
| Bootstrap 90% CI | 0.287–0.457 | False |
| CLV | unavailable (opener n = 0) | n/a |
| Totals CRPS | ablation 8.0920 v6 8.2617 close 7.3764 | True |
| p (one-sided binomial) | 0.9952 | False |

Feature nonzero on 12 games in this block.

### Windy subset (report)

| Gate | Value | Result |
| --- | --- | --- |
| Totals ATS | 0.667 (8/12) | False |
| Plays | 12 (games in block 12) | False |
| Bootstrap 90% CI | 0.417–0.917 | False |
| CLV | unavailable (opener n = 0) | n/a |
| Totals CRPS | ablation 7.3922 v6 8.7211 close 8.8960 | True |
| p (one-sided binomial) | 0.1938 | False |

Feature nonzero on 12 games in this block.

CLV is unavailable because no opener file is in this snapshot. That is neither a pass nor a fail.
