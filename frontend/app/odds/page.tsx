"use client";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { useState } from "react";

const fetcher = ([key, market]: [string, string]) =>
  api.odds(market || undefined, 200);

export default function OddsPage() {
  const [market, setMarket] = useState("");
  const { data, isLoading, error } = useSWR(["odds", market], fetcher);
  const lines = data ?? [];

  // Group by event for game odds
  const byEvent: Record<string, typeof lines> = {};
  for (const l of lines) {
    const k = l.event_id ?? "outright";
    byEvent[k] ??= [] as any;
    (byEvent[k] as any).push(l);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Odds</h1>
        <select value={market} onChange={(e) => setMarket(e.target.value)}
          className="bg-bg border divider rounded px-3 py-2 text-sm">
          <option value="">All markets</option>
          <option value="h2h">Moneyline (h2h)</option>
          <option value="spreads">Spreads</option>
          <option value="totals">Totals</option>
          <option value="outrights">Futures (outrights)</option>
        </select>
      </div>

      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      {error && <p className="text-sm text-red-400">Failed to load odds.</p>}
      {!isLoading && lines.length === 0 && (
        <Card>
          <p className="text-sm text-muted">
            No odds loaded. Set <code className="text-xs">ODDS_API_KEY</code> in <code className="text-xs">.env</code> and{" "}
            <code className="text-xs">POST /admin/refresh/odds</code>.
          </p>
        </Card>
      )}

      {Object.entries(byEvent).map(([eid, lines]) => {
        const first = lines[0];
        return (
          <Card key={eid} title={first ? `${first.away_team} @ ${first.home_team}` : eid}>
            <table className="w-full text-sm">
              <thead className="text-left text-muted">
                <tr><th>Market</th><th>Book</th><th>Outcome</th><th>Line</th><th>Price</th></tr>
              </thead>
              <tbody>
                {lines.map((l) => (
                  <tr key={l.id} className="border-t divider">
                    <td>{l.market}</td>
                    <td>{l.bookmaker}</td>
                    <td>{l.label}</td>
                    <td>{l.point ?? ""}</td>
                    <td className="tabular-nums">{l.price ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        );
      })}
    </div>
  );
}
