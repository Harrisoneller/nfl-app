"use client";

/**
 * Lightweight info badge for "the data you're seeing isn't quite what the
 * page says." Two flavors:
 *  - `fallback`: backend silently fell back from requested season to an
 *    older one because the requested season has no data yet (offseason).
 *  - `preview`: the page header says e.g. "2026" but the season hasn't
 *    started — these are projections, not actuals.
 */
export function DataSourceBadge({
  fallbackFrom,
  upcoming,
  className = "",
}: {
  fallbackFrom?: number;
  upcoming?: boolean;
  className?: string;
}) {
  if (!fallbackFrom && !upcoming) return null;
  if (upcoming) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${className}`}
        style={{ borderColor: "#eab308", color: "#eab308" }}
        title="Season hasn't started yet — these are model projections"
      >
        Preview
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${className}`}
      style={{ borderColor: "#f97316", color: "#f97316" }}
      title={`Requested season had no data; showing data from the most recent completed season.`}
    >
      Showing earlier data
    </span>
  );
}
