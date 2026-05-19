"use client";
import useSWR from "swr";
import Link from "next/link";
import { api, EdgeGame } from "@/lib/api";
import { Card } from "../Card";
import { TeamLogo } from "../TeamLogo";

/**
 * Compares this team's next upcoming game to the market line. Surfaces spread,
 * total, and moneyline edges vs our Elo + scoring model.
 */
export function TeamEdgeCard({ teamId }: { teamId: string }) {
  const { data, isLoading, error } = useSWR(
    ["team-betting-edge", teamId],
    () => api.teamBettingEdge(teamId),
    { revalidateOnFocus: false, refreshInterval: 5 * 60_000 },
  );

  if (isLoading || !data) {
    return (
      <Card title="Edge vs market">
        <p className="text-sm text-muted">Pulling market lines…</p>
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="Edge vs market">
        <p className="text-sm text-red-400">Could not load edge data.</p>
      </Card>
    );
  }

  const teamGames = data.games;
  const opponent = data.opponent;

  if (teamGames.length === 0) {
    const msg =
      data.empty_reason === "season_complete"
        ? "No upcoming games — season complete or schedule not synced yet."
        : "No upcoming game found for this team yet.";
    return (
      <Card title="Edge vs market">
        <p className="text-sm text-muted">{msg}</p>
      </Card>
    );
  }

  const hasMarket = teamGames.some((g) => g.market?.market_spread_home != null);

  return (
    <Card
      title="Edge vs market"
      action={
        opponent ? (
          <Link
            href={`/h2h/${teamId}/${opponent}`}
            className="text-[11px] text-muted hover:text-text"
          >
            H2H breakdown →
          </Link>
        ) : (
          <span className="text-[11px] text-muted">Model vs sportsbook consensus</span>
        )
      }
    >
      <div className="space-y-3">
        {teamGames.map((g) => (
          <EdgeRow key={g.id || `${g.home_team_id}-${g.away_team_id}`} g={g} myId={teamId} />
        ))}
      </div>
      {!hasMarket && (
        <p className="text-[11px] text-muted mt-3">
          Market lines not matched yet — run{" "}
          <code className="text-[10px]">POST /admin/refresh/odds</code> or set{" "}
          <code className="text-[10px]">ODDS_API_KEY</code> for live consensus.
        </p>
      )}
    </Card>
  );
}

function EdgeRow({ g, myId }: { g: EdgeGame; myId: string }) {
  const market = g.market;
  const isHome = g.home_team_id === myId;
  const oppId = isHome ? g.away_team_id : g.home_team_id;
  const pred = g.prediction;
  const ourSpreadForMe = isHome ? pred.predicted_spread : -pred.predicted_spread;
  const marketSpreadForMe =
    market?.market_spread_home != null
      ? isHome
        ? market.market_spread_home
        : -market.market_spread_home
      : null;
  const edgeSpread =
    g.edge_spread != null ? (isHome ? g.edge_spread : -g.edge_spread) : null;
  const ourWinPct = (isHome ? pred.home_win_prob : pred.away_win_prob) * 100;
  const marketWinPct =
    market?.market_home_win_prob != null
      ? (isHome ? market.market_home_win_prob : 1 - market.market_home_win_prob) * 100
      : null;
  const edgeWinPct =
    g.edge_win_prob != null ? (isHome ? -g.edge_win_prob : g.edge_win_prob) * 100 : null;

  return (
    <div className="panel p-3">
      <div className="flex items-center justify-between text-sm mb-2">
        <span className="font-medium">
          <span className="text-muted">{isHome ? "vs" : "@"}</span>{" "}
          <Link
            href={`/teams/${oppId}`}
            className="inline-flex items-center gap-1 align-middle hover:underline"
          >
            <TeamLogo teamId={oppId} size={20} />
            {oppId}
          </Link>
        </span>
        <span className="text-xs text-muted">
          Wk {g.week} · {g.gameday}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
        <Cell label="Our spread" value={signed(ourSpreadForMe)} />
        <Cell
          label="Market spread"
          value={marketSpreadForMe != null ? signed(marketSpreadForMe) : "n/a"}
        />
        <Cell
          label="Spread edge"
          value={edgeSpread != null ? signed(edgeSpread) : "—"}
          highlight={edgeSpread != null && Math.abs(edgeSpread) >= 2}
        />
        <Cell label="Our win %" value={`${ourWinPct.toFixed(0)}%`} />
        <Cell
          label="Market win %"
          value={marketWinPct != null ? `${marketWinPct.toFixed(0)}%` : "n/a"}
        />
        <Cell
          label="ML edge"
          value={edgeWinPct != null ? fmtPts(edgeWinPct) : "—"}
          highlight={edgeWinPct != null && Math.abs(edgeWinPct) >= 4}
        />
        <Cell label="Our total" value={pred.predicted_total?.toFixed(1) ?? "—"} />
        <Cell
          label="Market total"
          value={market?.market_total != null ? market.market_total.toFixed(1) : "n/a"}
        />
        <Cell
          label="Total edge"
          value={g.edge_total != null ? signed(g.edge_total) : "—"}
          highlight={g.edge_total != null && Math.abs(g.edge_total) >= 2.5}
        />
      </div>
      {g.recommendation && (
        <div className="mt-2 text-xs">
          <span className="bg-emerald-500/15 text-emerald-400 px-2 py-1 rounded">
            Value: {g.recommendation}
          </span>
        </div>
      )}
      {pred.game_script && (
        <p className="mt-2 text-[11px] text-muted">
          Game script: <span className="text-text">{pred.game_script}</span>
        </p>
      )}
    </div>
  );
}

function Cell({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="bg-bg rounded px-2 py-1.5">
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div
        className="font-medium tabular-nums"
        style={highlight ? { color: "#22c55e" } : undefined}
      >
        {value}
      </div>
    </div>
  );
}

function signed(v: number): string {
  if (v === 0) return "PK";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
}

function fmtPts(v: number): string {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(0)} pts`;
}
