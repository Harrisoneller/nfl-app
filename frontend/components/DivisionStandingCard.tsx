"use client";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "./Card";
import { TeamLogo } from "./TeamLogo";

/**
 * Where does this team sit in their division? Pulls projected standings
 * (already pre-warmed for the current season) and highlights this team.
 */
export function DivisionStandingCard({ teamId }: { teamId: string }) {
  const { data } = useSWR(["projected-standings"], () => api.projectedStandings());

  if (!data || data.divisions.length === 0) {
    return (
      <Card title="Division">
        <p className="text-sm text-muted">Standings populate after Elo build (~60s).</p>
      </Card>
    );
  }

  const division = data.divisions.find((d) => d.teams.some((t) => t.team_id === teamId));
  if (!division) {
    return (
      <Card title="Division">
        <p className="text-sm text-muted">This team isn't in projected standings yet.</p>
      </Card>
    );
  }
  const myIndex = division.teams.findIndex((t) => t.team_id === teamId);
  const me = division.teams[myIndex];

  return (
    <Card
      title={`${division.conference} ${division.division}`}
      action={
        <span className="text-xs">
          <span className="text-team-primary font-bold">#{myIndex + 1}</span>
          <span className="text-muted"> of {division.teams.length}</span>
        </span>
      }
    >
      <ul className="space-y-1.5 text-sm">
        {division.teams.map((t, i) => (
          <li
            key={t.team_id}
            className={`flex items-center justify-between gap-2 py-1 px-1.5 rounded ${
              t.team_id === teamId ? "bg-team-primary/15" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="text-muted text-xs tabular-nums w-3 text-right">{i + 1}</span>
              <TeamLogo teamId={t.team_id} size={20} />
              <Link href={`/teams/${t.team_id}`}
                className={`hover:underline ${t.team_id === teamId ? "font-bold" : "font-medium"}`}>
                {t.team_id}
              </Link>
            </div>
            <div className="text-[11px] tabular-nums">
              <span className="font-medium">{t.mean_wins.toFixed(1)}W</span>
              <span className="text-muted ml-2">{t.playoff_pct.toFixed(0)}% PO</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
