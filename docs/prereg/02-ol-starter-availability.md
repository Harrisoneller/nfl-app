# Preregistration: OL starter availability

Procedure: `docs/prereg/PROTOCOL.md`. This file locks the feature. The windows, walk-forward, CRPS, MDE, and Holm rules in the protocol are part of this preregistration.

Phase 1. No real fit in this commit. The column is not estimated here.

## Endpoint and sign

Endpoint: home margin, `home_score - away_score`.

One column, `ol_d = index_home - index_away`.

For one offense:

`index = snap_weighted_missing_share + 1.0 * center_out`

`snap_weighted_missing_share` is the share of the usual starters' prior offensive snaps that belong to starters listed Out or Doubtful. `center_out` is 1 when the usual center is Out or Doubtful. The weight 1.0 is frozen. It is not a second coefficient.

Expected stage-2 sign: negative. More starters missing on the home side lowers the home margin.

Questionable is available. That choice is fixed.

## Starters

A starter is an offensive lineman with snap share `>= 0.80` over the previous four team-games. Snap share is the player's offensive snaps divided by the team's offensive snaps, pooled over those games. Positions: `C`, `G`, `T`, `OL`, `OT`, `OG`, `LT`, `LG`, `RG`, `RT`. The center flag uses position `C` only. `C/G` is not a center.

The window is the last four team-games the team played before this game, not the last four calendar weeks. A bye does not pull in a fifth game. Games with a missing team snap total are not in the window.

If more than five linemen clear 0.80, keep the five with the highest share. Ties break on player id, ascending. If fewer than five clear 0.80, use those who do. Do not fill the set with non-starters. If a side has no starter, the game is missing for this feature, not zero.

Rolling replacement: a player who stops playing falls out once four team-games have passed without him. He is not held as a starter for the rest of the season, and he is not removed on the first missed game if the prior window still clears 0.80. IR does not need a separate list. While he is still inside the window, an IR or PUP report counts as missing, same as Out.

## Report

Status comes from the final pre-game injury report: the latest row whose `report_time` is strictly before kickoff.

- Out, Doubtful, IR, Injured Reserve, PUP, and Reserve/Injured are missing.
- Questionable, Probable, and any other status are available.
- A player with no injury row is available.
- A row with a null time, or a time at or after kickoff, is not a status. If a starter has injury rows and none of them are strictly before kickoff, the game is unverified and the feature is missing. It is not filled from the late row, and it is not treated as available.
- Timestamps are timezone-naive instants. A timezone-aware value raises. This commit does not guess a conversion.
- Who played the game is not a status. Snaps from the current game are not in the starter window. An Out player who later plays is still Out. A Questionable player who does not play is still available.

## QB-change games

A game is excluded when either team's point-in-time quarterback id differs from that team's id in its previous game. The ids are columns the caller supplies from a pre-kickoff source. They are not `home_qb_id` from the schedule of who played, and they are not inferred from snap counts. The first game in the frame for a team is not a change. An excluded game is missing, not zero.

## Allowlist

Margin base plus `ol_d` only:

`epa_d`, `pts_d`, `st_d`, `qb_d`, `hfa`, `ol_d`

No intercept beyond `hfa`. The center flag is inside `ol_d`. It is not its own column.

## Data

Free sources only, when a later commit is allowed to read them: nflverse snap counts and the injury report (report status and a timestamp that can be checked against kickoff). No paid API. Seasons through 2012 are dropped and are not starter history.

## Screen and confirmatory role

This feature is one of the three in the Holm family. Its exploratory screen, if run, uses the margin sigma 13.5 and the protocol MDE. QB-change games and unverified reports are already missing before that screen. A screen result does not remove this feature from the 2027 weeks 1–4 look.
