// Shared Sparky formatting helpers.

export function americanOdds(price: number | null | undefined): string {
  if (price == null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function pctPoints(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  return `${value.toFixed(digits)}%`;
}

export const CLASSIFICATION_LABEL: Record<string, string> = {
  anchor: "Anchor",
  strong_lean: "Strong Lean",
  lean: "Lean",
  coin_flip: "Coin Flip",
  upset_watch: "Upset Watch",
};

export function classificationLabel(c: string | null | undefined): string {
  if (!c) return "—";
  return CLASSIFICATION_LABEL[c] ?? c;
}

export function kickoff(iso: string | null | undefined): string {
  if (!iso) return "TBD";
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// Confidence -> emerald/amber/slate accent for rings & bars.
export function confidenceColor(score: number): string {
  if (score >= 78) return "#10b981";
  if (score >= 64) return "#22d3ee";
  if (score >= 52) return "#60a5fa";
  return "#94a3b8";
}
