import { FreshnessSnapshot } from "@/lib/api";

export function FreshnessBadges({ freshness }: { freshness: FreshnessSnapshot | null }) {
  if (!freshness || freshness.modules.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {freshness.modules.map((m) => {
        const tone =
          m.status === "ok"
            ? "border-emerald-500/40 text-emerald-300"
            : m.status === "warn"
              ? "border-amber-500/40 text-amber-300"
              : "border-rose-500/40 text-rose-300";
        const ageText = m.age_seconds == null ? "n/a" : humanAge(m.age_seconds);
        return (
          <span
            key={m.module}
            className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[10px] uppercase tracking-wide ${tone}`}
            title={`Last update: ${m.last_updated_at ?? "unknown"} (SLA ${humanAge(m.sla_seconds)})`}
          >
            {m.module}: {m.status} ({ageText})
          </span>
        );
      })}
    </div>
  );
}

function humanAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}
