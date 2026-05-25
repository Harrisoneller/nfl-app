"use client";

import { GamePrediction, PredictionExplainability } from "@/lib/api";

export function WhyPredictionPanel({
  game,
  compact = false,
}: {
  game: Pick<GamePrediction, "home_team_id" | "away_team_id" | "prediction">;
  compact?: boolean;
}) {
  const explain = game.prediction.explainability;
  const contributors = (explain?.top_contributors ?? []).slice(0, 3);
  if (contributors.length === 0) return null;
  const confidence = explain?.confidence_context;

  return (
    <div className={`rounded-lg border divider bg-bg/60 ${compact ? "p-2.5" : "p-3"} space-y-2`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wide text-muted">Why this prediction?</div>
        <span className="text-[10px] text-muted">{renderMethod(explain)}</span>
      </div>
      <ul className="space-y-1.5">
        {contributors.map((c) => {
          const favorsHome = c.direction === "home";
          const team = favorsHome ? game.home_team_id : game.away_team_id;
          const tone = c.impact >= 0 ? "text-emerald-300" : "text-orange-300";
          return (
            <li key={c.feature} className="text-[11px] flex items-center justify-between gap-2">
              <span className="text-muted truncate">{c.label}</span>
              <span className={`tabular-nums whitespace-nowrap ${tone}`}>
                {c.impact >= 0 ? "+" : ""}
                {c.impact.toFixed(2)} {team}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="text-[10px] text-muted">
        {confidence?.tier ?? game.prediction.confidence_tier ?? "low"} confidence
        {confidence?.calibration_score != null
          ? ` · cal ${Math.round(confidence.calibration_score * 100)}%`
          : game.prediction.calibration_score != null
            ? ` · cal ${Math.round(game.prediction.calibration_score * 100)}%`
            : ""}
      </div>
    </div>
  );
}

function renderMethod(explainability?: PredictionExplainability): string {
  if (!explainability?.method) return "heuristic";
  return explainability.method.replaceAll("_", " ");
}
