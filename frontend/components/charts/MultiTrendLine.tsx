"use client";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export type TrendSeries = {
  name: string;
  color: string;
  /** [{ season, value }] */
  points: { season: number; value: number | null }[];
};

/** Multi-series YoY line chart — one line per entity in the overlay. */
export function MultiTrendLine({
  series,
  height = 260,
  yLabel,
}: {
  series: TrendSeries[];
  height?: number;
  yLabel?: string;
}) {
  // Merge by season → { season, [name]: value }
  const allSeasons = Array.from(
    new Set(series.flatMap((s) => s.points.map((p) => p.season))),
  ).sort((a, b) => a - b);
  const data = allSeasons.map((season) => {
    const row: Record<string, any> = { season };
    for (const s of series) {
      const pt = s.points.find((p) => p.season === season);
      row[s.name] = pt?.value ?? null;
    }
    return row;
  });

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey="season" tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <YAxis
          tick={{ fill: "#94a3b8", fontSize: 11 }}
          width={48}
          label={
            yLabel
              ? { value: yLabel, angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 }
              : undefined
          }
        />
        <Tooltip
          contentStyle={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: "#cbd5e1" }} />
        {series.map((s) => (
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={s.color}
            strokeWidth={2}
            dot={{ r: 3 }}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
