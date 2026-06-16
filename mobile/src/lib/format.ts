// Small formatting helpers shared across screens. Ported/condensed from the
// web app's various `format.ts` helpers (Sparky, odds, etc.).

/** American odds with explicit sign: 150 -> "+150", -120 -> "-120". */
export function americanOdds(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
}

/** Probability 0..1 -> "62%". Pass digits for decimals. */
export function pct(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Already-percent value (0..100) -> "62%". */
export function pctRaw(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

/** Signed number: 3 -> "+3", -3.5 -> "-3.5". */
export function signed(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n.toFixed(digits));
  return v > 0 ? `+${v}` : `${v}`;
}

export function num(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

/** A spread point from the home team's perspective -> "PHI -3.5" style label. */
export function fmtSpread(point: number | null | undefined): string {
  if (point == null) return "PK";
  if (point === 0) return "PK";
  return signed(point, 1);
}

/** "Sun 1:00 PM" style kickoff label from an ISO timestamp. */
export function kickoff(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "2h ago" relative time. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

/** Tier/grade -> color. Used for confidence pills, Elo grades, matchup grades. */
export function gradeColor(grade: string | null | undefined): string {
  const g = (grade ?? "").toUpperCase();
  if (g.startsWith("A")) return "#34d399";
  if (g.startsWith("B")) return "#a3e635";
  if (g.startsWith("C")) return "#f59e0b";
  if (g.startsWith("D")) return "#fb923c";
  if (g.startsWith("F")) return "#f43f5e";
  return "#9ba3af";
}

export function confidenceColor(tier: string | null | undefined): string {
  switch ((tier ?? "").toLowerCase()) {
    case "high":
      return "#34d399";
    case "medium":
      return "#f59e0b";
    case "low":
      return "#f43f5e";
    default:
      return "#9ba3af";
  }
}
