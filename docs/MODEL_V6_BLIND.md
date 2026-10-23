# v6-blind core

Market-blind team units and a starter QB delta. Graded against the nflverse close.
`spread_line` is positive when the home team is favored, the same direction as home margin.
A home cover is `result > spread_line`. Pushes are left out of ATS.
Margin CRPS uses sigma 13.5 for the model and the close. Totals CRPS uses sigma 10.
Neither sigma was tuned.

## Core bar, 2024–25 vs the close

| Check | Result | Model | Close | Limit |
| --- | --- | --- | --- | --- |
| CRPS within 2% of the close | FAIL | 7.325 | 7.028 | 7.169 |
| RMSE no worse than close + 0.3 | FAIL | 13.075 | 12.485 | 12.785 |
| ATS vs the close ≥ .50 (baseline .490) | FAIL | 0.499 |  | .500 |

The edge on this holdout is not a claim of a significant betting advantage.
Wilson intervals on the buckets below include 0.50 unless a row says otherwise.

## Opener coverage

Usable openers, when a file exists, are 2016 (all weeks) and 2023 from week 7 on.
This snapshot has no opener file. 2024–25 opener n = 0. Nothing is filled in.
2026 DraftKings openers are treated as unreliable and are not shown.
CLV needs an open and a close, so CLV is unavailable.

## Versus the close

### 2016–2023 selection window vs CLOSE (walk-forward coefficients)

| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | MAE | Totals CRPS | Close totals CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2191 | 0.508 | 0.487–0.529 | 7.394 | 7.116 | 13.251 | 12.752 | 10.195 | 7.973 | 7.573 |

| Edge vs close (points) | n | ATS | Wilson 95% |
| --- | --- | --- | --- |
| 0-1 | 505 | 0.501 | 0.458–0.544 |
| 1-2 | 484 | 0.523 | 0.478–0.567 |
| 2-3 | 395 | 0.491 | 0.442–0.540 |
| 3+ | 747 | 0.511 | 0.476–0.547 |

Versus opener: n = 0. CLV unavailable.

### 2024–25 CLEAN HOLDOUT vs CLOSE (frozen 2015–2023 coefficients)

| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | MAE | Totals CRPS | Close totals CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 570 | 0.499 | 0.458–0.540 | 7.325 | 7.028 | 13.075 | 12.485 | 10.162 | 7.577 | 7.311 |

| Edge vs close (points) | n | ATS | Wilson 95% |
| --- | --- | --- | --- |
| 0-1 | 135 | 0.481 | 0.399–0.565 |
| 1-2 | 125 | 0.472 | 0.387–0.559 |
| 2-3 | 115 | 0.548 | 0.457–0.636 |
| 3+ | 190 | 0.500 | 0.430–0.570 |

Versus opener: n = 0. CLV unavailable.

### 2026 LIVE CHECK vs CLOSE (frozen coefficients, scored games only)

| n | ATS | Wilson 95% | Margin CRPS | Close CRPS | RMSE | Close RMSE | MAE | Totals CRPS | Close totals CRPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 94 | 0.534 | 0.431–0.635 | 6.774 | 6.695 | 11.982 | 11.781 | 9.387 | 8.262 | 7.376 |

| Edge vs close (points) | n | ATS | Wilson 95% |
| --- | --- | --- | --- |
| 0-1 | 16 | 0.500 | 0.280–0.720 |
| 1-2 | 24 | 0.542 | 0.351–0.721 |
| 2-3 | 18 | 0.500 | 0.290–0.710 |
| 3+ | 30 | 0.567 | 0.392–0.726 |

Versus opener: n = 0. CLV unavailable.

## Frozen choices (2016–2023 walk-forward margin CRPS)

- Half-life (weeks): 14.0
- Pass shrinkage k (games): 8.0
- Rush shrinkage k (games): 10.0
- Success shrinkage k (games): 12.0
- Defense weight: 0.55
- QB shrinkage k (dropbacks): 400.0
- Selection CRPS: 7.3939

Preseason prior is last season kept at 2/3, then multiplied by
0.5 · roster continuity + 0.5 · week-1 starter's share of last year's dropbacks.
Roster continuity is week-1 roster snaps over last season's offensive snaps.
The prior is the ridge target. Current-season games are not stacked on top of raw prior-season games.

Play gate frozen on 2016–2023 vs the close: None.
The core bar above is every holdout game, not the gated subset.

## Starter

2014–2024: weekly depth-chart QB order, overridden only when an injury row with date_modified at or before kickoff minus 24h has report_status Out. Those depth charts have season and week and no captured_at, so they are not a proven T-24h snapshot. 2025–2026: latest depth chart whose dt is at or before kickoff minus 24h (those files are timestamped). Injury files for 2025–2026 have no date_modified, so they are not applied. gametime is compared as UTC. No home_qb_id. No last-start fallback. Missing chart → QB delta 0.

- Injury `date_modified` non-null rate: 1.000
- Depth-chart timestamp column: True
- Starter assigned: 6599 / 6600
- Missing depth chart (QB delta left at 0): 1
- QB1 replaced because the T-24h report said Out: 171

2014–2024 depth charts have no captured_at, so the weekly file is the proxy for those seasons. 2025–2026 charts are timestamped and are cut at kickoff minus 24 hours. Injury files in 2025–2026 have no date_modified, so they are not applied.

## Snapshot

- Data SHA-256: `539b6fb9a848cbf25c8efceb9b8777caf87a7b8db519a226de166c153138eefc`
- Design SHA-256: `e4aab1dd9a7cc68268a901e6566ef8e9bc1d2c10178e1517d3e3d6e8cca02e5b`

Ablations that are not in this model: QB-transition, luck, wind, offensive line, rest, lookahead, revenge, primetime.

