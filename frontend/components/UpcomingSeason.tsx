"use client";
import useSWR from "swr";
import { api, UpcomingSeason as Upcoming } from "@/lib/api";
import { Card } from "./Card";

/**
 * Preview of the upcoming/current season for a team.
 *
 * Shows the released schedule (NFL drops it in May), with per-opponent
 * strength annotations pulled from the previous completed season's EPA
 * profile. Also shows the team's strength-of-schedule estimate.
 */
export function UpcomingSeason({ teamId }: { teamId: string }) {
  const { data, isLoading } = useSWR(
    ["team-upcoming", teamId],
    () => api.getTeamUpcoming(teamId),
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <Card title="Upcoming season">
        <p className="text-sm text-muted">Loading upcoming schedule…</p>
      </Card>
    );
  }
  if (!data || data.schedule.length === 0) {
    return (
      <Card title="Upcoming season">
        <p className="text-sm text-muted">
          The {data?.season ?? "upcoming"} season schedule isn't available yet.
          NFL typically releases it in mid-May.
        </p>
      </Card>
    );
  }

  const sos = data.strength_of_schedule;
  return (
    <Card
      title={`Upcoming season — ${data.season}`}
      action={
        <span className="text-xs text-muted">
          Opponent ratings from {data.previous_season}
        </span>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Stat label="Games scheduled" value={String(sos.n_games)} />
        <Stat
          label="Avg opp off EPA / play"
          value={fmt(sos.avg_opponent_off_epa)}
          hint="(harder if positive)"
        />
        <Stat
          label="Avg opp def EPA / play"
          value={fmt(sos.avg_opponent_def_epa)}
          hint="(harder if negative)"
        />
        <Stat
          label="Season"
          value={`${data.season}`}
          hint={data.is_upcoming ? "preview" : "in-progress"}
        />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">Matchup</th>
              <th className="pr-3">Opponent</th>
              <th className="pr-3">Opp off EPA</th>
              <th className="pr-3">Opp def EPA</th>
              <th className="pr-3">Opp PPG</th>
              <th className="pr-3">Date</th>
              <th className="pr-3">TV</th>
            </tr>
          </thead>
          <tbody>
            {data.schedule.map((g) => (
              <tr key={g.id || `${g.week}-${g.opponent}`} className="border-t divider">
                <td className="py-1 pr-3 text-muted">{g.week ?? "—"}</td>
                <td className="pr-3">{g.away_team_id} @ {g.home_team_id}</td>
                <td className="pr-3 font-medium">{g.opponent ?? "—"}</td>
                <td className="pr-3 tabular-nums">{fmt(g.opponent_prev_off_epa)}</td>
                <td className="pr-3 tabular-nums">{fmt(g.opponent_prev_def_epa)}</td>
                <td className="pr-3 tabular-nums">
                  {g.opponent_prev_points_per_game != null
                    ? g.opponent_prev_points_per_game.toFixed(1)
                    : "—"}
                </td>
                <td className="pr-3 text-muted">{g.gameday || "TBD"}</td>
                <td className="pr-3 text-muted">{g.network || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="panel p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-muted">{hint}</div>}
    </div>
  );
}

function fmt(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(3);
}
