"use client";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "./Card";
import { TeamLogo } from "./TeamLogo";

/**
 * Last 5 completed games for a team. W/L badge + score + opponent.
 * Reads from team_remaining_schedule which already has played-vs-unplayed
 * marked + outcomes.
 */
export function RecentFormCard({ teamId }: { teamId: string }) {
  const { data, isLoading } = useSWR(
    ["team-remaining-schedule", teamId],
    () => api.teamRemainingSchedule(teamId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Recent form">
        <p className="text-sm text-muted">Loading…</p>
      </Card>
    );
  }

  const played = data.games.filter((g) => g.played).reverse().slice(0, 5);

  if (played.length === 0) {
    return (
      <Card title="Recent form">
        <p className="text-sm text-muted">
          No completed games yet this season. Form will appear once games are played.
        </p>
      </Card>
    );
  }

  // Compute current streak
  const streakOutcome = played[0]?.outcome;
  let streakLen = 0;
  for (const g of played) {
    if (g.outcome === streakOutcome) streakLen += 1;
    else break;
  }

  return (
    <Card title="Recent form" action={
      streakOutcome && streakLen > 1 && (
        <span className="text-xs">
          <span
            className="font-bold"
            style={{ color: streakOutcome === "W" ? "#22c55e" : streakOutcome === "L" ? "#ef4444" : "#94a3b8" }}
          >
            {streakLen} {streakOutcome === "W" ? "W" : streakOutcome === "L" ? "L" : "T"} streak
          </span>
        </span>
      )
    }>
      <ul className="space-y-2 text-sm">
        {played.map((g) => (
          <li key={g.id || `${g.week}-${g.opponent}`} className="flex items-center gap-3">
            <OutcomeBadge outcome={g.outcome} />
            <span className="text-muted text-xs w-12">Wk {g.week ?? "—"}</span>
            <span className="text-muted">{g.is_home ? "vs" : "@"}</span>
            <TeamLogo teamId={g.opponent} size={20} />
            <Link href={`/teams/${g.opponent}`} className="hover:underline font-medium flex-1">
              {g.opponent}
            </Link>
            <span className="tabular-nums">
              {g.my_score}-{g.opp_score}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function OutcomeBadge({ outcome }: { outcome: "W" | "L" | "T" | null }) {
  const color = outcome === "W" ? "#22c55e" : outcome === "L" ? "#ef4444" : "#94a3b8";
  return (
    <span
      className="inline-flex items-center justify-center w-6 h-6 rounded text-xs font-bold"
      style={{ background: `${color}25`, color }}
    >
      {outcome ?? "—"}
    </span>
  );
}
