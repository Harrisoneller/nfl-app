"use client";
import Link from "next/link";
import { SparkyGame } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { ConfidenceRing } from "./ConfidenceRing";
import { SignalPills } from "./SignalPill";
import { HelpTip, TERMS } from "./HelpTip";
import {
  americanOdds,
  classificationDescription,
  classificationLabel,
  kickoff,
  pct,
} from "./format";

/**
 * The marquee prediction card (SOW 2 "Prediction Card Elements"): teams + odds,
 * predicted winner + confidence, signal pills, plain-English read, and an
 * Add-to-Parlay action.
 */
export function PredictionCard({
  game,
  selected = false,
  selectable = false,
  onToggle,
}: {
  game: SparkyGame;
  selected?: boolean;
  selectable?: boolean;
  onToggle?: (eventId: string) => void;
}) {
  const winner = game.predicted_winner;
  const homePicked = winner === game.home_team_id;
  const cls = `sparky-card p-4 ${selected ? "sparky-selected" : ""}`;

  return (
    <div className={cls}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <span
              className={`sparky-chip sparky-chip--${game.classification ?? "lean"}`}
              title={classificationDescription(game.classification)}
            >
              {classificationLabel(game.classification)}
            </span>
            {game.classification && (
              <HelpTip
                label={`${classificationLabel(game.classification)} — Pick Tier`}
                body={classificationDescription(game.classification) || TERMS.classification.body}
              />
            )}
            <span>{kickoff(game.commence_time)}</span>
          </div>

          {/* Teams */}
          <div className="mt-2 space-y-1.5">
            <TeamRow
              teamId={game.away_team_id}
              fallback={game.away_team}
              ml={game.market?.away_ml ?? null}
              picked={!homePicked && !!winner}
            />
            <TeamRow
              teamId={game.home_team_id}
              fallback={game.home_team}
              ml={game.market?.home_ml ?? null}
              picked={homePicked && !!winner}
            />
          </div>
        </div>

        <div className="flex flex-col items-center shrink-0">
          <ConfidenceRing score={game.confidence_score} />
          <div className="text-[10px] text-muted mt-1 flex items-center">
            confidence
            <HelpTip label={TERMS.confidence.label} body={TERMS.confidence.body} />
          </div>
        </div>
      </div>

      {/* Pick summary */}
      <div className="mt-3 flex items-center justify-between text-sm">
        <div className="text-emerald-300 font-semibold">
          {winner ?? "—"} <span className="text-muted font-normal">to win</span>{" "}
          <span className="tabular-nums">{pct(game.win_prob)}</span>
          <HelpTip label={TERMS.win_prob.label} body={TERMS.win_prob.body} />
        </div>
        <div className="text-[11px] text-muted tabular-nums flex items-center gap-1">
          {game.model_prob != null && (
            <>
              model {pct(game.model_prob)}
              <HelpTip label={TERMS.model_prob.label} body={TERMS.model_prob.body} />
              ·
            </>
          )}
          {game.market_prob != null && (
            <>
              mkt {pct(game.market_prob)}
              <HelpTip label={TERMS.market_prob.label} body={TERMS.market_prob.body} />
            </>
          )}
        </div>
      </div>

      {/* Signals */}
      <div className="mt-3">
        <SignalPills signals={game.signals} limit={5} />
      </div>

      {/* Explanation */}
      {game.explanation && (
        <p className="mt-3 text-xs text-slate-300/80 leading-relaxed">{game.explanation}</p>
      )}

      {/* Actions */}
      <div className="mt-3 flex items-center justify-between">
        <Link
          href={`/sparky/${encodeURIComponent(game.event_id)}`}
          className="text-[11px] text-cyan-300 hover:underline"
        >
          Game detail →
        </Link>
        {selectable && (
          <button
            onClick={() => onToggle?.(game.event_id)}
            className={`sparky-btn ${selected ? "sparky-btn--solid" : ""} !py-1 !px-3 !text-xs`}
          >
            {selected ? "✓ In parlay" : "+ Add to parlay"}
          </button>
        )}
      </div>
    </div>
  );
}

function TeamRow({
  teamId,
  fallback,
  ml,
  picked,
}: {
  teamId: string | null;
  fallback: string | null;
  ml: number | null;
  picked: boolean;
}) {
  return (
    <div className={`flex items-center justify-between gap-2 ${picked ? "" : "opacity-80"}`}>
      <div className="flex items-center gap-2 min-w-0">
        {teamId ? <TeamLogo teamId={teamId} size={26} /> : <div className="w-[26px]" />}
        <span className={`text-sm truncate ${picked ? "font-semibold text-white" : ""}`}>
          {teamId ?? fallback ?? "—"}
        </span>
        {picked && <span className="text-[10px] text-emerald-400">PICK</span>}
      </div>
      <span className="text-xs text-muted tabular-nums">{americanOdds(ml)}</span>
    </div>
  );
}
