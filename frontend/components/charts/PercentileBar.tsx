import { pctColor } from "@/lib/metrics";

/**
 * Horizontal percentile bar — the PlayerProfiler-style "92nd %ile" pill.
 */
export function PercentileBar({
  label,
  value,
  percentile,
}: {
  label: string;
  value: string;
  percentile: number | null;
}) {
  const p = percentile == null ? null : Math.max(0, Math.min(100, percentile));
  const color = pctColor(p);
  return (
    <div className="py-1.5">
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-muted">{label}</span>
        <span className="font-medium tabular-nums">{value}</span>
      </div>
      <div className="mt-1 relative h-1.5 rounded-full bg-bg overflow-hidden border divider">
        {p != null && (
          <div
            className="absolute inset-y-0 left-0"
            style={{ width: `${p}%`, background: color }}
          />
        )}
      </div>
      <div className="flex justify-between text-[10px] mt-0.5 text-muted">
        <span>0</span>
        <span style={{ color }}>{p == null ? "n/a" : `${p.toFixed(0)}th %ile`}</span>
        <span>100</span>
      </div>
    </div>
  );
}
