# P5 player layer, 2018 dev

The current player model is `backend/app.services/player_opportunity_model.py`, version `player-opp-v1`.
It projects QB, RB, WR, and TE.
The stat lines are attempts, completions, passing yards, carries, rushing yards,
targets, receptions, and receiving yards, on the quantile grid
0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95.
A market-blind replay is `prepare_weekly`, `player_profiles`, and `project`
with the sim's own mean points and mean margin passed in.
The module does not download odds.
Its team-volume coefficients were fit with a spread and implied points as inputs,
and the service layer can shift quantiles toward a prop anchor.
This run does not call that module, the anchor path, or a prop price.

Fit history is 2015-2017. The only scored season is 2018 regular season.
The yards-per-play layer is off. Player sums match the team totals in each draw.

Games 256, dropped 0, drop rate 0.0.
Reconciliation max abs error 1.1368683772161603e-13.
Gated player-games 6293.

## Share MAE

- attempts: 0.3793202764256813
- carries: 0.20880093866271124
- targets: 0.07007895555423617

## Coverage

The intervals are the sim quantiles. Coverage is well below the nominal 80 and 90 percent. A player's rate is a pre-week constant, so a draw moves only with the simulated play count.

- attempts: n 844, MAE 14.035354502369666, 80% 0.09834123222748815, 90% 0.12677725118483413
- completions: n 844, MAE 9.078207582938388, 80% 0.1066350710900474, 90% 0.13981042654028436
- passing_yards: n 844, MAE 103.3331250586328, 80% 0.07345971563981042, 90% 0.09834123222748815
- carries: n 1368, MAE 5.866554970760235, 80% 0.20614035087719298, 90% 0.260233918128655
- rushing_yards: n 1368, MAE 28.474403548380582, 80% 0.13230994152046785, 90% 0.17616959064327486
- targets: n 4944, MAE 2.389055683656958, 80% 0.2750809061488673, 90% 0.3230177993527508
- receptions: n 4944, MAE 1.6771112459546924, 80% 0.32119741100323623, 90% 0.3800566343042071
- receiving_yards: n 4944, MAE 21.17654191919858, 80% 0.12014563106796117, 90% 0.1595873786407767
