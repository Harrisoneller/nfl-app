/**
 * Name the side that holds a spread edge.
 *
 * `spread_edge` is model_spread − market_spread on the home-negative
 * convention. Negative edge = model likes the home side more than the books.
 */
export function spreadEdgeSide(
  spreadEdge: number | null | undefined,
  homeId: string,
  awayId: string,
): string | null {
  if (spreadEdge == null || Number.isNaN(Number(spreadEdge))) return null;
  const v = Number(spreadEdge);
  if (Math.abs(v) < 0.05) return null;
  return v < 0 ? homeId : awayId;
}
