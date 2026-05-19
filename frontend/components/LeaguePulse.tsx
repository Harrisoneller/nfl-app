"use client";
import Link from "next/link";
import { EloRow, ProjectedDivision } from "@/lib/api";
import { TeamLogo } from "./TeamLogo";

/**
 * "League pulse" — 5 punchy, glanceable stats. Casual users absorb them in
 * seconds; analytics readers verify with a click into the relevant page.
 */
export function LeaguePulse({
  elo, standings,
}: {
  elo: EloRow[];
  standings: ProjectedDivision[];
}) {
  // 1. Best team by Elo
  const topElo = elo[0];

  // 2. Predicted Super Bowl favorite (highest sb_appearance_pct)
  const allTeams = standings.flatMap((d) => d.teams);
  const sbFav = allTeams.length ? [...allTeams].sort((a, b) => b.sb_appearance_pct - a.sb_appearance_pct)[0] : null;

  // 3. Predicted playoff lock (highest playoff_pct ≥ 90% if any)
  const playoffLock = allTeams.filter((t) => t.playoff_pct >= 75).sort((a, b) => b.playoff_pct - a.playoff_pct)[0];

  // 4. Tightest division race (smallest gap between #1 and #2 mean_wins)
  const divisionRaces = standings
    .filter((d) => d.teams.length >= 2)
    .map((d) => ({
      conf: d.conference, div: d.division,
      gap: d.teams[0].mean_wins - d.teams[1].mean_wins,
      top: d.teams[0], second: d.teams[1],
    }))
    .sort((a, b) => a.gap - b.gap);
  const tightestRace = divisionRaces[0];

  // 5. Biggest underdog still alive (lowest sb_pct but >5%, signals a sleeper)
  const sleeper = allTeams
    .filter((t) => t.sb_appearance_pct > 4 && t.sb_appearance_pct < 12)
    .sort((a, b) => a.sb_appearance_pct - b.sb_appearance_pct)[0];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      {topElo && (
        <Stat
          label="Top of the league"
          teamId={topElo.team_id}
          value={`Elo ${Math.round(topElo.rating)}`}
          accent={topElo.grade}
        />
      )}
      {sbFav && (
        <Stat
          label="Super Bowl favorite"
          teamId={sbFav.team_id}
          value={`${sbFav.sb_appearance_pct.toFixed(0)}%`}
          accent={`${sbFav.mean_wins.toFixed(1)}W proj`}
        />
      )}
      {playoffLock && (
        <Stat
          label="Closest to a lock"
          teamId={playoffLock.team_id}
          value={`${playoffLock.playoff_pct.toFixed(0)}% PO`}
          accent={`${playoffLock.mean_wins.toFixed(1)}W`}
        />
      )}
      {tightestRace && (
        <Stat
          label={`${tightestRace.conf} ${tightestRace.div} race`}
          teamId={tightestRace.top.team_id}
          value={`+${tightestRace.gap.toFixed(1)}W`}
          accent={`over ${tightestRace.second.team_id}`}
        />
      )}
      {sleeper && (
        <Stat
          label="Live sleeper"
          teamId={sleeper.team_id}
          value={`${sleeper.sb_appearance_pct.toFixed(1)}% SB`}
          accent="dark horse"
        />
      )}
    </div>
  );
}

function Stat({
  label, teamId, value, accent,
}: { label: string; teamId: string; value: string; accent?: string }) {
  return (
    <Link href={`/teams/${teamId}`} className="pulse-card flex items-center gap-3 group block">
      <span className="pulse-accent" />
      <TeamLogo teamId={teamId} size={36} />
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-muted truncate">{label}</div>
        <div className="font-bold tabular-nums text-base leading-tight">{value}</div>
        {accent && <div className="text-[10px] text-muted truncate">{accent}</div>}
      </div>
    </Link>
  );
}
