"use client";
import { SparkyLegMenuGame } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { HelpTip } from "./HelpTip";
import { kickoff } from "./format";
import { LegSelectCard, cardFromMenuLeg } from "./LegSelectCard";

/**
 * Leg picker for the parlay builder.
 *
 * The builder always searched moneyline, spread and total — `build_leg_pool`
 * has enumerated all three since the parlay rebuild. What it did not do was let
 * you *steer*: you picked games, the engine picked the market and side inside
 * each one, and the game cards showed only a moneyline price, so there was no
 * visible sign the other markets were in play at all.
 *
 * This is the missing half. Each game expands to all six sides with the numbers
 * that decide between them — price, best book, the fair (de-vigged) number, the
 * edge over it, and EV. Leaving a game on "best available" hands that choice
 * back to the engine, which is the right default for anyone who does not have a
 * specific opinion.
 *
 * One leg per game, enforced here and again server-side. Two legs from the same
 * game are linked through the outcome distribution, not through estimation
 * error, and the correlation model prices the latter — it would understate the
 * dependence by an order of magnitude and manufacture edge out of nothing.
 */

const MARKET_LABEL: Record<string, string> = {
  moneyline: "Moneyline",
  spread: "Spread",
  total: "Total",
};

const MARKET_ORDER = ["spread", "total", "moneyline"];

export function LegPicker({
  games,
  chosen,
  onChoose,
}: {
  games: SparkyLegMenuGame[];
  /** event_id -> leg key, or "auto" to let the engine choose. */
  chosen: Record<string, string>;
  onChoose: (eventId: string, legKey: string) => void;
}) {
  return (
    <div className="space-y-3">
      {games.map((g) => (
        <GameLegs
          key={g.event_id}
          game={g}
          chosen={chosen[g.event_id] ?? "auto"}
          onChoose={(key) => onChoose(g.event_id, key)}
        />
      ))}
    </div>
  );
}

function GameLegs({
  game,
  chosen,
  onChoose,
}: {
  game: SparkyLegMenuGame;
  chosen: string;
  onChoose: (legKey: string) => void;
}) {
  const grouped = MARKET_ORDER.map((m) => ({
    market: m,
    legs: game.legs.filter((l) => (l.market ?? "moneyline") === m),
  })).filter((g) => g.legs.length > 0);

  return (
    <div className="sparky-card p-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          {game.away_team_id && <TeamLogo teamId={game.away_team_id} size={22} />}
          <span className="text-sm font-semibold text-white">
            {game.away_team_id ?? game.away_team} @ {game.home_team_id ?? game.home_team}
          </span>
          {game.home_team_id && <TeamLogo teamId={game.home_team_id} size={22} />}
        </div>
        <span className="text-[11px] text-muted">{kickoff(game.commence_time)}</span>
      </div>

      {/* A game that could not be priced says why. */}
      {game.reason && (
        <p className="mt-2 text-xs text-amber-300/90">{game.reason}</p>
      )}

      {game.legs.length > 0 && (
        <>
          <button
            onClick={() => onChoose("auto")}
            className={`mt-3 w-full text-left px-3 py-2 rounded-lg border text-xs transition-colors ${
              chosen === "auto"
                ? "bg-emerald-500/15 border-emerald-400/60 text-emerald-200"
                : "border-slate-700/60 text-muted hover:text-white hover:border-slate-500"
            }`}
          >
            <span className="font-semibold">Best available</span>
            <span className="ml-2 text-[11px] opacity-80">
              let Sparky pick the market and side in this game
            </span>
          </button>

          <div className="mt-2 space-y-2.5">
            {grouped.map(({ market, legs }) => (
              <div key={market}>
                <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1">
                  {MARKET_LABEL[market] ?? market}
                </div>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
                  {legs.map((leg) => (
                    <LegSelectCard
                      key={leg.key}
                      data={cardFromMenuLeg(leg, game)}
                      selected={chosen === leg.key}
                      onToggle={() => onChoose(leg.key!)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** Header explaining what picking legs by hand changes about the pricing. */
export function LegPickerNote({ anyManual }: { anyManual: boolean }) {
  if (!anyManual) return null;
  return (
    <div className="sparky-card p-3 text-[11px] text-slate-300/85 leading-relaxed">
      <span className="text-emerald-300 font-semibold">Hand-built ticket.</span> Because
      you chose these legs yourself, no selection penalty is charged.
      <HelpTip
        label="Why no selection penalty"
        body="The winner's-curse penalty corrects for searching: when the engine enumerates a slate and reports the best ticket, the winner is disproportionately the one whose estimation error happened to point up, so its EV is optimistic. You did not search — you named these legs — so there is no order statistic to correct for, and charging it would make your ticket look worse than it is for a reason that does not apply. Per-leg edge shrinkage still applies, because that is a statement about how much of the model's disagreement with the market is real, and it holds whoever picked the leg."
      />
    </div>
  );
}
