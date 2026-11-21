# P3 report

P3 is not a fair ATS or key-number comparison against v5 until a fourth-quarter endgame model is accepted on a later season.

Primary decision: FAILURE.
Yards-per-play layer kept: False.
Primary games: 2534. Dropped: 0.
Drops by reason: {'missing_qb': 0, 'model_failure': 0}.
Secondary scores are a Holm family. They do not flip the primary call.
Playoffs are descriptive. 2018 cannot choose the layer.

Mean margin-CRPS d: 0.143408.
Margin-3 share, actual: 0.143.
Margin-7 share, actual: 0.084.
Regulation-tie rate, actual: 0.004.
Overtime-entry rate, sim: 0.042.
Seconds per play, sim mean: 27.219.
Margin at 5:00, sim mean: 1.358.
Flag rate, margin: 0.427.
Flag rate, total: 0.151.
Flag rate, home-win probability: 0.310.

The game-average pace oracle is a handicapped benchmark.
A gap whose interval covers zero is not a win for the predicted pace.
Continuous CRPS is a sensitivity check. It does not change the primary call.

Primary mean margin-CRPS d 0.143408, 90% CI [0.076919, 0.207401].
With-2018 sensitivity mean margin-CRPS d 0.130593, 90% CI [0.075397, 0.191837]. It does not change the primary call.
Playoffs are descriptive only: 133 games, mean margin-CRPS d 0.114505.
Tracked margin-3 share: actual 0.143, sim 0.085, gap -0.057.
Tracked margin-7 share: actual 0.084, sim 0.086.
Tracked seconds-per-play bias: 1.455.
Tracked regulation-tie rate: 0.004.
Tracked overtime-entry rate: 0.042.
Tracked margin at 5:00: 1.358.
Continuous-CRPS sensitivity, sim sample minus v5 normal: margin 0.142, total -0.227. It does not change the call.
Per-game sim context is the hashed week card pickle plus the pre-game team row. It is reproducible from those pickles, the derived play extract, and the hashed depth charts. It is not a separate fitted artifact.
Yards-per-play ablation, on minus off, on the primary regular-season set: margin-CRPS mean d -0.000369, 90% CI [-0.011843, 0.011292]; total-CRPS mean d -0.000144, 90% CI [-0.010447, 0.010344]. The margin upper bound is not below zero, so the layer is not kept.
