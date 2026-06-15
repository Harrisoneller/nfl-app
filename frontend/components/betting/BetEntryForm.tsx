"use client";

import { useMemo, useState } from "react";
import type { BetInput, BetLegInput, BetMarket } from "@/lib/api";
import { Card } from "@/components/Card";

const input =
  "w-full bg-bg border divider rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-team-primary";
const label = "block text-[11px] text-muted mb-1";

type LegDraft = {
  market: BetMarket;
  selection: string;
  line: string;
  odds_american: string;
  home_team_id: string;
  away_team_id: string;
  event_id: string;
  commence_time: string;
};

function emptyLeg(): LegDraft {
  return {
    market: "moneyline",
    selection: "",
    line: "",
    odds_american: "",
    home_team_id: "",
    away_team_id: "",
    event_id: "",
    commence_time: "",
  };
}

function americanToDecimal(a: number): number {
  if (a === 0) return 1;
  return a > 0 ? 1 + a / 100 : 1 + 100 / -a;
}

function labelFor(l: LegDraft): string {
  const sel = l.selection.trim().toUpperCase();
  if (l.market === "moneyline") return `${sel} ML`;
  if (l.market === "spread") {
    const n = parseFloat(l.line);
    const sign = n > 0 ? "+" : "";
    return `${sel} ${sign}${l.line}`;
  }
  return `${sel.charAt(0) + sel.slice(1).toLowerCase()} ${l.line}`;
}

/**
 * Manual bet entry for straights and parlays. Keeps the common path (market,
 * pick, line, odds) front-and-center; team/event fields for auto-grading + CLV
 * are tucked into an "advanced" disclosure so casual logging stays one-line-fast.
 */
