"use client";
import Link from "next/link";
import { GamePrediction } from "@/lib/api";
import { WinProbBar } from "./WinProbBar";
import { ExplainPopover } from "./ExplainPopover";
import { TeamLogo } from "../TeamLogo";

/**
 * Compact game prediction card. Logos for casual readability, Elo + win
 * prob + spread + total for analytics readers, ML overlay when it diverges.
 */
export function PredictionCard({ game }: { game: GamePrediction }) {
  const p = game.prediction;
  const fav = p.predicted_spread <= 0 ? game.home_team_id : game.away_team_id;
  const absSpread = Math.abs(p.predicted_spread);
  const played = game.home_score != null && game.away_score != null;
  const winner = played ? (
    (game.home_score! > game.away_score!) ? game.home_team_id : (game.away_score! > game.home_score!) ? game.away_team_id : null
  ) : null;

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
              awayProb={p.away_win_prob}
              homeTeam={game.home_team_id}
              homeProb={p.home_win_prob}
            />
          </ExplainPopover>
          <div className="flex items-center justify-between text-[11px] text-muted pt-1">
            <span>Pick: <span className="text-text font-medium">{fav} -{absSpread.toFixed(1)}</span></span>
            <span>O/U <span className="text-text tabular-nums">{p.predicted_total.toFixed(1)}</span></span>
          </div>
          {game.ml_prediction && Math.abs(game.ml_prediction.predicted_spread - p.predicted_spread) > 0.5 && (
            <div className="text-[10px] text-muted pt-0.5">
              ML: {game.ml_prediction.predicted_spread <= 0 ? game.home_team_id : game.away_team_id}{" "}
              -{Math.abs(game.ml_prediction.predicted_spread).toFixed(1)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
