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

/** Plain-English explanation of each pick tier — surfaced as the chip's title.
 *  Re-exported from HelpTip's PICK_TIER_DESCRIPTIONS would create a server/client
 *  bundling dance; the tier copy is short so we duplicate it here intentionally. */
export const CLASSIFICATION_DESCRIPTION: Record<string, string> = {
  anchor: "Sparky's strongest call — high confidence with multi-book agreement.",
  strong_lean: "Above-average confidence; model and market agree but not a slam dunk.",
  lean: "Modest edge — worth a look but not a featured play.",
  coin_flip: "Near pick'em — no real edge either way; usually skip.",
  upset_watch: "Sparky's model rates the underdog meaningfully better than the market.",
};

export function classificationLabel(c: string | null | undefined): string {
  if (!c) return "—";
  return CLASSIFICATION_LABEL[c] ?? c;
}

export function classificationDescription(c: string | null | undefined): string {
  if (!c) return "";
  return CLASSIFICATION_DESCRIPTION[c] ?? "";
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
