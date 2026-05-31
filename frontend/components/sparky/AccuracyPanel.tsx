"use client";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { SparkyAccuracy } from "@/lib/api";
import { HelpTip, TERMS } from "./HelpTip";
import { pctPoints } from "./format";

const WINDOW_LABELS: Record<string, string> = {
  daily: "Today",
  rolling_7d: "7-day",
  rolling_21d: "21-day",
  rolling_30d: "30-day",
};

/** Historical-accuracy dashboard: rolling windows, calibration, by-signal, parlays. */
export function AccuracyPanel({ data }: { data: SparkyAccuracy }) {
  const picks = data.individual_picks;
  const parlays = data.parlays;
  const trends = data.trends;

  return (
    <div className="space-y-5">
      {/* Headline trends */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat
          label="Pick accuracy"
          tip={{ label: "Pick Accuracy", body: "How often Sparky's predicted winner was the actual winner." }}
          value={pctPoints(trends.overall_pick_accuracy_pct)}
          sub={`${trends.n_picks_settled} settled`}
        />
        <Stat
          label="Best parlay hits"
          tip={TERMS.rank1_hit}
          value={pctPoints(trends.overall_parlay_rank1_pct)}
          sub={`${trends.n_parlays_settled} slates · Rank #1`}
        />
        <Stat
          label="Close enough (Top 3)"
          tip={TERMS.top3}
          value={pctPoints(trends.overall_parlay_top3_pct)}
          sub="winner in top 3 ranked"
        />
        <Stat
          label="Best signal"
          tip={{
            label: "Best Signal",
            body: "Which named market signal has historically had the highest win rate when Sparky's pick included it.",
          }}
          value={trends.best_signal ? labelize(trends.best_signal.signal) : "—"}
          sub={trends.best_signal ? pctPoints(trends.best_signal.accuracy_pct) : ""}
        />
      </div>

      {/* Rolling windows */}
      <div className="sparky-card p-4">
        <h3 className="text-sm font-semibold text-white mb-1 flex items-center">
          Recent track record
          <HelpTip label={TERMS.rolling.label} body={TERMS.rolling.body} />
        </h3>
        <p className="text-xs text-muted mb-3">
          7-day reacts fast to a streak; 30-day smooths out noise and shows the underlying trend.
        </p>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            <div className="text-xs text-muted mb-2">Individual picks (winner correct)</div>
            <div className="grid grid-cols-4 gap-2">
              {Object.entries(picks.rolling).map(([k, w]) => (
                <Window key={k} label={WINDOW_LABELS[k] ?? k} value={pctPoints(w.accuracy_pct)} n={w.n} />
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted mb-2 flex items-center">
              Top-ranked parlay hit rate
              <HelpTip label={TERMS.rank1_hit.label} body={TERMS.rank1_hit.body} />
            </div>
            <div className="grid grid-cols-4 gap-2">
              {Object.entries(parlays.rolling).map(([k, w]) => (
                <Window key={k} label={WINDOW_LABELS[k] ?? k} value={pctPoints(w.rank_1_hit_rate)} n={w.n} />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Calibration: accuracy by confidence band */}
      <div className="sparky-card p-4">
        <h3 className="text-sm font-semibold text-white mb-1 flex items-center">
          Is Sparky's confidence honest?
          <HelpTip label={TERMS.calibration.label} body={TERMS.calibration.body} />
        </h3>
        <p className="text-xs text-muted mb-3">
          An 80%-confidence pick should win about 80% of the time. If Sparky is well-calibrated,
          these bars rise from left to right.
        </p>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart
            data={picks.by_confidence_band.map((b) => ({
              band: b.band,
              acc: b.accuracy_pct ?? 0,
              n: b.n,
            }))}
            margin={{ top: 8, right: 12, bottom: 0, left: -8 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
            <XAxis dataKey="band" stroke="#7c8aa0" fontSize={11} />
            <YAxis domain={[0, 100]} stroke="#7c8aa0" fontSize={11} tickFormatter={(v) => `${v}%`} width={42} />
            <Tooltip
              contentStyle={{ background: "#0b1018", border: "1px solid rgba(45,212,191,0.3)", borderRadius: 10, fontSize: 12 }}
              formatter={(v: number, _n, p) => [`${v}% (${(p?.payload as { n: number })?.n ?? 0} picks)`, "Accuracy"]}
            />
            <Bar dataKey="acc" radius={[6, 6, 0, 0]}>
              {picks.by_confidence_band.map((_, i) => (
                <Cell key={i} fill={`rgba(16,185,129,${0.45 + i * 0.12})`} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* By signal */}
      <div className="sparky-card p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Accuracy by signal type</h3>
        {picks.by_signal.length === 0 ? (
          <p className="text-xs text-muted">No settled picks with signals yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-muted text-xs">
                <tr>
                  <th className="py-1 pr-3">Signal</th>
                  <th className="py-1 pr-3 text-right">Picks</th>
                  <th className="py-1 pr-3 text-right">Accuracy</th>
                  <th className="py-1 w-1/2">&nbsp;</th>
                </tr>
              </thead>
              <tbody>
                {picks.by_signal.map((s) => (
                  <tr key={s.signal} className="border-t divider">
                    <td className="py-1.5 pr-3">{labelize(s.signal)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{s.n}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums font-semibold text-white">
                      {pctPoints(s.accuracy_pct)}
                    </td>
                    <td className="py-1.5">
                      <div className="h-1.5 rounded bg-slate-700/60 overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-emerald-400 to-cyan-400"
                          style={{ width: `${s.accuracy_pct ?? 0}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tip,
}: {
  label: string;
  value: string;
  sub?: string;
  tip?: { label: string; body: string };
}) {
  return (
    <div className="sparky-stat">
      <div className="sparky-stat__value">{value}</div>
      <div className="sparky-stat__label inline-flex items-center">
        {label}
        {tip && <HelpTip label={tip.label} body={tip.body} />}
      </div>
      {sub && <div className="text-[10px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function Window({ label, value, n }: { label: string; value: string; n: number }) {
  return (
    <div className="text-center rounded-lg border border-slate-700/50 py-2">
      <div className="text-sm font-semibold text-white tabular-nums">{value}</div>
      <div className="text-[10px] text-muted">{label}</div>
      <div className="text-[9px] text-muted/70">n={n}</div>
    </div>
  );
}

function labelize(key: string): string {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
