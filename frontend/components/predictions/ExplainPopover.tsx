"use client";
import { useEffect, useRef, useState } from "react";
import { GamePrediction, PredictionInputs } from "@/lib/api";

/**
 * Click-to-explain popover. Wraps any element and shows the math behind a
 * game prediction on click. Keeps the casual UI clean while letting
 * analytics-minded users dig in.
 */
export function ExplainPopover({
  game,
  children,
}: {
  game: GamePrediction;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const inputs = game.prediction.inputs;

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("click", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!inputs) {
    // Older predictions before we added inputs — render passthrough
    return <>{children}</>;
  }

  return (
    <div ref={ref} className="relative inline-block w-full">
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="w-full text-left cursor-help"
        title="Click to explain this prediction"
      >
        {children}
      </button>
      {open && (
        <div className="absolute z-40 left-0 right-0 mt-2 panel p-3 shadow-xl text-xs space-y-2 min-w-[280px]">
          <Section title="Win probability">
            <Math>
              ({game.home_team_id}) {inputs.home_elo} + {inputs.home_field_advantage_elo} HFA
              − ({game.away_team_id}) {inputs.away_elo}
              = {(inputs.home_elo + inputs.home_field_advantage_elo - inputs.away_elo).toFixed(1)} Elo diff
            </Math>
            <Math>
              → 1 / (1 + 10^(−diff/400)) =
              {" "}<b>{(game.prediction.home_win_prob * 100).toFixed(1)}% {game.home_team_id}</b>
            </Math>
          </Section>

          <Section title="Predicted spread">
            <Math>Elo diff ÷ 25 pts/Elo = <b>{game.prediction.predicted_spread > 0 ? "+" : ""}{game.prediction.predicted_spread.toFixed(1)}</b></Math>
          </Section>

          <Section title="Predicted total">
            <Math>
              ({game.home_team_id} off {inputs.home_off_ppg} + {game.away_team_id} def {inputs.away_def_ppg_allowed}) ÷ 2 = {inputs.expected_home_pts.toFixed(1)}
            </Math>
            <Math>
              ({game.away_team_id} off {inputs.away_off_ppg} + {game.home_team_id} def {inputs.home_def_ppg_allowed}) ÷ 2 = {inputs.expected_away_pts.toFixed(1)}
            </Math>
            <Math>
              Total = <b>{game.prediction.predicted_total.toFixed(1)}</b>
            </Math>
          </Section>

          {game.prediction.game_script && (
            <div className="text-[10px] text-muted pt-1 border-t divider">
              Script: <span className="text-text">{game.prediction.game_script}</span>
              {game.ml_prediction && (
                <span className="ml-2">· ML disagrees by {globalThis.Math.abs(game.ml_prediction.predicted_spread - game.prediction.predicted_spread).toFixed(1)}</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted mb-0.5">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function Math({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] font-mono tabular-nums leading-snug">{children}</div>;
}
