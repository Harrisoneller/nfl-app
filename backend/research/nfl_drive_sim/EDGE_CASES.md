# Drive-table edge cases

P1 is a market-blind drive table. It does not simulate, fit a model, or read
served rating coefficients. Schedule and play-by-play files from nflverse
contain `spread_line`, `total_line`, moneylines, and `vegas_wp`. Those columns
are dropped before a frame is cached or returned.

## Clock

`gameday` and `gametime` are an America/New_York wall clock. A missing
`gametime` is filled with 13:00 Eastern, the nflverse default. An aware
timestamp is converted to Eastern before it is compared with the cutoff.
A game that kicks off at or after 2027-01-01 00:00 Eastern is not loaded.
Seasons outside 2015–2026 are not loaded. Naive timestamps are not accepted
by the pre-game row filter; only the schedule parser treats a naive value as
an Eastern wall clock.

Pre-game rows are those whose kickoff is strictly earlier in Eastern time.
The target game, a game with the same kickoff, and any later game are excluded.

## Pre-game quarterback

Weekly depth charts through 2024 have no publication timestamp. The QB1
(`position` QB, `depth_team` 1) for that season, week, team, and game type is
treated as known one hour before kickoff. The 2025 and 2026 file is a series
of chart snapshots with a `dt`. The QB1 (`pos_abb` QB, `pos_rank` 1) is taken
from the latest snapshot whose `dt` is strictly before kickoff, and that `dt`
is the as-of time. A snapshot at or after kickoff is not used. Either stamp
is checked with the strict pre-game assert. The passer who played is not used.
Injury `date_modified` is cached and is not used here, because a report can be
revised after the game.

Schedule rows for the St. Louis Rams, San Diego Chargers, and Oakland Raiders
still say STL, SD, and OAK. Play-by-play uses LA, LAC, and LV. The home flag
crosswalks those codes. The drive keeps the abbreviation that was on the play.

`drive_first_passer_id` on a drive is the in-game first passer, or the rusher
when the drive is a kneel or a rush with no pass. It is not a pre-game input.
Pre-game helpers return only `PREGAME_COLUMNS`.

## What an offensive drive is

Plays that share a `fixed_drive` are one series. A kickoff is usually filed on
the receiving team's drive, so kickoff plays are removed from the start state,
the play count, and the yards. A series with no scrimmage play and no safety
is not an offensive drive.

- **Kneel.** A terminal `qb_kneel` is `END_OF_HALF`. Yards are the kneel yards.
  The starting quarterback is the rusher. Clock expiration at the end of a
  half, the end of regulation, or an overtime period uses the same label.
  nflverse calls the last of those "End of half" or "End of game"; both are
  `END_OF_HALF`.
- **Onside kick.** nflverse does not publish `onside_kick`.
  `own_kickoff_recovery` is 1 when the kicking team recovers the kick. The
  kick itself is not a drive. The snaps after the recovery are an ordinary
  offensive drive, and the start state is the first of those snaps.
- **Kickoff return touchdown.** The return and its extra point share a
  `fixed_drive` and there is no scrimmage snap. That series is not an
  offensive drive. The points are non-offensive points for the returning team,
  including the extra point or two-point try.
- **Defensive touchdown.** A pick-six is `TURNOVER`. A punt return, a blocked
  punt returned for a score, and a missed or blocked field goal returned for a
  score stay `PUNT` or `FG_MISSED`. The points, including the extra point, go
  to `defteam_points`. The extra point often flips `posteam`; points are taken
  from the home and away score, not from that flipped label.
- **Safety.** A safety outranks a punt or field-goal attempt on the same play,
  including a penalty enforced in the end zone (`play_type` `no_play`). Two
  points go to the defense.
- **Penalties only.** A series of accepted penalties with no scrimmage snap and
  no safety is `OTHER`. If that series is how the half expires, it is
  `END_OF_HALF`.
- **Overtime.** Quarter 5 and later stay in the table. A score in overtime uses
  the scoring label. A period that expires without one is `END_OF_HALF`.

## Scores

`total_home_score` on a timeout, and on null play-type rows such as "END GAME",
often still shows the previous score. Those rows do not change the running
score. A `no_play` row may add a penalty safety, but it may not lower the
score: a few games have an old `no_play` filed after the final snap with an
earlier total. A real play whose drive number is earlier than a drive already
seen, or that dips and a later row brings the score back, is mis-ordered. A
dip on a real play that never comes back is a reversed score and is kept.
A kickoff-return touchdown can have a penalty and the extra point on that
same drive number; those points stay non-offensive.

Drive points are the repaired points on the snaps, the extra point, and the
timeouts in that `fixed_drive`. Reconciliation adds those to the non-offensive
points and compares both teams with the schedule's final score. A game that
does not match is written to `reconciliation_mismatches.csv` and kept in the
drive table.

## Play clock and yards

`seconds_elapsed` prefers `drive_time_of_possession`. If that string is
missing, it is the game clock at the first scrimmage snap minus the game clock
at the last one, which does not include the last snap's duration. Yards are
pass, rush, kneel, and penalty yards. Punt and kickoff return yards are not
credited to the offense.
