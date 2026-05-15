"use client";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";

export type RadarSeries = {
  name: string;
  color: string;
  /** percentile-keyed by metric label */
  values: Record<string, number | null>;
};

/**
 * Renders N overlapping radars on a single 0..100 axis. The data shape
 * recharts wants is one row per metric label, with a column per series.
 */
export function MultiRadar({
  metrics,
  series,
  height = 360,
}: {
  metrics: string[];
  series: RadarSeries[];
  height?: number;
}) {
  const data = metrics.map((m) => {
    const row: Record<string, any> = { metric: m };
    for (const s of series) {
      const v = s.values[m];
      // Recharts will treat null as a gap; that's fine for missing values.
      row[s.name] = v == null ? null : v;
    }
    return row;
  });

  if (series.length === 0 || metrics.length === 0) {
    return <div className="text-sm text-muted">Nothing to compare yet.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={data} outerRadius="78%">
        <PolarGrid stroke="rgba(255,255,255,0.08)" />
        <PolarAngleAxis dataKey="metric" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 10 }} />
        {series.map((s) => (
          <Radar
            key={s.name}
            name={s.name}
            dataKey={s.name}
            stroke={s.color}
            fill={s.color}
            fillOpacity={series.length === 1 ? 0.35 : 0.18}
            isAnimationActive={false}
          />
        ))}
        <Legend wrapperStyle={{ fontSize: 12, color: "#cbd5e1" }} />
        <Tooltip
          contentStyle={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
          formatter={(v: number, name: string) => [`${v?.toFixed?.(0) ?? v} %ile`, name]}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
