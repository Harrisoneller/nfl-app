"use client";
import { useEffect, useRef, useState } from "react";
import { SparkySignal } from "@/lib/api";

function glyph(sev: string): string {
  if (sev === "bullish") return "↑";
  if (sev === "warning") return "⚠";
  return "•";
}

/** A single colored signal tag.
 *
 * Behavior
 * --------
 * - Desktop hover still shows the native `title` (fast and accessible).
 * - Click/tap also expands a bubble with the same explanation, so mobile users
 *   (who can't hover) actually get the plain-English read.
 *
 * This is the change the SOW calls "plain-English explanation templates for the
 * user interface" — the explanation already came from the backend, this just
 * makes it reachable without a mouse.
 */
export function SignalPill({ signal }: { signal: SparkySignal }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);
  const cls =
    signal.severity === "bullish"
      ? "sparky-pill sparky-pill--bullish"
      : signal.severity === "warning"
        ? "sparky-pill sparky-pill--warning"
        : "sparky-pill sparky-pill--info";

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span
      ref={ref}
      className={`sparky-help ${open ? "is-open" : ""}`}
      style={{ marginLeft: 0 }}
    >
      <button
        type="button"
        className={`${cls} sparky-help__btn`}
        style={{
          width: "auto",
          height: "auto",
          fontSize: "0.68rem",
          padding: "0.15rem 0.55rem",
          color: "inherit",
          background: "inherit",
          border: "1px solid transparent",
          cursor: "pointer",
        }}
        title={`${signal.label} — ${signal.explanation}`}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        <span aria-hidden style={{ marginRight: "0.3rem" }}>
          {glyph(signal.severity)}
        </span>
        {signal.label}
      </button>
      <span role="tooltip" className="sparky-help__bubble">
        <span className="sparky-help__title">{signal.label}</span>
        <span className="sparky-help__body">{signal.explanation}</span>
      </span>
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
