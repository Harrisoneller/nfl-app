/**
 * Game-headline display. The numbers on the card are the model fields.
 * The market line is formatted on its own. Nothing here averages the two.
 *
 * Spread sign: negative means the home team is favored (sportsbook convention).
 * A spread of 0 is pick'em. A null market number is missing, not zero.
 */

export const BLEND_BASIS = "market-blend-v1";
export const OVERRIDE_BASIS = "admin_override";
export const V5_VERSION = "qb-ridge-v5";
export const V4_VERSION = "epa-elo-ctx-v4";

export type HeadlinePrediction = {
  predicted_spread: number;
  predicted_total: number;
  predicted_home_score: number;
  predicted_away_score: number;
  home_win_prob: number;
  away_win_prob: number;
  prediction_basis?: string | null;
  model_version?: string | null;
};

export type HeadlineNumbers = {
  spread: number;
  total: number;
  homeScore: number;
  awayScore: number;
  homeWinProb: number;
  awayWinProb: number;
};

export function headlineNumbers(prediction: HeadlinePrediction): HeadlineNumbers {
  return {
    spread: prediction.predicted_spread,
    total: prediction.predicted_total,
    homeScore: prediction.predicted_home_score,
    awayScore: prediction.predicted_away_score,
    homeWinProb: prediction.home_win_prob,
    awayWinProb: prediction.away_win_prob,
  };
}

/** What the UI may call the headline. A blend is never labeled qb-ridge-v5. */
export function headlineSourceLabel(prediction: {
  prediction_basis?: string | null;
  model_version?: string | null;
}): string {
  if (prediction.prediction_basis === BLEND_BASIS) return "Market blend";
  if (prediction.prediction_basis === OVERRIDE_BASIS) return "Admin override";
  if (prediction.model_version === V5_VERSION) return V5_VERSION;
  if (prediction.model_version === V4_VERSION) return "v4 fallback";
  return "Model";
}

export function isMarketBlended(prediction: { prediction_basis?: string | null }): boolean {
  return prediction.prediction_basis === BLEND_BASIS;
}

/**
 * Home-spread display. `KC -3.5` when KC is home and favored, `BUF -3.5` when
 * the away team is favored, `PK` at pick'em.
 */
export function formatHomeSpread(
  spread: number | null | undefined,
  homeId: string,
  awayId: string,
): string {
  if (spread == null || Number.isNaN(Number(spread))) return "—";
  const value = Number(spread);
  if (Math.abs(value) < 0.05) return "PK";
  if (value < 0) return `${homeId} ${value.toFixed(1)}`;
  return `${awayId} ${(-value).toFixed(1)}`;
}

export function formatMarketLine(market: {
  spread_home?: number | null;
  total?: number | null;
  consensus_home_prob?: number | null;
} | null | undefined, homeId: string, awayId: string): string {
  if (!market) return "Market line unavailable";
  const hasSpread = market.spread_home != null && !Number.isNaN(Number(market.spread_home));
  const hasTotal = market.total != null && !Number.isNaN(Number(market.total));
  const hasProb = market.consensus_home_prob != null && !Number.isNaN(Number(market.consensus_home_prob));
  if (!hasSpread && !hasTotal && !hasProb) return "Market line unavailable";

  const parts: string[] = [];
  if (hasSpread) parts.push(formatHomeSpread(Number(market.spread_home), homeId, awayId));
  else if (hasProb) {
    const p = Number(market.consensus_home_prob);
    const side = p >= 0.5 ? homeId : awayId;
    parts.push(`${side} ${Math.round(Math.max(p, 1 - p) * 100)}%`);
  }
  if (hasTotal) parts.push(`O/U ${Number(market.total).toFixed(1)}`);
  return parts.join(" · ");
}

/**
 * edge.spread is model spread minus market spread. Negative means the model
 * likes the home team more than the line does.
 */
export function formatSpreadEdge(
  edge: number | null | undefined,
  homeId: string,
  awayId: string,
): string | null {
  if (edge == null || Number.isNaN(Number(edge))) return null;
  const value = Number(edge);
  if (Math.abs(value) < 0.05) return "Pick'em";
  const side = value < 0 ? homeId : awayId;
  return `${side} +${Math.abs(value).toFixed(1)}`;
}

export function headlineIsConsistent(prediction: HeadlinePrediction): boolean {
  const h = headlineNumbers(prediction);
  const margin = h.homeScore - h.awayScore;
  const total = h.homeScore + h.awayScore;
  if (Math.abs(margin + h.spread) > 0.2) return false;
  if (Math.abs(total - h.total) > 0.2) return false;
  if (Math.abs(h.homeWinProb + h.awayWinProb - 1) > 1e-6) return false;
  if (h.spread < -0.05 && h.homeWinProb <= 0.5) return false;
  if (h.spread > 0.05 && h.homeWinProb >= 0.5) return false;
  return true;
}
