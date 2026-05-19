"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { api, Team } from "@/lib/api";
import { Card } from "./Card";
import { TeamLogo } from "./TeamLogo";

/**
 * Quick H2H launcher for a team page. User picks an opponent → routes to
 * /h2h/[this]/[opponent]. Lives on the Overview tab.
 */
export function H2HLauncher({ teamId }: { teamId: string }) {
  const router = useRouter();
  const { data: teams } = useSWR(["teams-list"], () => api.listTeams());
  const [opponent, setOpponent] = useState<string>("");

  const grouped = useMemo(() => {
    const out: Record<string, Team[]> = {};
    for (const t of teams ?? []) {
      if (t.id === teamId) continue;
      const key = `${t.conference} ${t.division}`;
      out[key] ??= [];
      out[key].push(t);
    }
    for (const k of Object.keys(out)) out[k].sort((x, y) => x.full_name.localeCompare(y.full_name));
    return out;
  }, [teams, teamId]);

  const launch = () => {
    if (opponent) router.push(`/h2h/${teamId}/${opponent}`);
  };

  return (
    <Card title="Compare to another team">
      <p className="text-sm text-muted mb-3">
        Launch the full head-to-head view: predicted matchup, strength-vs-weakness
        breakdown, historical results, and overlapping radars.
      </p>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          {opponent && <TeamLogo teamId={opponent} size={28} />}
          <select
            value={opponent}
            onChange={(e) => setOpponent(e.target.value)}
            className="bg-bg border divider rounded px-2 py-1.5 text-sm min-w-[200px]"
          >
            <option value="">Pick opponent…</option>
            {Object.entries(grouped).sort().map(([div, ts]) => (
              <optgroup key={div} label={div}>
                {ts.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.full_name} ({t.id})
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <button
          onClick={launch}
          disabled={!opponent}
          className="bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-50 hover:opacity-90"
        >
          Launch matchup →
        </button>
      </div>
    </Card>
  );
}
