"use client";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

/**
 * Radar of percentile ranks. Always 0..100 axis.
 */
export function RadarProfile({
  data,
  color = "var(--team-primary)",
  height = 320,
}: {
  data: { metric: string; percentile: number | null }[];
  color?: string;
  height?: number;
}) {
  const safe = data
    .filter((d) => d.percentile != null)
    .map((d) => ({ metric: d.metric, percentile: d.percentile as number }));
  if (safe.length === 0) {
    return <div className="text-sm text-muted">Not enough data to render radar.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={safe} outerRadius="80%">
        <PolarGrid stroke="rgba(255,255,255,0.08)" />
        <PolarAngleAxis dataKey="metric" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 10 }} />
        <Radar dataKey="percentile" stroke={color} fill={color} fillOpacity={0.35} />
        <Tooltip
          contentStyle={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
          formatter={(v: number) => [`${v.toFixed(0)} %ile`, ""]}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
