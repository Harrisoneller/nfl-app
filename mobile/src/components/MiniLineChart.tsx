import { View } from "react-native";
import Svg, { Path, Line, Circle, Defs, LinearGradient, Stop } from "react-native-svg";
import { colors } from "@/theme/theme";

export type Series = {
  points: Array<number | null>;
  color?: string;
};

/**
 * Lightweight multi-series line chart built on react-native-svg. Replaces
 * Recharts for trend lines (Elo history, metric trends, line movement).
 * Auto-scales to the combined min/max across all series.
 */
export function MiniLineChart({
  series,
  width,
  height = 160,
  showArea = true,
}: {
  series: Series[];
  width: number;
  height?: number;
  showArea?: boolean;
}) {
  const padX = 6;
  const padY = 10;
  const innerW = Math.max(1, width - padX * 2);
  const innerH = Math.max(1, height - padY * 2);

  const all = series.flatMap((s) => s.points.filter((v): v is number => v != null));
  const min = all.length ? Math.min(...all) : 0;
  const max = all.length ? Math.max(...all) : 1;
  const span = max - min || 1;

  const maxLen = Math.max(1, ...series.map((s) => s.points.length));

  const x = (i: number) =>
    padX + (maxLen <= 1 ? innerW / 2 : (i / (maxLen - 1)) * innerW);
  const y = (v: number) => padY + innerH - ((v - min) / span) * innerH;

  function pathFor(points: Array<number | null>): string {
    let d = "";
    let started = false;
    points.forEach((v, i) => {
      if (v == null) return;
      const cmd = started ? "L" : "M";
      d += `${cmd}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      started = true;
    });
    return d.trim();
  }

  function areaFor(points: Array<number | null>): string {
    const valid = points
      .map((v, i) => ({ v, i }))
      .filter((p): p is { v: number; i: number } => p.v != null);
    if (valid.length < 2) return "";
    let d = `M${x(valid[0].i).toFixed(1)},${(height - padY).toFixed(1)} `;
    valid.forEach((p) => {
      d += `L${x(p.i).toFixed(1)},${y(p.v).toFixed(1)} `;
    });
    d += `L${x(valid[valid.length - 1].i).toFixed(1)},${(height - padY).toFixed(1)} Z`;
    return d;
  }

  return (
    <View>
      <Svg width={width} height={height}>
        <Defs>
          {series.map((s, idx) => (
            <LinearGradient key={idx} id={`grad${idx}`} x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={s.color ?? colors.accent} stopOpacity={0.28} />
              <Stop offset="1" stopColor={s.color ?? colors.accent} stopOpacity={0} />
            </LinearGradient>
          ))}
        </Defs>

        {/* baseline */}
        <Line
          x1={padX}
          y1={height - padY}
          x2={width - padX}
          y2={height - padY}
          stroke={colors.border}
          strokeWidth={1}
        />

        {showArea &&
          series.map((s, idx) =>
            areaFor(s.points) ? (
              <Path key={`a${idx}`} d={areaFor(s.points)} fill={`url(#grad${idx})`} />
            ) : null,
          )}

        {series.map((s, idx) => (
          <Path
            key={`l${idx}`}
            d={pathFor(s.points)}
            stroke={s.color ?? colors.accent}
            strokeWidth={2}
            fill="none"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {/* last-point dots */}
        {series.map((s, idx) => {
          const lastIdx = lastValidIndex(s.points);
          if (lastIdx < 0) return null;
          const v = s.points[lastIdx] as number;
          return (
            <Circle
              key={`d${idx}`}
              cx={x(lastIdx)}
              cy={y(v)}
              r={3}
              fill={s.color ?? colors.accent}
            />
          );
        })}
      </Svg>
    </View>
  );
}

function lastValidIndex(points: Array<number | null>): number {
  for (let i = points.length - 1; i >= 0; i--) {
    if (points[i] != null) return i;
  }
  return -1;
}
