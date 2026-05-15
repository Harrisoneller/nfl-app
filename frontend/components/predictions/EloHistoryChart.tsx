"use client";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EloHistoryPoint } from "@/lib/api";

/**
 * Per-week Elo trace across one or more seasons. X-axis is a flattened
 * "season-week" index so multi-season views stay readable.
 */
export function EloHistoryChart({
  points,
  color = "var(--team-primary)",
  height = 240,
}: {
  points: EloHistoryPoint[];
  color?: string;
  height?: number;
}) {
  if (!points || points.length === 0) {
    return <div className="text-sm text-muted">No Elo history yet.</div>;
  }
  const data = points.map((p, i) => ({
    idx: i,
    label: `${p.season} W${p.week}`,
    rating: p.rating,
    season: p.season,
    week: p.week,
  }));
  // Season boundaries for vertical guides
  const seasonStarts: number[] = [];
  let lastSeason: number | null = null;
  data.forEach((d, i) => {
    if (d.season !== lastSeason) {
      if (lastSeason !== null) seasonStarts.push(i);
      lastSeason = d.season;
    }
  });
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" />
        <XAxis
          dataKey="label"
          tick={{ fill: "#94a3b8", fontSize: 10 }}
          interval={Math.max(0, Math.floor(data.length / 12))}
        />
        <YAxis
          tick={{ fill: "#94a3b8", fontSize: 11 }}
          domain={["dataMin - 30", "dataMax + 30"]}
          width={42}
        />
        <ReferenceLine y={1500} stroke="rgba(255,255,255,0.18)" strokeDasharray="3 3" />
        {seasonStarts.map((i) => (
          <ReferenceLine key={i} x={data[i].label} stroke="rgba(255,255,255,0.15)" />
        ))}
        <Tooltip
          contentStyle={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
        />
        <Line
          type="monotone"
          dataKey="rating"
          stroke={color}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
