# Preregistration: pass rush vs pass protection

Procedure: `docs/prereg/PROTOCOL.md`. This file locks the feature. The windows, walk-forward, CRPS, MDE, and Holm rules in the protocol are part of this preregistration.

Phase 1. No real fit in this commit. The column is not estimated here.

## Endpoint and sign

Endpoint: home margin, `home_score - away_score`.

One column, `pressure_x`:

`pressure_x = (off_A * def_B) - (off_B * def_A)`

`A` is the home team and `B` is the away team. `off` is the offense's residual pressure-allowed effect. `def` is the defense's pressure effect. The interaction is antisymmetric: swapping home and away flips the sign. It is not mean-centered a second time.

Expected stage-2 sign: negative. A larger value means the home offense is the more pressured side, which should lower the home margin.

Main effects of pressure allowed and pressure rate are not in the design. Level of pressure is already inside `epa_d` and inside `qb_d`. `pass_d` stays out of the margin allowlist as well. This is one interaction, not a search over main effects.

## How the two effects are built

Inputs are team-game counts the caller has already cut to the play filter, plus the quarterback's average time to throw entering that team-game. This module does not download them.

Play filter, applied before the counts exist:

- Dropbacks only: nflverse `qb_dropback == 1`. That includes pass attempts, sacks, and scrambles. It is wider than v5's `pass` flag, which excludes scrambles.
- Win probability on the closed interval [0.10, 0.90]. Missing win probability is out.
- Kneels and spikes are out. Special-teams plays are out when the column is present.
- Seasons before 2018 are out. PFR pressure starts in 2018. Earlier seasons are not imputed from hits or sacks.
- Seasons through 2012 are out.

Season center, point in time. For a team-game in week `w`, subtract the mean raw pressure-allowed rate of team-games in that season with week `< w`. Week 1 uses the previous season's mean rate. If neither exists, the center is missing and the row never enters the ridge. A later week does not revise an earlier center. 2018 week 1 has no 2017 PFR season, so those rows are history for the week-2 center and are not themselves ridge rows.

Residualize the centered offense rate on average time to throw (NGS). On the rows that will enter the ridge for this as-of week, fit `centered ~ 1 + qb_att` by least squares and take the residual. The fit is in-sample on that prior window. It does not use the current game. A row with missing `qb_att` is dropped, not filled. If time to throw has no variance, the slope is 0 and the residual is the demeaned rate. The defense effect is not residualized. `qb_att` must already be the quarterback's average from dropbacks before that team-game. A same-game time to throw is not a column this feature reads.

Opponent-adjust both sides with one ridge on those residuals:

`residual = intercept + offense_i + defense_j`

Equal weight per team-game. No home term. Shrinkage `k = 8` pseudo-games, the heavy end of the 6–8 game range where pressure stabilizes. `k` is not searched. Effects are centered across the teams in the history. A team with no prior team-game is the zero prior. History is team-games strictly before the current `(season, week)`.

The current game's counts are not in its own ridge, its own center, or its own residualization sample.

## Allowlist

Margin base plus `pressure_x` only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`, `pressure_x`

No intercept beyond `hfa`.

## Data

Free sources only, when a later commit is allowed to read them: nflverse/nflreadr play-by-play for the dropback filter, PFR advanced pressure for the 2018+ counts, NGS for average time to throw. No paid API.

A season-total PFR row must not be copied onto every week of that season. The builder requires a week. Expanding a full-season rate backward through that season would use later weeks. If the free file is season-grain, the caller stops and this preregistration is amended before any fit. This commit does not open the file to see its grain.

## Screen and confirmatory role

This feature is one of the three in the Holm family. Its exploratory screen, if run, uses the margin sigma 13.5 and the protocol MDE. A screen result does not remove it from the January 2027 kickoff look. That look is the 2026-season games whose Eastern kickoff is in January 2027, not the 2027 regular season.
