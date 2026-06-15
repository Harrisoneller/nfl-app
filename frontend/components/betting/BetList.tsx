"use client";

import { useState } from "react";
import type { Bet, BetLeg } from "@/lib/api";
import { Card } from "@/components/Card";
import { american, resultColor, signedUnits, STATUS_STYLES } from "./format";

export function BetList({
  bets,
  onDelete,
}: {
  bets: Bet[];
  onDelete: (id: string) => void | Promise<void>;
}) {
  if (bets.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No bets logged yet. Add one above, or tap “Track” on the odds board.
        </p>
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      {bets.map((b) => (
        <BetRow key={b.id} bet={b} onDelete={onDelete} />
      ))}
    </div>
  );
}

function BetRow({ bet, onDelete }: { bet: Bet; onDelete: (id: string) => void | Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const status = bet.status;

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded-full border ${STATUS_STYLES[status] ?? STATUS_STYLES.pending}`}
            >
              {status}
            </span>
            <span className="text-xs text-muted capitalize">
              {bet.bet_type} · {bet.stake_units.toFixed(2)}u
              {bet.stake_dollars ? ` ($${bet.stake_dollars.toFixed(0)})` : ""} · {american(bet.odds_american)}
            </span>
          </div>

          <div className="mt-2 space-y-1">
            {bet.legs.map((l) => (
              <LegLine key={l.id} leg={l} />
            ))}
          </div>

          {bet.note && <p className="text-xs text-muted mt-1.5 italic">“{bet.note}”</p>}
        </div>

        <div className="text-right shrink-0">
          <div className={`text-lg font-semibold tabular-nums ${resultColor(bet.result_units)}`}>
            {status === "pending" ? "—" : signedUnits(bet.result_units)}
          </div>
          {bet.clv_pct != null && (
            <div className={`text-[11px] tabular-nums ${resultColor(bet.clv_pct)}`}>
              {bet.clv_pct > 0 ? "+" : ""}
              {bet.clv_pct.toFixed(1)}% CLV
            </div>
          )}
          <button
            onClick={async () => {
              setBusy(true);
              try {
                await onDelete(bet.id);
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            className="mt-1 text-[11px] text-muted hover:text-red-400 underline disabled:opacity-50"
          >
            Delete
          </button>
        </div>
      </div>
    </Card>
  );
}

function LegLine({ leg }: { leg: BetLeg }) {
  const resultDot =
    leg.leg_result === "won"
      ? "bg-green-500"
      : leg.leg_result === "lost"
        ? "bg-red-400"
        : leg.leg_result === "push"
          ? "bg-amber-500"
          : "bg-muted/40";

  // Closing value hint: price-based for ML, points for spread/total.
  const clvHint =
    leg.clv_pct != null
      ? `${leg.clv_pct > 0 ? "+" : ""}${leg.clv_pct.toFixed(1)}% vs close`
      : leg.clv_line != null
        ? `${leg.clv_line > 0 ? "+" : ""}${leg.clv_line.toFixed(1)} pts vs close`
        : null;

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${resultDot}`} aria-hidden />
      <span className="font-medium">{leg.selection_label || leg.selection}</span>
      <span className="text-xs text-muted">{american(leg.odds_american)}</span>
      {clvHint && (
        <span
          className={`text-[10px] ml-auto ${leg.beat_close ? "text-green-500" : "text-muted"}`}
          title="Closing line value for this leg"
        >
          {clvHint}
        </span>
      )}
    </div>
  );
}
