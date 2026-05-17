"use client";
import useSWR from "swr";
import { api, EdgeGame } from "@/lib/api";
import { Card } from "../Card";
import { TeamLogo } from "../TeamLogo";

/**
 * Compares this team's upcoming game(s) to the market line. When our model
 * disagrees with the line by ≥2 points, surface it as a value play. Without
 * an Odds API key, the market columns just say "n/a".
 */
export function TeamEdgeCard({ teamId }: { teamId: string }) {
  const { data, isLoading } = useSWR(
    ["betting-edge"],
    () => api.bettingEdge(),
    { revalidateOnFocus: false, refreshInterval: 5 * 60_000 },
  );

  if (isLoading || !data) {
    return (
      <Card title="Edge vs market">
        <p className="text-sm text-muted">Pulling market lines…</p>
      </Card>
    );
  }

  const teamGames = data.games.filter(
    (g) => g.home_team_id === teamId || g.away_team_id === teamId,
  );

  if (teamGames.length === 0) {
    return (
      <Card title="Edge vs market">
        <p className="text-sm text-muted">No upcoming game found for this team yet.</p>
      </Card>
    );
  }

  return (
    <Card title="Edge vs market" action={<span className="text-[11px] text-muted">Our line vs sportsbook consensus</span>}>
      <div className="space-y-3">
        {teamGames.map((g) => (
          <EdgeRow key={g.id || `${g.home_team_id}-${g.away_team_id}`} g={g} myId={teamId} />
        ))}
      </div>
      {!data.games.some((g) => g.market) && (
        <p className="text-[11px] text-muted mt-3">
          Market data unavailable — set <code className="text-[10px]">ODDS_API_KEY</code> in
          <code className="text-[10px]"> .env</code> to enable the edge column.
        </p>
      )}
    </Card>
  );
}

function EdgeRow({ g, myId }: { g: EdgeGame; myId: string }) {
  const market = g.market;
  const isHome = g.home_team_id === myId;
  const ourSpreadForMe = isHome ? g.prediction.predicted_spread : -g.prediction.predicted_spread;
  const marketSpreadForMe = market?.market_spread_home != null
    ? (isHome ? market.market_spread_home : -market.market_spread_home)
    : null;
  const edge = (g.edge_spread != null)
    ? (isHome ? g.edge_spread : -g.edge_spread)
    : null;
  const totalEdge = g.edge_total;

  return (
    <div className="panel p-3">
      <div className="flex items-center justify-between text-sm mb-2">
        <span className="font-medium">
          <span className="text-muted">{isHome ? "vs" : "@"}</span>{" "}
          <span className="inline-flex items-center gap-1 align-middle">
            <TeamLogo teamId={isHome ? g.away_team_id : g.home_team_id} size={20} />
            {isHome ? g.away_team_id : g.home_team_id}
          </span>
        </span>
        <span className="text-xs text-muted">Wk {g.week} · {g.gameday}</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        <Cell label="Our line" value={signed(ourSpreadForMe)} />
        <Cell label="Market" value={marketSpreadForMe != null ? signed(marketSpreadForMe) : "n/a"} />
        <Cell
          label="Spread edge"
          value={edge != null ? signed(edge) : "—"}
          color={edge != null && Math.abs(edge) >= 2 ? "#22c55e" : undefined}
        />
        <Cell
          label="Total edge"
          value={totalEdge != null ? signed(totalEdge) : "—"}
          color={totalEdge != null && Math.abs(totalEdge) >= 2.5 ? "#22c55e" : undefined}
        />
      </div>
      {g.recommendation && (
        <div className="mt-2 text-xs">
          <span className="bg-emerald-500/15 text-emerald-400 px-2 py-1 rounded">
            Value: {g.recommendation}
          </span>
        </div>
      )}
      {g.prediction?.game_script && (
        <div className="mt-2 text-[11px] text-muted">
          Predicted game script: <span className="text-text">{g.prediction.game_script}</span>
        </div>
      )}
    </div>
  );
}

function Cell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-bg rounded px-2 py-1.5">
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="font-medium tabular-nums" style={color ? { color } : undefined}>{value}</div>
    </div>
  );
}

function signed(v: number): string {
  if (v === 0) return "PK";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
}
