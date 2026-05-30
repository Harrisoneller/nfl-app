"use client";
import { SparkySignal } from "@/lib/api";

function glyph(sev: string): string {
  if (sev === "bullish") return "↑";
  if (sev === "warning") return "⚠";
  return "•";
}

/** A single colored signal tag with a tooltip carrying the full explanation. */
export function SignalPill({ signal }: { signal: SparkySignal }) {
  const cls =
    signal.severity === "bullish"
      ? "sparky-pill sparky-pill--bullish"
      : signal.severity === "warning"
        ? "sparky-pill sparky-pill--warning"
        : "sparky-pill sparky-pill--info";
  return (
    <span className={cls} title={signal.explanation}>
      <span aria-hidden>{glyph(signal.severity)}</span>
      {signal.label}
    </span>
  );
}

/** Wrap a set of signals, optionally limited with a "+N" overflow chip. */
export function SignalPills({
  signals,
  limit,
}: {
  signals: SparkySignal[];
  limit?: number;
}) {
  if (!signals?.length) {
    return <span className="text-xs text-muted">No standout market signals</span>;
  }
  const shown = limit ? signals.slice(0, limit) : signals;
  const extra = limit ? Math.max(0, signals.length - limit) : 0;
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.map((s) => (
        <SignalPill key={s.key} signal={s} />
      ))}
      {extra > 0 && (
        <span className="sparky-pill sparky-pill--info" title={signals.slice(limit).map((s) => s.label).join(", ")}>
          +{extra}
        </span>
      )}
    </div>
  );
}
