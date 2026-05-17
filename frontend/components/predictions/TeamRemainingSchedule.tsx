"use client";
import useSWR from "swr";
import Link from "next/link";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, TeamRemainingSchedule as TRS } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Full-season schedule with predicted win prob + cumulative win projection.
 * Plays the role of the team's "where are we headed?" view.
 */
export function TeamRemainingScheduleCard({ teamId }: { teamId: string }) {
  const { data, isLoading } = useSWR(
    ["team-remaining-schedule", teamId],
    () => api.teamRemainingSchedule(teamId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Full-season outlook">
        <p className="text-sm text-muted">Loading game-by-game predictions…</p>
      </Card>
    );
  }
  if (data.games.length === 0) {
    return (
      <Card title="Full-season outlook">
        <p className="text-sm text-muted">No schedule available yet.</p>
      </Card>
    );
  }

  const chartData = data.games.map((g) => ({
    week: g.week ?? 0,
    projected: g.cumulative_projected_wins,
    expected: g.win_prob,
  }));

  return (
    <Card
      title="Full-season outlook"
      action={
        <span className="text-xs text-muted tabular-nums">
          {data.banked_wins} banked + {data.projected_remaining_wins.toFixed(1)} projected = {data.projected_total_wins.toFixed(1)} wins
        </span>
      }
    >
      <div className="mb-3">
        <h3 className="text-xs text-muted mb-1">Cumulative projected wins</h3>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="week" tick={{ fill: "#94a3b8", fontSize: 11 }} />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} width={36} />
            <Tooltip
              contentStyle={{
                background: "var(--panel)",
                border: "1px solid var(--border)",
                color: "var(--text)",
                fontSize: 12,
              }}
            />
            <Line
              type="monotone"
              dataKey="projected"
              stroke="var(--team-primary)"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">Opp</th>
              <th className="pr-3">Result</th>
              <th className="pr-3">Win %</th>
              <th className="pr-3">Spread</th>
              <th className="pr-3">O/U</th>
            </tr>
          </thead>
          <tbody>
            {data.games.map((g) => (
              <tr key={g.id || `${g.week}-${g.opponent}`} className="border-t divider">
                <td className="py-1 pr-3 text-muted">{g.week ?? "—"}</td>
                <td className="pr-3">
                  <span className="text-muted">{g.is_home ? "vs" : "@"}</span>{" "}
                  <Link href={`/teams/${g.opponent}`} className="hover:underline font-medium">
                    {g.opponent}
                  </Link>
                </td>
                <td className="pr-3 tabular-nums">
                  {g.played ? (
                    <span className={
                      g.outcome === "W" ? "text-emerald-400" :
                      g.outcome === "L" ? "text-red-400" : "text-muted"
                    }>
                      {g.outcome} {g.my_score}-{g.opp_score}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="pr-3 tabular-nums">
                  {(g.win_prob * 100).toFixed(0)}%
                </td>
                <td className="pr-3 tabular-nums">
                  {g.predicted_spread_for_team > 0 ? "+" : ""}
                  {g.predicted_spread_for_team.toFixed(1)}
                </td>
                <td className="pr-3 tabular-nums">{g.predicted_total.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
