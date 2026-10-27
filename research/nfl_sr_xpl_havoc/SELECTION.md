# Selection, 2016–23

Verdict: **NO-SHIP**.

The 2024–25 holdout was not fit and was not scored. `n_h` = 544 is the count of played regular-season games in those seasons with a closing line. It is the power sample size only.

Paired difference is CRPS(v5-blind) minus CRPS(augmented), n = 2095.

- mean 0.003340
- SD 0.571714
- paired z 0.2674
- MDE at 80% holdout power 0.068673 CRPS
- projected holdout power 0.0341

Go required paired z >= 2 and projected power >= 0.50. The bar was not cleared, so the verdict is NO-SHIP and the holdout stays unspent.

| Model | 2016–23 REG CRPS |
| --- | ---: |
| v5-blind | 7.335753 |
| augmented | 7.332413 |
| live v5 | 7.246554 |

Descriptive paired z versus live v5, CRPS(live v5) minus CRPS(augmented): -3.5924. That number is not the go/no-go.

Feature audit on the selection games: success SD 0.037586, explosive SD 0.012652, havoc SD 0.022551.

Preregistration file SHA-256 `9281fb0b4bcb56d4b092b64eeebf9863fc550f7c6eaa4399a9628b945ab83b93`.
defs.py commit `b68470fa18119e7b443c13cdf2278bcadebfd686`.
