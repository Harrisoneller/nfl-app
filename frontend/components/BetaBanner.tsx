"use client";

/**
 * Inline banner flagging a section as in-progress. Use sparingly — only
 * for parts of the app where data quality or UX is still actively being
 * iterated on, so users know to lower their expectations.
 */
export function BetaBanner({ title, children }: { title?: string; children?: React.ReactNode }) {
  return (
    <div
      className="rounded-lg border px-4 py-3 text-sm flex items-start gap-3"
      style={{ borderColor: "#eab308", background: "rgba(234, 179, 8, 0.06)" }}
    >
      <span
        className="text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded mt-0.5"
        style={{ background: "#eab308", color: "#0b0d10" }}
      >
        Beta
      </span>
      <div>
        {title && <div className="font-medium mb-0.5">{title}</div>}
        <div className="text-muted text-xs leading-relaxed">
          {children ?? (
            <>This section is in active development. Some data may be incomplete
            or take a moment to populate, and the layout is still evolving.</>
          )}
        </div>
      </div>
    </div>
  );
}

export function BetaPill() {
  return (
    <span
      className="inline-flex items-center text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded"
      style={{ background: "#eab308", color: "#0b0d10" }}
    >
      Beta
    </span>
  );
}
