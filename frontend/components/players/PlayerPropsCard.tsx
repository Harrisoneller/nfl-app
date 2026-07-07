"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * PlayerPropsCard — sportsbook prop lines vs the model's distribution.
 * Consensus line = median across books; market P(over) is de-vigged; edge =
 * model P(over) − market P(over). Also hosts the custom-line calculator so
 * users can price ANY line, not just what the books post.
 */

const CALC_STATS: Record<string, { key: string; label: string }[]> = {
  QB: [
    { key: "passing_yards", label: "Passing yards" },
    { key: "passing_tds", label: "Passing TDs" },
    { key: "attempts", label: "Pass attempts" },
    { key: "rushing_yards", label: "Rushing yards" },
  ],
  RB: [
    { key: "rushing_yards", label: "Rushing yards" },
    { key: "carries", label: "Carries" },
    { key: "receptions", label: "Receptions" },
    { key: "receiving_yards", label: "Receiving yards" },
    { key: "anytime_td", label: "Anytime TD" },
  ],
  WR: [
    { key: "receiving_yards", label: "Receiving yards" },
    { key: "receptions", label: "Receptions" },
    { key: "targets", label: "Targets" },
    { key: "anytime_td", label: "Anytime TD" },
  ],
  TE: [
    { key: "receiving_yards", label: "Receiving yards" },
    { key: "receptions", label: "Receptions" },
    { key: "targets", label: "Targets" },
    { key: "anytime_td", label: "Anytime TD" },
  ],
};

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function EdgeBadge({ edge, side }: { edge?: number; side?: string }) {
  if (edge == null) return null;
  const strong = Math.abs(edge) >= 0.06;
  const color = edge >= 0 ? "#22c55e" : "#ef4444";
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[10px] font-bold"
      style={{ color, border: `1px solid ${color}`, opacity: strong ? 1 : 0.7 }}
      title="model P(over) − de-vigged market P(over)"
    >
      {side === "over" ? "OVER" : "UNDER"} {(Math.abs(edge) * 100).toFixed(1)}%
    </span>
  );
}

export function PlayerPropsCard({ playerId, position }: { playerId: string; position: string }) {
  const { data, isLoading } = useSWR(
    ["player-props", playerId],
    () => api.playerProps(playerId),
    { revalidateOnFocus: false },
  );

  const hasProps = !!data && !data.error && data.props.length > 0;

  return (
    <Card
      title="Prop lines vs model"
      action={<span className="text-[11px] text-muted">consensus across books · de-vigged</span>}
    >
      {isLoading && <p className="text-sm text-muted">Loading prop lines…</p>}
      {!isLoading && !hasProps && (
        <p className="text-sm text-muted">
          No prop lines captured for this player yet — books post player props a
          few days before kickoff. The calculator below prices any line from the
          model's distribution in the meantime.
        </p>
      )}
      {hasProps && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-3">Market</th>
                <th className="pr-3">Line</th>
                <th className="pr-3">Books</th>
                <th className="pr-3">Market P(over)</th>
                <th className="pr-3">Model P(over)</th>
                <th className="pr-3">Edge</th>
              </tr>
            </thead>
            <tbody>
              {data!.props.map((p, i) => (
                <tr key={i} className="border-t divider">
                  <td className="py-1 pr-3">{p.market_label}</td>
                  <td className="pr-3 tabular-nums">{p.line ?? "—"}</td>
                  <td className="pr-3 tabular-nums">{p.books}</td>
                  <td className="pr-3 tabular-nums">{pct(p.market_over_prob)}</td>
                  <td
                    className="pr-3 tabular-nums"
                    title={p.model_mean != null ? `model: ${p.model_mean} ± ${p.model_sd}` : undefined}
                  >
                    {pct(p.model_over_prob)}
                  </td>
                  <td className="pr-3"><EdgeBadge edge={p.edge} side={p.side} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <OverProbCalculator playerId={playerId} position={position} />
      <p className="text-[10px] text-muted mt-2">
        Model probabilities come from the same distribution as the projections
        above — one source of truth. Advisory only; not betting advice.
      </p>
    </Card>
  );
}

function OverProbCalculator({ playerId, position }: { playerId: string; position: string }) {
  const stats = CALC_STATS[position] || CALC_STATS.WR;
  const [stat, setStat] = useState(stats[0]?.key || "receiving_yards");
  const [line, setLine] = useState<string>("");
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.playerOverProb>> | null>(null);
  const [busy, setBusy] = useState(false);

  const isTd = stat === "anytime_td";

  async function run() {
    setBusy(true);
    try {
      const r = await api.playerOverProb(playerId, stat, isTd ? 0 : Number(line || 0));
      setResult(r);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 panel p-3">
      <div className="text-xs text-muted mb-2 font-medium">
        Custom line calculator — price any line from the model
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={stat}
          onChange={(e) => { setStat(e.target.value); setResult(null); }}
          className="bg-bg border divider rounded px-2 py-1.5 text-xs"
        >
          {stats.map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
        {!isTd && (
          <input
            value={line}
            onChange={(e) => setLine(e.target.value)}
            placeholder="Line (e.g. 67.5)"
            inputMode="decimal"
            className="bg-bg border divider rounded px-2 py-1.5 text-xs w-28"
          />
        )}
        <button
          onClick={run}
          disabled={busy || (!isTd && line.trim() === "")}
          className="bg-team-primary text-white text-xs rounded px-3 py-1.5 disabled:opacity-50"
        >
          {busy ? "Pricing…" : "Price it"}
        </button>
        {result && !result.error && (
          <span className="text-xs tabular-nums">
            {isTd ? (
              <>P(anytime TD) = <b>{pct(result.prob)}</b> (λ {result.expected_tds})</>
            ) : (
              <>
                Over <b>{pct(result.over_prob)}</b> · Under <b>{pct(result.under_prob)}</b>
                <span className="text-muted ml-1">
                  (model {result.mean} ± {result.sd}, wk {result.week} vs {result.opponent})
                </span>
              </>
            )}
          </span>
        )}
        {result?.error && <span className="text-xs text-muted">{result.error}</span>}
      </div>
    </div>
  );
}
