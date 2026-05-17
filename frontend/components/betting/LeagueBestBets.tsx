"use client";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "../Card";
import { TeamLogo } from "../TeamLogo";

/**
 * League-wide top model-vs-market disagreements, sorted by |edge|.
 * Surfaces sharp-side picks for the gambling-focused user.
 */
export function LeagueBestBetsCard() {
  const { data, isLoading } = useSWR(
    ["league-best-bets"],
    () => api.bestBets(),
    { refreshInterval: 5 * 60_000, revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="League-wide value bets">
        <p className="text-sm text-muted">Comparing our model to market lines…</p>
      </Card>
    );
  }
  if (data.best_bets.length === 0) {
    return (
      <Card title="League-wide value bets">
        <p className="text-sm text-muted">
          No notable edges found this week, or market data is unavailable. Set
          <code className="text-[10px] mx-1">ODDS_API_KEY</code> to enable.
        </p>
      </Card>
    );
  }

  return (
    <Card title="League-wide value bets" action={<span className="text-[11px] text-muted">|edge| ≥ 2pts</span>}>
      <ul className="space-y-2 text-sm">
        {data.best_bets.map((g) => (
          <li key={g.id || `${g.home_team_id}-${g.away_team_id}`} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <TeamLogo teamId={g.away_team_id} size={18} />
              <Link href={`/teams/${g.away_team_id}`} className="hover:underline">{g.away_team_id}</Link>
              <span className="text-muted">@</span>
              <TeamLogo teamId={g.home_team_id} size={18} />
              <Link href={`/teams/${g.home_team_id}`} className="hover:underline">{g.home_team_id}</Link>
            </div>
            <div className="text-xs text-right">
              <div className="font-medium text-emerald-400">{g.recommendation ?? `Edge ${g.edge_spread?.toFixed(1)}`}</div>
              <div className="text-muted">
                Our: {g.prediction.predicted_spread > 0 ? "+" : ""}{g.prediction.predicted_spread.toFixed(1)} ·
                Mkt: {g.market?.market_spread_home != null
                  ? `${g.market.market_spread_home > 0 ? "+" : ""}${g.market.market_spread_home.toFixed(1)}`
                  : "n/a"}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
