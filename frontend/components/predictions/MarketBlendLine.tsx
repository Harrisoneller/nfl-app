"use client";

import { GamePrediction } from "@/lib/api";

/**
 * Compact market-context line for a prediction card: consensus line + sources,
 * line-movement arrow, and a model-edge chip when the model materially
 * disagrees with the market. Renders nothing for model-only games.
 */
export function MarketBlendLine({
  game,
}: {
  game: Pick<GamePrediction, "home_team_id" | "away_team_id" | "prediction">;
}) {
  const p = game.prediction;
  const m = p.market;
  if (!m) return null;

  const mktFav = m.spread_home != null
    ? (m.spread_home <= 0 ? game.home_team_id : game.away_team_id)
    : (m.consensus_home_prob >= 0.5 ? game.home_team_id : game.away_team_id);

  const sources =
    (m.books > 0 ? `${m.books} book${m.books === 1 ? "" : "s"}` : "") +
    (m.sources?.kalshi ? (m.books > 0 ? " + Kalshi" : "Kalshi") : "");

  // Line movement: which side has the money coming in since open.
  const delta = m.movement?.delta_home_prob ?? 0;
  const moveSide = Math.abs(delta) >= 0.02
    ? (delta > 0 ? game.home_team_id : game.away_team_id)
    : null;

  return (
    <div className="flex items-center justify-between gap-2 text-[10px] text-muted pt-0.5">
      <span className="truncate">
        Mkt:{" "}
        <span className="text-text">
          {m.spread_home != null
            ? `${mktFav} -${Math.abs(m.spread_home).toFixed(1)}`
            : `${mktFav} ${Math.round(Math.max(m.consensus_home_prob, 1 - m.consensus_home_prob) * 100)}%`}
        </span>
        {m.total != null && <> · O/U <span className="tabular-nums text-text">{m.total.toFixed(1)}</span></>}
        {sources && <> · {sources}</>}
        {moveSide && (
          <span className="text-sky-300"> · line → {moveSide}</span>
        )}
      </span>
      <EdgeChip game={game} />
    </div>
  );
}

/** Model-vs-market edge, shown only when it clears a display threshold. */
export function EdgeChip({
  game,
}: {
  game: Pick<GamePrediction, "home_team_id" | "away_team_id" | "prediction">;
}) {
  const edge = game.prediction.edge;
  if (!edge) return null;

  // Prefer the spread expression when available; fall back to win prob.
  if (edge.spread != null && Math.abs(edge.spread) >= 1.0) {
    // edge.spread = model − market (home line). Negative = model likes HOME
    // more than the market (model's home line is more negative).
    const side = edge.spread < 0 ? game.home_team_id : game.away_team_id;
    return (
      <span className="whitespace-nowrap rounded px-1 py-px bg-emerald-500/10 text-emerald-300">
        Edge: {side} +{Math.abs(edge.spread).toFixed(1)}
      </span>
    );
  }
  if (Math.abs(edge.home_win_prob) >= 0.04) {
    const side = edge.home_win_prob > 0 ? game.home_team_id : game.away_team_id;
    return (
      <span className="whitespace-nowrap rounded px-1 py-px bg-emerald-500/10 text-emerald-300">
        Edge: {side} +{Math.round(Math.abs(edge.home_win_prob) * 100)}%
      </span>
    );
  }
  return null;
}
