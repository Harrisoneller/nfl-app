"use client";
import { useMemo, useState } from "react";
import { api, SparkyGame, SparkyParlay, SparkyParlayResponse } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { PredictionCard } from "./PredictionCard";
import { americanOdds, pct, pctPoints } from "./format";

/**
 * Parlay Builder (SOW 2): pick exactly three games, then Sparky ranks all eight
 * winner combinations by composite score (not raw odds) and explains each.
 */
export function ParlayBuilder({ games }: { games: SparkyGame[] }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<SparkyParlayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const byEvent = useMemo(() => {
    const m = new Map<string, SparkyGame>();
    games.forEach((g) => m.set(g.event_id, g));
    return m;
  }, [games]);

  const toggle = (eventId: string) => {
    setResult(null);
    setError(null);
    setSelected((prev) => {
      if (prev.includes(eventId)) return prev.filter((e) => e !== eventId);
      if (prev.length >= 3) return prev; // cap at three
      return [...prev, eventId];
    });
  };

  const run = async () => {
    if (selected.length !== 3) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.sparkyParlay(selected);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to rank parlay");
    } finally {
      setLoading(false);
    }
  };

  if (games.length < 3) {
    return (
      <div className="sparky-card p-5 text-sm text-muted">
        Need at least three games on the slate to build a parlay.
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="sparky-card p-4 flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm">
          <span className="font-semibold text-white">Pick 3 games</span>{" "}
          <span className="text-muted">
            ({selected.length}/3 selected) — then rank all 8 winner combinations.
          </span>
        </div>
        <div className="flex items-center gap-2">
          {selected.length > 0 && (
            <button onClick={() => { setSelected([]); setResult(null); }} className="sparky-btn !py-1.5 !text-xs">
              Clear
            </button>
          )}
          <button
            onClick={run}
            disabled={selected.length !== 3 || loading}
            className="sparky-btn sparky-btn--solid !py-1.5"
          >
            {loading ? "Ranking…" : "Rank parlay"}
          </button>
        </div>
      </div>

      {error && <div className="sparky-card p-4 text-sm text-red-400">{error}</div>}

      {result ? (
        <ParlayResults result={result} byEvent={byEvent} onReset={() => setResult(null)} />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {games.map((g) => (
            <PredictionCard
              key={g.event_id}
              game={g}
              selectable
              selected={selected.includes(g.event_id)}
              onToggle={toggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ParlayResults({
  result,
  byEvent,
  onReset,
}: {
  result: SparkyParlayResponse;
  byEvent: Map<string, SparkyGame>;
  onReset: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Ranked combinations (8)</h3>
        <button onClick={onReset} className="sparky-btn !py-1 !px-3 !text-xs">
          ← Change picks
        </button>
      </div>
      {result.parlays.map((p) => (
        <ParlayRow key={p.rank} parlay={p} byEvent={byEvent} />
      ))}
    </div>
  );
}

function ParlayRow({
  parlay,
  byEvent,
}: {
  parlay: SparkyParlay;
  byEvent: Map<string, SparkyGame>;
}) {
  const top = parlay.rank === 1;
  return (
    <div className={`sparky-card p-4 ${top ? "sparky-card--rank1" : ""}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span
            className={`text-xs font-bold w-7 h-7 rounded-full grid place-items-center ${
              top ? "bg-emerald-400 text-emerald-950" : "bg-slate-700 text-slate-200"
            }`}
          >
            #{parlay.rank}
          </span>
          <div className="flex items-center gap-3 flex-wrap">
            {parlay.legs.map((leg) => (
              <span key={leg.event_id} className="flex items-center gap-1.5 text-sm">
                {leg.team_id ? <TeamLogo teamId={leg.team_id} size={22} /> : null}
                <span className={leg.is_underdog ? "text-amber-300" : "text-white"}>
                  {leg.team_id}
                </span>
                <span className="text-[10px] text-muted tabular-nums">
                  {americanOdds(leg.price_american)}
                </span>
              </span>
            ))}
          </div>
        </div>
        <div className="text-right">
          <div className="text-base font-bold text-white tabular-nums">
            {americanOdds(parlay.parlay_odds_american)}
          </div>
          <div className="text-[10px] text-muted">
            composite <span className="text-emerald-300 font-semibold">{parlay.composite_score.toFixed(0)}</span>
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
        <Metric label="Model hit" value={pct(parlay.combined_win_prob, 1)} />
        <Metric label="Implied" value={pct(parlay.implied_prob, 1)} />
        <Metric
          label="Edge"
          value={pctPoints((parlay.edge ?? 0) * 100, 1)}
          good={(parlay.edge ?? 0) > 0}
        />
        <Metric label="Underdogs" value={`${parlay.underdog_count}`} />
      </div>

      <p className="mt-3 text-xs text-slate-300/80 leading-relaxed">{parlay.explanation}</p>
    </div>
  );
}

function Metric({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div>
      <div className={`text-sm font-semibold tabular-nums ${good ? "text-emerald-300" : "text-white"}`}>
        {value}
      </div>
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
    </div>
  );
}
