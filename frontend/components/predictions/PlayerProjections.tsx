"use client";
import useSWR from "swr";
import { api, PlayerSeasonProjection, PlayerGamePredictions } from "@/lib/api";
import { Card } from "@/components/Card";
import { playerMetricLabel } from "@/lib/metrics";

/**
 * PlayerProjections — game-by-game stat predictions + season outlook.
 * Designed for the Predictions tab on a player page.
 */

const GRADE_COLORS: Record<string, string> = {
  A: "#22c55e", B: "#84cc16", C: "#eab308", D: "#f97316", F: "#ef4444",
};

const STAT_DISPLAY_ORDER: Record<string, string[]> = {
  QB: ["passing_yards", "passing_tds", "interceptions", "completions", "attempts", "rushing_yards", "fantasy_points_ppr"],
  RB: ["rushing_yards", "rushing_tds", "carries", "receptions", "receiving_yards", "fantasy_points_ppr"],
  WR: ["receiving_yards", "receiving_tds", "receptions", "targets", "fantasy_points_ppr"],
  TE: ["receiving_yards", "receiving_tds", "receptions", "targets", "fantasy_points_ppr"],
};


export function PlayerSeasonProjectionCard({ playerId }: { playerId: string }) {
  const { data, isLoading } = useSWR(
    ["player-season-projection", playerId],
    () => api.playerSeasonProjection(playerId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Season projection">
        <p className="text-sm text-muted">Computing pace + projections…</p>
      </Card>
    );
  }
  if (data.error) {
    return (
      <Card title="Season projection">
        <p className="text-sm text-muted">{data.error}</p>
      </Card>
    );
  }

  const order = STAT_DISPLAY_ORDER[data.position] || Object.keys(data.stats);
  const headlineStats = order.filter((k) => data.stats[k]).slice(0, 4);

  return (
    <Card
      title={`Season projection — ${data.season}`}
      action={
        <span className="text-[11px] text-muted">
          {data.games_played} played · {data.games_remaining} remaining
        </span>
      }
    >
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        {headlineStats.map((k) => {
          const s = data.stats[k];
          return (
            <div key={k} className="panel p-3">
              <div className="text-xs text-muted">{playerMetricLabel(k)}</div>
              <div className="text-2xl font-bold tabular-nums">{fmt(s.projected_final)}</div>
              <div className="text-[10px] text-muted">
                Range: {fmt(s.low_final)} – {fmt(s.high_final)}
              </div>
              <div className="text-[10px] text-muted">
                Pace: {fmt(s.per_game_pace)} / game
              </div>
            </div>
          );
        })}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Stat</th>
              <th className="pr-3">YTD</th>
              <th className="pr-3">Per game</th>
              <th className="pr-3">Proj. remaining</th>
              <th className="pr-3">Proj. final</th>
              <th className="pr-3">Range</th>
            </tr>
          </thead>
          <tbody>
            {order.filter((k) => data.stats[k]).map((k) => {
              const s = data.stats[k];
              return (
                <tr key={k} className="border-t divider">
                  <td className="py-1 pr-3">{playerMetricLabel(k)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.ytd)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.per_game_pace)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.projected_remaining)}</td>
                  <td className="pr-3 tabular-nums font-medium">{fmt(s.projected_final)}</td>
                  <td className="pr-3 tabular-nums text-muted">{fmt(s.low_final)}–{fmt(s.high_final)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}


export function PlayerGamePredictionsCard({ playerId }: { playerId: string }) {
  const { data, isLoading } = useSWR(
    ["player-game-predictions", playerId],
    () => api.playerGamePredictions(playerId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Upcoming game predictions">
        <p className="text-sm text-muted">Computing matchup adjustments…</p>
      </Card>
    );
  }
  if (data.error) {
    return (
      <Card title="Upcoming game predictions">
        <p className="text-sm text-muted">{data.error}</p>
      </Card>
    );
  }
  if (data.games.length === 0) {
    return (
      <Card title="Upcoming game predictions">
        <p className="text-sm text-muted">No upcoming games scheduled.</p>
      </Card>
    );
  }

  const order = STAT_DISPLAY_ORDER[data.position] || Object.keys(data.games[0]?.predicted || {});
  const cols = order.filter((k) => data.games[0]?.predicted?.[k]).slice(0, 6);

  return (
    <Card
      title="Upcoming game predictions"
      action={
        <span className="text-[11px] text-muted">
          {data.baseline_window}-game rolling baseline · matchup adjusted
        </span>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">vs/@</th>
              <th className="pr-3">Matchup</th>
              {cols.map((k) => (
                <th key={k} className="pr-3">{playerMetricLabel(k)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.games.map((g, i) => (
              <tr key={i} className="border-t divider">
                <td className="py-1 pr-3">{g.week ?? "—"}</td>
                <td className="pr-3">
                  <span className="text-muted">{g.is_home ? "vs" : "@"}</span>{" "}
                  <span className="font-medium">{g.opponent}</span>
                </td>
                <td className="pr-3">
                  <span
                    className="inline-block rounded px-1.5 py-0.5 text-[10px] font-bold"
                    style={{ color: GRADE_COLORS[g.matchup_grade], borderColor: GRADE_COLORS[g.matchup_grade], border: "1px solid" }}
                  >
                    {g.matchup_grade}
                  </span>
                </td>
                {cols.map((k) => {
                  const p = g.predicted[k];
                  if (!p) return <td key={k} className="pr-3">—</td>;
                  return (
                    <td key={k} className="pr-3 tabular-nums" title={`Range: ${fmt(p.low)}–${fmt(p.high)}`}>
                      {fmt(p.predicted)}
                      <span className="text-[9px] text-muted ml-1">({fmt(p.low)}–{fmt(p.high)})</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted mt-2">
        Matchup grade based on opponent defensive EPA percentile. A = soft defense, F = elite defense.
        Range shows 25th–75th percentile based on the player's per-game volatility.
      </p>
    </Card>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toFixed(1);
}
