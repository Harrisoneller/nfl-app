"use client";
import Link from "next/link";
import { GamePrediction } from "@/lib/api";
import { WinProbBar } from "./WinProbBar";

/**
 * Compact game prediction card — shown on the home page scoreboard strip and
 * the predictions tab. Includes both Elo and (if available) ML predictions.
 */
export function PredictionCard({ game }: { game: GamePrediction }) {
  const p = game.prediction;
  const fav = p.predicted_spread <= 0 ? game.home_team_id : game.away_team_id;
  const absSpread = Math.abs(p.predicted_spread);
  const played = game.home_score != null && game.away_score != null;

  return (
    <div className="panel p-3 space-y-2 hover:border-team-primary transition-colors">
      <div className="flex items-start justify-between text-sm">
        <div>
          <Link href={`/teams/${game.away_team_id}`} className="font-medium hover:underline">
            {game.away_team_id}
          </Link>
          <span className="text-muted mx-1.5">@</span>
          <Link href={`/teams/${game.home_team_id}`} className="font-medium hover:underline">
            {game.home_team_id}
          </Link>
        </div>
        <div className="text-xs text-muted">{game.gameday || `Wk ${game.week}`}</div>
      </div>

      {played ? (
        <div className="text-xs">
          <span className="text-muted">Final: </span>
          <span className="tabular-nums font-medium">
            {game.away_score} – {game.home_score}
          </span>
        </div>
      ) : (
        <>
          <WinProbBar
            awayTeam={game.away_team_id}
            awayProb={p.away_win_prob}
            homeTeam={game.home_team_id}
            homeProb={p.home_win_prob}
          />
          <div className="flex items-center justify-between text-[11px] text-muted pt-1">
            <span>Pick: <span className="text-text font-medium">{fav} -{absSpread.toFixed(1)}</span></span>
            <span>O/U <span className="text-text tabular-nums">{p.predicted_total.toFixed(1)}</span></span>
          </div>
          {game.ml_prediction && Math.abs(game.ml_prediction.predicted_spread - p.predicted_spread) > 0.5 && (
            <div className="text-[10px] text-muted pt-0.5">
              ML model: {game.ml_prediction.predicted_spread <= 0 ? game.home_team_id : game.away_team_id}{" "}
              -{Math.abs(game.ml_prediction.predicted_spread).toFixed(1)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
