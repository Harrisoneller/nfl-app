# Study record

Documentation only. The locked evaluation code is commit `44077c965fc4e430f88aa859be8ad2536811554c`. This record does not change that code, the gates, the seed, or the spent calculation. No `RESULTS.json` was written. `include_ml` stays false.

Verdict: **terminal NO-SHIP at selection.** The 2016–2025 sample was not scored. This is not a request to rerun.

## Selection, 2013–2015, versus the close

z is candidate CRPS minus close CRPS. It is not a comparison with v5. Incumbent CRPS was not computed and cannot support a "beats v5" claim.

| Item | Value |
| --- | --- |
| n | 768 |
| n with a close | 768 |
| season-week clusters | 51 |
| z versus the close | −5.218876433529135 |
| reported projected power | 0.9676390443967837 |
| open final | false |

Coverage before selection: `reg_team_games` 7326, `missing_epa_history` 0, `unknown_ids` 0, `has_2011` 1.

z ≥ 2 failed. The reported power cleared 0.5. Both were required, so the z failure is decisive and 2016–2025 was not opened.

The reported power is power at a true difference of zero against the non-inferiority margin (an absolute effect). It is not power to detect a favorable improvement. A future preregistration should use a favorable prespecified minimum effect, or mark power n/a when z < 0. The spent gates and this calculation are not amended.

Close provenance: nflverse `schedules/games.csv` column `spread_line`, defined in `closing_line.py` as the closing home margin. No opener or posted line was substituted.

Stage-1 settings in `team_ratings_v5.DEFAULT_PARAMS` were validated on 2016–2020 and held out 2021–2026 and are not refit. Starter ids are the players who played. Sigma 13.5 is the scoring convention, not the production distribution. Those facts are why a later incumbent-CRPS number, had one existed, would still be descriptive only.

The schedule count of 175 final-season clusters was used only to project power. Those games were not scored.

## Replacement invocation

SHA: `44077c965fc4e430f88aa859be8ad2536811554c`
Started: 2026-11-07T11:38:58Z
Exit: 1
Tracked tree at start: clean

```
cd /agent/repos/nfl-app/backend
NFL_ML_BLIND_LOCKED_SHA=44077c965fc4e430f88aa859be8ad2536811554c PYTHONUNBUFFERED=1 /tmp/nfl-venv/bin/python /opt/cursor/artifacts/run_nfl_ml_blind_44077c9.py
```

The runner is `study/run_nfl_ml_blind_44077c9.py`. It calls locked `run_locked_eval` and prints the coverage and selection return values. It does not change the locked implementation. Log: `study/44077c96-replacement.log`
SHA-256: `9632fa2f19646ed60b1262763bf65daa699e8093a83dddf8054f9ee28810a0db`

## Earlier abort, not a score

`e9130494de29ad10ff8db472142c2e5b7f0ec943` stopped in `assert_prefit_coverage` with `ValueError: 2011 EPA history is required` before any selection statistic. QA classified it as an execution failure before statistical exposure. Log: `study/e9130494-abort.log`
SHA-256: `6e2df3aef82f06d685803d4fc797108e5391a323700dc905edcd68a32960d9fc`

```
cd /agent/repos/nfl-app/backend
NFL_ML_BLIND_LOCKED_SHA=e9130494de29ad10ff8db472142c2e5b7f0ec943 PYTHONUNBUFFERED=1 /tmp/nfl-venv/bin/python -m scripts.eval_nfl_ml_blind
```

Exit 1. No selection z, power, n, or cluster count was computed on that run.
