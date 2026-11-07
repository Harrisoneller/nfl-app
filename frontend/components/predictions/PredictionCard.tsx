"use client";
import Link from "next/link";
import { GamePrediction } from "@/lib/api";
import { WinProbBar } from "./WinProbBar";
import { ExplainPopover } from "./ExplainPopover";
import { TeamLogo } from "../TeamLogo";
import { usePersona } from "@/context/PersonaProvider";
import { WhyPredictionPanel } from "./WhyPredictionPanel";
import { MarketBlendLine } from "./MarketBlendLine";
import { formatHomeSpread, headlineNumbers, headlineSourceLabel } from "@/lib/headline";

/**
 * Compact game prediction card. Logos for casual readability, Elo + win
 * prob + spread + total for analytics readers.
 */
export function PredictionCard({ game }: { game: GamePrediction }) {
  const { persona } = usePersona();
  const p = game.prediction;
  const headline = headlineNumbers(p);
  const sourceLabel = headlineSourceLabel(p);
  const spreadLabel = formatHomeSpread(headline.spread, game.home_team_id, game.away_team_id);
  const played = game.home_score != null && game.away_score != null;
  const winner = played ? (
    (game.home_score! > game.away_score!) ? game.home_team_id : (game.away_score! > game.home_score!) ? game.away_team_id : null
  ) : null;

  const confidenceTone =
    p.confidence_tier === "high"
      ? "text-emerald-400"
      : p.confidence_tier === "medium"
        ? "text-amber-300"
        : "text-rose-300";

  return (
    <div className="panel panel-hover-lift p-3 space-y-2">
      <div className="flex items-center justify-between text-sm gap-2">
        <Link href={`/teams/${game.away_team_id}`} className="flex items-center gap-1.5 font-medium hover:underline">
          <TeamLogo teamId={game.away_team_id} size={22} />
          <span className={winner === game.away_team_id ? "" : winner ? "text-muted" : ""}>{game.away_team_id}</span>
        </Link>
        <span className="text-muted text-xs">@</span>
        <Link href={`/teams/${game.home_team_id}`} className="flex items-center gap-1.5 font-medium hover:underline">
          <span className={winner === game.home_team_id ? "" : winner ? "text-muted" : ""}>{game.home_team_id}</span>
          <TeamLogo teamId={game.home_team_id} size={22} />
        </Link>
      </div>
      <div className="text-[11px] text-muted">{game.gameday || `Wk ${game.week}`}</div>

      {played ? (
        <div className="text-sm tabular-nums font-medium">
          Final: {game.away_score} – {game.home_score}
        </div>
      ) : (
        <>
          <ExplainPopover game={game}>
            <WinProbBar
              awayTeam={game.away_team_id}
              awayProb={headline.awayWinProb}
              homeTeam={game.home_team_id}
              homeProb={headline.homeWinProb}
            />
          </ExplainPopover>
          {persona !== "fantasy" && (
            <div className="flex items-center justify-between text-[11px] text-muted pt-1">
              <span>
                Model spread: <span className="text-text font-medium">{spreadLabel}</span>
                <span
                  className="ml-1.5 rounded px-1 py-px text-[9px] uppercase tracking-wide bg-team-primary/10 text-team-primary"
                  title={
                    sourceLabel === "Market blend"
                      ? "This cached headline was blended with the current line. It is not qb-ridge-v5."
                      : sourceLabel === "qb-ridge-v5"
                        ? "qb-ridge-v5 model. Not blended with this week's line. The model still uses a prior from previous games' closing lines."
                        : sourceLabel === "v4 fallback"
                          ? "v5 inputs were not used for this row. The number is the legacy model."
                          : "Headline source."
                  }
                >
                  {sourceLabel}
                </span>
              </span>
              <span>Model O/U <span className="text-text tabular-nums">{headline.total.toFixed(1)}</span></span>
            </div>
          )}
          {persona !== "fantasy" && <MarketBlendLine game={game} />}
          {persona === "fantasy" && (
            <div className="flex items-center justify-between text-[11px] text-muted pt-1">
              <span>Scoring env: <span className="text-text tabular-nums">{p.predicted_total.toFixed(1)}</span></span>
              <span>{p.game_script ?? "neutral script"}</span>
            </div>
          )}
          {(p.home_win_prob_interval_80 || p.calibration_score != null) && persona !== "fantasy" && (
            <div className="flex items-center justify-between text-[10px] text-muted pt-0.5">
              <span>
                80% CI{" "}
                {p.home_win_prob_interval_80
                  ? `${Math.round(p.home_win_prob_interval_80[0] * 100)}-${Math.round(p.home_win_prob_interval_80[1] * 100)}%`
                  : "—"}
              </span>
              <span className={confidenceTone}>
                {p.confidence_tier ?? "low"} confidence
                {p.calibration_score != null ? ` · cal ${Math.round(p.calibration_score * 100)}%` : ""}
              </span>
            </div>
          )}
          {persona !== "fantasy" && <WhyPredictionPanel game={game} compact />}
        </>
      )}
    </div>
  );
}