export function BetEntryForm({ onSubmit }: { onSubmit: (b: BetInput) => Promise<void> }) {
  const [legs, setLegs] = useState<LegDraft[]>([emptyLeg()]);
  const [stakeUnits, setStakeUnits] = useState("1");
  const [stakeDollars, setStakeDollars] = useState("");
  const [note, setNote] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isParlay = legs.length > 1;

  const combined = useMemo(() => {
    let dec = 1;
    let ok = true;
    for (const l of legs) {
      const a = parseInt(l.odds_american, 10);
      if (Number.isNaN(a) || a === 0) {
        ok = false;
        break;
      }
      dec *= americanToDecimal(a);
    }
    if (!ok) return null;
    const american = dec >= 2 ? Math.round((dec - 1) * 100) : Math.round(-100 / (dec - 1));
    return { dec, american };
  }, [legs]);

  function update(i: number, patch: Partial<LegDraft>) {
    setLegs((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  }

  async function submit() {
    setErr(null);
    const units = parseFloat(stakeUnits);
    if (Number.isNaN(units) || units <= 0) {
      setErr("Enter a stake greater than 0 units.");
      return;
    }
    const outLegs: BetLegInput[] = [];
    for (const l of legs) {
      const a = parseInt(l.odds_american, 10);
      if (Number.isNaN(a) || a === 0) {
        setErr("Every leg needs valid American odds (e.g. -110 or +150).");
        return;
      }
      if (!l.selection.trim()) {
        setErr("Every leg needs a pick.");
        return;
      }
      if ((l.market === "spread" || l.market === "total") && l.line.trim() === "") {
        setErr(`${l.market} legs need a line.`);
        return;
      }
      outLegs.push({
        market: l.market,
        selection: l.market === "total" ? l.selection.trim().toLowerCase() : l.selection.trim().toUpperCase(),
        selection_label: labelFor(l),
        line: l.line.trim() === "" ? null : parseFloat(l.line),
        odds_american: a,
        event_id: l.event_id.trim() || null,
        home_team_id: l.home_team_id.trim().toUpperCase() || null,
        away_team_id: l.away_team_id.trim().toUpperCase() || null,
        commence_time: l.commence_time ? new Date(l.commence_time).toISOString() : null,
      });
    }

    const payload: BetInput = {
      bet_type: isParlay ? "parlay" : "straight",
      stake_units: units,
      stake_dollars: stakeDollars.trim() ? parseFloat(stakeDollars) : null,
      source: "manual",
      note: note.trim(),
      legs: outLegs,
    };

    setBusy(true);
    try {
      await onSubmit(payload);
      setLegs([emptyLeg()]);
      setStakeDollars("");
      setNote("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save bet.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={isParlay ? `Log a parlay (${legs.length} legs)` : "Log a bet"}>
      <div className="space-y-3">
        {legs.map((l, i) => (
          <div key={i} className="border divider rounded-lg p-3 space-y-2.5 bg-bg/40">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-muted">Leg {i + 1}</span>
              {legs.length > 1 && (
                <button
                  onClick={() => setLegs((prev) => prev.filter((_, idx) => idx !== i))}
                  className="text-[11px] text-muted hover:text-red-400"
                >
                  Remove
                </button>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div>
                <span className={label}>Market</span>
                <select
                  value={l.market}
                  onChange={(e) => update(i, { market: e.target.value as BetMarket })}
                  className={input}
                >
                  <option value="moneyline">Moneyline</option>
                  <option value="spread">Spread</option>
                  <option value="total">Total</option>
                </select>
              </div>
              <div>
                <span className={label}>{l.market === "total" ? "Over / Under" : "Team"}</span>
                {l.market === "total" ? (
                  <select
                    value={l.selection || "over"}
                    onChange={(e) => update(i, { selection: e.target.value })}
                    className={input}
                  >
                    <option value="over">Over</option>
                    <option value="under">Under</option>
                  </select>
                ) : (
                  <input
                    value={l.selection}
                    onChange={(e) => update(i, { selection: e.target.value })}
                    placeholder="PHI"
                    className={input}
                  />
                )}
              </div>
              {l.market !== "moneyline" && (
                <div>
                  <span className={label}>Line</span>
                  <input
                    value={l.line}
                    onChange={(e) => update(i, { line: e.target.value })}
                    placeholder={l.market === "spread" ? "-3.5" : "44.5"}
                    inputMode="decimal"
                    className={input}
                  />
                </div>
              )}
              <div>
                <span className={label}>Odds</span>
                <input
                  value={l.odds_american}
                  onChange={(e) => update(i, { odds_american: e.target.value })}
                  placeholder="-110"
                  inputMode="numeric"
                  className={input}
                />
              </div>
            </div>

            {advanced && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 border-t divider">
                <div>
                  <span className={label}>Home id</span>
                  <input value={l.home_team_id} onChange={(e) => update(i, { home_team_id: e.target.value })} placeholder="PHI" className={input} />
                </div>
                <div>
                  <span className={label}>Away id</span>
                  <input value={l.away_team_id} onChange={(e) => update(i, { away_team_id: e.target.value })} placeholder="DAL" className={input} />
                </div>
                <div>
                  <span className={label}>Kickoff</span>
                  <input type="datetime-local" value={l.commence_time} onChange={(e) => update(i, { commence_time: e.target.value })} className={input} />
                </div>
                <div>
                  <span className={label}>Event id</span>
                  <input value={l.event_id} onChange={(e) => update(i, { event_id: e.target.value })} placeholder="(optional)" className={input} />
                </div>
              </div>
            )}
          </div>
        ))}

        <div className="flex items-center justify-between text-xs">
          <button
            onClick={() => setLegs((prev) => [...prev, emptyLeg()])}
            className="text-team-primary hover:underline"
          >
            + Add leg (make it a parlay)
          </button>
          <button onClick={() => setAdvanced((v) => !v)} className="text-muted hover:text-text">
            {advanced ? "Hide" : "Add"} grading details
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-2 border-t divider">
          <div>
            <span className={label}>Stake (units)</span>
            <input value={stakeUnits} onChange={(e) => setStakeUnits(e.target.value)} inputMode="decimal" className={input} />
          </div>
          <div>
            <span className={label}>Stake ($, optional)</span>
            <input value={stakeDollars} onChange={(e) => setStakeDollars(e.target.value)} inputMode="decimal" placeholder="—" className={input} />
          </div>
          <div>
            <span className={label}>Combined odds</span>
            <div className={`${input} text-muted`}>
              {combined ? `${combined.american > 0 ? "+" : ""}${combined.american} (${combined.dec.toFixed(2)}x)` : "—"}
            </div>
          </div>
        </div>

        <div>
          <span className={label}>Note (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why you liked it…" className={input} />
        </div>

        {err && <p className="text-sm text-red-400">{err}</p>}

        <button
          onClick={submit}
          disabled={busy}
          className="w-full bg-team-primary text-white text-sm font-medium rounded px-4 py-2 disabled:opacity-60"
        >
          {busy ? "Saving…" : isParlay ? "Log parlay" : "Log bet"}
        </button>
      </div>
    </Card>
  );
}
