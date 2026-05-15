"use client";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function GameLogBar({
  data,
  xKey = "week",
  yKey,
  color = "var(--team-primary)",
  height = 220,
  yLabel,
}: {
  data: any[];
  xKey?: string;
  yKey: string;
  color?: string;
  height?: number;
  yLabel?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey={xKey} tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} width={48}
          label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 10 } : undefined} />
        <Tooltip
          contentStyle={{
            background: "var(--panel)",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
        />
        <Bar dataKey={yKey} fill={color} radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
