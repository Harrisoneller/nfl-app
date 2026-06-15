"use client";

import type { BetProfile } from "@/lib/api";
import { Card } from "@/components/Card";
import { resultColor, signedPct, signedUnits } from "./format";

/**
 * The bettor's at-a-glance scorecard. Leads with the two metrics that matter
 * most: realized ROI (units) and CLV / beat-close rate — the leading indicator
 * of whether the bettor is actually beating the market, independent of variance.
 */
export function ProfileSummary({ p }: { p: BetProfile }) {
  const streakLabel =
    p.current_streak === 0
      ? "—"
      : p.current_streak > 0
        ? `W${p.current_streak}`
        : `L${Math.abs(p.current_streak)}`;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat
          label="Profit (units)"
          value={signedUnits(p.profit_units)}
          sub={p.roi_pct != null ? `${signedPct(p.roi_pct)} ROI` : "no settled bets"}
          tone={resultColor(p.profit_units)}
        />
        <Stat
          label="Avg CLV"
          value={p.avg_clv_pct != null ? signedPct(p.avg_clv_pct) : "—"}
          sub={
            p.beat_close_pct != null
              ? `beat close ${p.beat_close_pct}% (${p.legs_with_clv})`
              : "needs closing lines"
          }
          tone={resultColor(p.avg_clv_pct)}
        />
        <Stat
          label="Record"
          value={`${p.won}-${p.lost}${p.push ? `-${p.push}` : ""}`}
          sub={p.win_rate != null ? `${(p.win_rate * 100).toFixed(1)}% win` : "—"}
        />
        <Stat
          label="Open / Streak"
          value={`${p.pending} open`}
          sub={`${p.open_risk_units.toFixed(2)}u at risk · ${streakLabel}`}
        />
      </div>

      {(p.staked_dollars != null || p.profit_dollars != null) && (
        <Card>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
            <span className="text-muted">Real money:</span>
            <span className={resultColor(p.profit_dollars)}>
              {p.profit_dollars != null
                ? `${p.profit_dollars >= 0 ? "+" : "-"}$${Math.abs(p.profit_dollars).toFixed(2)} P/L`
                : "—"}
            </span>
            {p.roi_dollars_pct != null && (
              <span className={resultColor(p.roi_dollars_pct)}>{signedPct(p.roi_dollars_pct)} ROI</span>
            )}
            {p.staked_dollars != null && (
              <span className="text-muted">${p.staked_dollars.toFixed(2)} staked</span>
            )}
          </div>
        </Card>
      )}

      <Card title="Breakdown">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
          <RecordTable title="By market" rows={p.record_by_market} />
          <RecordTable title="By type" rows={p.record_by_type} />
        </div>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone = "text-text",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <Card>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-2xl font-semibold tabular-nums mt-1 ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted mt-0.5">{sub}</div>}
    </Card>
  );
}

function RecordTable({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, { won: number; lost: number; push: number }>;
}) {
  const entries = Object.entries(rows);
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">{title}</div>
      {entries.length === 0 ? (
        <p className="text-xs text-muted">No settled bets yet.</p>
      ) : (
        <table className="w-full text-xs">
          <tbody>
            {entries.map(([k, r]) => (
              <tr key={k} className="border-t divider">
                <td className="py-1 capitalize">{k}</td>
                <td className="py-1 text-right tabular-nums">
                  <span className="text-green-500">{r.won}W</span>{" "}
                  <span className="text-red-400">{r.lost}L</span>
                  {r.push ? <span className="text-amber-500"> {r.push}P</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
