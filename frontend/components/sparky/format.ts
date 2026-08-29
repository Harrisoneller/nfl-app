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

/** Prediction tiers describe *how sure Sparky is who wins* — nothing more.
 *  "Anchor" used to read as "anchor your ticket to this", which is close to the
 *  opposite of true: the tier peaks where the model and market agree, i.e.
 *  where the price has already taken the edge. Renamed to say what it measures.
 *  Whether a game is worth betting is the Value Board's question, not this one's. */
export const CLASSIFICATION_LABEL: Record<string, string> = {
  anchor: "Near Lock",
  strong_lean: "Strong Lean",
  lean: "Lean",
  coin_flip: "Coin Flip",
  upset_watch: "Upset Watch",
};

/** Plain-English explanation of each pick tier — surfaced as the chip's title.
 *  Re-exported from HelpTip's PICK_TIER_DESCRIPTIONS would create a server/client
 *  bundling dance; the tier copy is short so we duplicate it here intentionally. */
export const CLASSIFICATION_DESCRIPTION: Record<string, string> = {
  anchor:
    "Sparky is very confident who wins — and that is a forecast, not a bet. The market prices these games too, so the moneyline here is usually terrible value. Check the Value Board before staking anything.",
  strong_lean:
    "Above-average confidence in the winner; model and market broadly agree.",
  lean: "Modest confidence in the winner.",
  coin_flip: "Near pick'em — the winner is genuinely uncertain. Note this says nothing about whether there is value in the price.",
  upset_watch:
    "Sparky's model rates the underdog meaningfully better than the market does — the one prediction tier that regularly does coincide with a bet.",
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

/** Inverse of implied American probability. Matches backend `implied_to_american`. */
export function impliedToAmerican(p: number | null | undefined): number | null {
  if (p == null || !(p > 0 && p < 1)) return null;
  if (p >= 0.5) return Math.round((-100 * p) / (1 - p));
  return Math.round((100 * (1 - p)) / p);
}

/** Offered vs fair, in American cents. Positive = the price is better than fair. */
export function centsOfValue(offered: number, fair: number): number {
  const ladder = (price: number) => {
    if (price >= 100) return price - 100;
    if (price <= -100) return price + 100;
    return 0;
  };
  return Math.round(ladder(offered) - ladder(fair));
}

/** True only when both sides resolved to a modeled NFL team id. */
export function isNflMatchup(
  homeId?: string | null,
  awayId?: string | null,
): boolean {
  return Boolean(homeId?.trim() && awayId?.trim());
}

// Confidence -> emerald/amber/slate accent for rings & bars.
export function confidenceColor(score: number): string {
  if (score >= 78) return "#10b981";
  if (score >= 64) return "#22d3ee";
  if (score >= 52) return "#60a5fa";
  return "#94a3b8";
}
