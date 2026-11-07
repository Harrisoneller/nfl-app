"use client";

import { GamePrediction } from "@/lib/api";
import { formatMarketLine, formatSpreadEdge } from "@/lib/headline";

/**
 * Market line and model-vs-market edge, labeled separately from the headline.
 * Missing lines stay missing. A spread of 0 renders as pick'em.
 */
export function MarketBlendLine({
  game,
}: {
  game: Pick<GamePrediction, "home_team_id" | "away_team_id" | "prediction">;
}) {
  const p = game.prediction;
  const m = p.market;
  const line = formatMarketLine(m, game.home_team_id, game.away_team_id);
  const sources = m
    ? (m.books > 0 ? `${m.books} book${m.books === 1 ? "" : "s"}` : "") +
      (m.sources?.kalshi ? (m.books > 0 ? " + Kalshi" : "Kalshi") : "")
    : "";
  const delta = m?.movement?.delta_home_prob ?? 0;
  const moveSide = m && Math.abs(delta) >= 0.02
    ? (delta > 0 ? game.home_team_id : game.away_team_id)
    : null;

  return (
    <div className="flex items-center justify-between gap-2 text-[10px] text-muted pt-0.5">
      <span className="truncate">
        Market: <span className="text-text">{line}</span>
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
  // edge.spread = model − market on the home line. Negative = model likes HOME.
  const spreadLabel = formatSpreadEdge(edge.spread, game.home_team_id, game.away_team_id);
  if (edge.spread != null && Math.abs(edge.spread) >= 1.0 && spreadLabel && spreadLabel !== "Pick'em") {
    const exp = edge.spread_expected_pts;
    return (
      <span
        className="whitespace-nowrap rounded px-1 py-px bg-emerald-500/10 text-emerald-300"
        title={
          exp != null
            ? `Model disagrees with the market by ${Math.abs(edge.spread).toFixed(1)} pts. ` +
              `Historically ~${Math.round((edge.realization?.spread ?? 0) * 100)}% of that ` +
              `showed up in results, so the expected edge is ~${Math.abs(exp).toFixed(1)} pts.`
            : undefined
        }
      >
        Edge: {spreadLabel}
        {exp != null && <span className="text-muted"> (exp {Math.abs(exp).toFixed(1)})</span>}
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
