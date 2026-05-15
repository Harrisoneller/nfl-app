"use client";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function TrendLine({
  data,
  xKey = "season",
  yKey = "value",
  color = "var(--team-primary)",
  height = 220,
  yLabel,
}: {
  data: any[];
  xKey?: string;
  yKey?: string;
  color?: string;
  height?: number;
  yLabel?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }}>
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
        <Line type="monotone" dataKey={yKey} stroke={color} strokeWidth={2}
          dot={{ r: 3 }} activeDot={{ r: 5 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
