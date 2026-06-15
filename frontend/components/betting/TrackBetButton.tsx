"use client";

import { useState } from "react";
import Link from "next/link";
import { api, type BetLegInput } from "@/lib/api";
import { useAuth } from "@/context/AuthProvider";

/**
 * One-tap bet logging. Drop it next to any priced selection (odds board, Sparky
 * card) with a prefilled leg; it creates a 1-unit straight bet the user can edit
 * later on their profile. Falls back to a sign-in link when logged out, since a
 * bet log is inherently per-account.
 */
export function TrackBetButton({
  leg,
  source = "odds",
  stakeUnits = 1,
  compact = false,
}: {
  leg: BetLegInput;
  source?: "odds" | "sparky";
  stakeUnits?: number;
  compact?: boolean;
}) {
  const { isAuthenticated } = useAuth();
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">("idle");

  const base = compact
    ? "text-[10px] px-1.5 py-0.5"
    : "text-[11px] px-2 py-1";
  const cls = `rounded-full border divider transition-colors ${base}`;

  if (!isAuthenticated) {
    return (
      <Link href="/login" className={`${cls} text-muted hover:text-text hover:border-team-primary`}>
        Track
      </Link>
    );
  }

  if (state === "done") {
    return <span className={`${cls} text-green-500 border-green-500/40`}>Tracked ✓</span>;
  }

  return (
    <button
      onClick={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        setState("saving");
        try {
          await api.createBet({
            bet_type: "straight",
            stake_units: stakeUnits,
            source,
            legs: [leg],
          });
          setState("done");
          window.setTimeout(() => setState("idle"), 2500);
        } catch {
          setState("error");
          window.setTimeout(() => setState("idle"), 2500);
        }
      }}
      disabled={state === "saving"}
      className={`${cls} ${state === "error" ? "text-red-400 border-red-500/40" : "text-team-primary hover:border-team-primary"} disabled:opacity-50`}
      title="Track this bet on your profile"
    >
      {state === "saving" ? "…" : state === "error" ? "Retry" : "+ Track"}
    </button>
  );
}
