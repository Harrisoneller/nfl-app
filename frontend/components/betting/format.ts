// Small formatters shared across the bet-tracker UI.

export function american(price: number | null | undefined): string {
  if (price == null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

export function signedUnits(u: number | null | undefined): string {
  if (u == null) return "—";
  const s = u > 0 ? "+" : "";
  return `${s}${u.toFixed(2)}u`;
}

export function signedPct(p: number | null | undefined): string {
  if (p == null) return "—";
  const s = p > 0 ? "+" : "";
  return `${s}${p.toFixed(1)}%`;
}

export function signedDollars(d: number | null | undefined): string {
  if (d == null) return "—";
  const s = d < 0 ? "-" : "";
  return `${s}$${Math.abs(d).toFixed(2)}`;
}

export function resultColor(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-muted";
  return v > 0 ? "text-green-500" : "text-red-400";
}

export const STATUS_STYLES: Record<string, string> = {
  pending: "bg-bg text-muted border-divider",
  won: "bg-green-500/15 text-green-500 border-green-500/30",
  lost: "bg-red-500/15 text-red-400 border-red-500/30",
  push: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  void: "bg-bg text-muted border-divider",
};
