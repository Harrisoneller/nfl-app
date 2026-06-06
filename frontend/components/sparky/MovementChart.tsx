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
import { SparkyMovementPoint } from "@/lib/api";

/**
 * Line-movement chart: de-vigged home win probability across the captured
 * snapshots (T1 -> T3/T4). The 50% reference line makes favorite/underdog
 * flips obvious.
 */
export function MovementChart({
  movement,
  homeId,
  awayId,
  height = 220,
}: {
  movement: SparkyMovementPoint[];
  homeId?: string | null;
  awayId?: string | null;
  height?: number;
}) {
  if (!movement || movement.length < 2) {
    return (
      <div className="text-xs text-muted py-6 text-center">
        Not enough snapshots yet to chart movement. Lines accrue as the market is captured over time.
      </div>
    );
  }

  const data = movement.map((p) => ({
    label: p.label,
    home: Math.round(p.home_prob * 1000) / 10,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
        <XAxis dataKey="label" stroke="#7c8aa0" fontSize={11} />
        <YAxis
          domain={[0, 100]}
          stroke="#7c8aa0"
          fontSize={11}
          tickFormatter={(v) => `${v}%`}
          width={42}
        />
        <ReferenceLine y={50} stroke="rgba(148,163,184,0.4)" strokeDasharray="4 4" />
        <Tooltip
          contentStyle={{
            background: "#0b1018",
            border: "1px solid rgba(45,212,191,0.3)",
            borderRadius: 10,
            fontSize: 12,
          }}
          labelStyle={{ color: "#9fb3c8" }}
          formatter={(v: number) => [`${v}%`, `${homeId ?? "Home"} win prob`]}
        />
        <Line
          type="monotone"
          dataKey="home"
          stroke="#22d3ee"
          strokeWidth={2.5}
          dot={{ r: 3, fill: "#10b981" }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
