"use client";
import useSWR from "swr";
import { api, BettingRecord } from "@/lib/api";
import { Card } from "../Card";

const KEY_PERFORMANCE_THRESHOLDS = {
  excellent: 60,
  good: 55,
  average: 48,
};

function pctColor(pct: number): string {
  if (pct >= KEY_PERFORMANCE_THRESHOLDS.excellent) return "#22c55e";
  if (pct >= KEY_PERFORMANCE_THRESHOLDS.good) return "#84cc16";
  if (pct >= KEY_PERFORMANCE_THRESHOLDS.average) return "#eab308";
  if (pct >= 40) return "#f97316";
  return "#ef4444";
}

function recordLine(r: { wins: number; losses: number; ties?: number; pushes?: number }): string {
  const ties = r.ties ?? 0;
  return ties > 0 ? `${r.wins}-${r.losses}-${ties}` : `${r.wins}-${r.losses}`;
}

export function BettingHistoryCard({ teamId }: { teamId: string }) {
  const { data, isLoading } = useSWR(
    ["team-betting-history", teamId],
    () => api.teamBettingHistory(teamId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Betting history">
        <p className="text-sm text-muted">Computing ATS / O/U records across 5 seasons…</p>
      </Card>
    );
  }
  if (data.lifetime.games === 0) {
    return (
      <Card title="Betting history">
        <p className="text-sm text-muted">No historical line data found for this team.</p>
      </Card>
    );
  }

  return (
    <Card
      title="Betting history"
      action={<span className="text-[11px] text-muted">{data.lifetime.games}g across {data.seasons.length} seasons</span>}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <RecordPanel label={`Last ${Math.min(20, data.last20.games)} games`} record={data.last20} />
        <RecordPanel label="5-year totals" record={data.lifetime} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-5">
        <Split label="As favorite" rec={data.lifetime.as_favorite} />
        <Split label="As underdog" rec={data.lifetime.as_underdog} />
        <Split label="Home" rec={data.lifetime.home_split} />
        <Split label="Away" rec={data.lifetime.away_split} />
      </div>
    </Card>
  );
}

function RecordPanel({ label, record }: { label: string; record: BettingRecord }) {
  return (
    <div className="panel p-4">
      <h3 className="text-xs uppercase tracking-wide text-muted mb-3">{label}</h3>
      <div className="grid grid-cols-3 gap-3">
        <Stat
          title="Straight up"
          value={recordLine(record.su)}
          pct={pct(record.su.wins, record.su.wins + record.su.losses)}
        />
        <Stat
          title="ATS"
          value={recordLine(record.ats)}
          pct={record.ats.win_pct}
          subtitle={record.ats.pushes ? `${record.ats.pushes} push${record.ats.pushes === 1 ? "" : "es"}` : undefined}
        />
        <Stat
          title="Over / Under"
          value={`${record.ou.overs}-${record.ou.unders}`}
          pct={record.ou.over_pct}
          subtitle={`${record.ou.over_pct.toFixed(0)}% O`}
        />
      </div>
    </div>
  );
}

function Stat({ title, value, pct, subtitle }: { title: string; value: string; pct: number; subtitle?: string }) {
  const color = pctColor(pct);
  return (
    <div>
      <div className="text-[10px] text-muted uppercase tracking-wide">{title}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-[11px] tabular-nums" style={{ color }}>{pct.toFixed(1)}%</div>
      {subtitle && <div className="text-[10px] text-muted">{subtitle}</div>}
    </div>
  );
}

function Split({ label, rec }: { label: string; rec: { games: number; wins: number; losses: number; win_pct: number } }) {
  if (rec.games === 0) return null;
  const color = pctColor(rec.win_pct);
  return (
    <div className="bg-bg rounded px-2 py-1.5">
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="font-medium tabular-nums text-sm">
        {recordLine(rec)} <span className="text-xs" style={{ color }}>· {rec.win_pct.toFixed(0)}%</span>
      </div>
    </div>
  );
}

function pct(n: number, d: number): number {
  return d === 0 ? 0 : (100 * n) / d;
}
