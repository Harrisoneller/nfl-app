"use client";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "./Card";

/**
 * Top 5 players on a team by depth-chart rank + position relevance.
 * Pulls from the team roster (already cached) and surfaces the offensive
 * skill players the casual fan will recognize.
 */
const PRIORITY_POSITIONS = ["QB", "RB", "WR", "TE"];

export function TopPerformersCard({ teamId }: { teamId: string }) {
  const { data: roster } = useSWR(
    ["team-roster", teamId],
    () => api.getTeamRoster(teamId),
  );

  if (!roster) {
    return (
      <Card title="Key players">
        <p className="text-sm text-muted">Loading roster…</p>
      </Card>
    );
  }

  // Filter to skill players, sort by position priority + depth chart
  const skill = roster.filter((p) => PRIORITY_POSITIONS.includes(p.position));
  skill.sort((a, b) => {
    const ai = PRIORITY_POSITIONS.indexOf(a.position);
    const bi = PRIORITY_POSITIONS.indexOf(b.position);
    if (ai !== bi) return ai - bi;
    const ad = (a.metadata_json as any)?.depth_chart_order ?? 99;
    const bd = (b.metadata_json as any)?.depth_chart_order ?? 99;
    return ad - bd;
  });

  // Take starting QB + 2 RB + 3 WR + 1 TE = up to 7 players
  const targets = { QB: 1, RB: 2, WR: 3, TE: 1 };
  const picked: typeof skill = [];
  for (const pos of PRIORITY_POSITIONS) {
    const seen = picked.filter((p) => p.position === pos).length;
    const cap = targets[pos as keyof typeof targets];
    for (const p of skill) {
      if (p.position === pos && picked.filter((x) => x.position === pos).length < cap) {
        picked.push(p);
      }
    }
  }

  if (picked.length === 0) {
    return (
      <Card title="Key players">
        <p className="text-sm text-muted">No skill players found on roster.</p>
      </Card>
    );
  }

  return (
    <Card title="Key players" action={<Link href={`/players?team_id=${teamId}`} className="text-[11px] text-muted hover:text-text">All →</Link>}>
      <ul className="space-y-1.5 text-sm">
        {picked.map((p) => {
          const injuryStatus = (p.metadata_json as any)?.injury_status;
          return (
            <li key={p.id} className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-[10px] uppercase tracking-wide bg-team-primary/15 text-team-primary px-1.5 py-0.5 rounded">
                  {p.position}
                </span>
                <Link href={`/players/${p.id}`} className="hover:underline font-medium truncate">
                  {p.full_name}
                </Link>
              </div>
              <div className="text-[11px] text-muted whitespace-nowrap">
                {injuryStatus && injuryStatus !== "Healthy" && (
                  <span className="text-orange-400 mr-1.5">{injuryStatus}</span>
                )}
                #{p.jersey_number ?? "—"}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
