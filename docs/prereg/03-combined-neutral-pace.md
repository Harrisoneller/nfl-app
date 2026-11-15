# Preregistration: combined neutral pace

Procedure: `docs/prereg/PROTOCOL.md`. This file locks the feature. The windows, walk-forward, CRPS, MDE, and Holm rules in the protocol are part of this preregistration.

Phase 1. No real fit in this commit. The column is not estimated here.

## Endpoint and sign

Endpoint: total points, `home_score + away_score`.

One column, `neutral_pace_sum`: the home offense's prior neutral seconds per play plus the away offense's.

The unit is seconds. Smaller is faster. Expected stage-2 sign: negative. Faster combined pace, fewer seconds per play, means a higher total.

The column is not multiplied by −1 to force a positive coefficient.

## What v5 already has

v5's total equation already includes `plays_s`, the sum of both sides' opponent-adjusted plays per game. That is a volume term. It is not neutral seconds per play. v4 computes `off_neutral_sec_per_play` in `epa_adjust_service.context_metrics`. The v5 prediction path does not use that adjustment.

This test adds `neutral_pace_sum` on top of the total allowlist, which still contains `plays_s`. It does not replace `plays_s`. It does not add weather, wind, roof, or dome. Those columns are not read. Two frames that differ only in weather produce the same feature.

## Neutral snap

A snap counts only when all of the following hold:

- Quarter is 1, 2, or 3. The fourth quarter and overtime are out.
- Absolute score differential is at most 7. Missing differential is out.
- Win probability is on the closed interval [0.20, 0.80]. Missing win probability is out.
- Outside the last 2:00 of each half. The last two minutes of the first half are second-quarter plays with `half_seconds_remaining <= 120` (or `quarter_seconds_remaining` when the half clock is absent). A missing clock on a second-quarter play is out. The last two minutes of the second half are in the fourth quarter, already out. End-of-quarter plays in the first and third quarters stay in. This is a half cut, not a quarter cut.
- Scrimmage: `pass == 1` or `rush == 1`. Kneels, spikes, and special teams are out.
- Seasons through 2012 are out.

Seconds per play are the drop in `game_seconds_remaining` between consecutive snaps in the same game, offense, and drive, ordered so the clock runs down. Keep deltas in `(0, 40]` seconds. The cap is the 40-second play clock. A longer gap is a timeout, injury, or other stoppage. v4's 60-second cap is not used. The cap is not searched.

## Team pace, point in time

A team's pace for a game is the mean of its neutral deltas from prior games only: the current season's earlier weeks, plus the previous two seasons (`seasons_back` on the frozen v5 window). The current game is not included. A later week does not change an earlier feature.

If either offense has fewer than 30 such deltas, the feature is missing. It is not filled with a league mean.

## Allowlist

Total base, an intercept, and `neutral_pace_sum`:

`pts_s`, `epa_s`, `env2`, `plays_s`, `qb_s`, `neutral_pace_sum`, plus `const`.

## Data

Free sources only, when a later commit is allowed to read them: nflverse/nflreadr play-by-play (`game_seconds_remaining`, quarter, score, win probability, drive, pass/rush flags). No paid API. No weather feed.

## Screen and confirmatory role

This feature is one of the three in the Holm family. Its exploratory screen, if run, uses the total sigma 10 and the protocol MDE. A screen result does not remove it from the 2027 weeks 1–4 look.
