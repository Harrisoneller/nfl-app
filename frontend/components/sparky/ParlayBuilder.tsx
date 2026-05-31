"use client";
import { useMemo, useState } from "react";
import { api, SparkyGame, SparkyParlay, SparkyParlayResponse } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { PredictionCard } from "./PredictionCard";
import { HelpTip, TERMS } from "./HelpTip";
import { americanOdds, pct, pctPoints } from "./format";

const MIN_LEGS = 2;
const MAX_LEGS = 8;
const DEFAULT_LEGS = 3;

/**
 * Parlay Builder (SOW 1 §"Parlay Ranking", N-leg generalization): pick N games
 * (2..8), then Sparky ranks every 2**N winner combination by composite score
 * (confidence × signal alignment × underdog balance × value factor) — not raw
 * odds — and surfaces EV / +EV / Kelly per parlay so value picks are obvious.
 */
export function ParlayBuilder({ games }: { games: SparkyGame[] }) {
  const [targetN, setTargetN] = useState<number>(DEFAULT_LEGS);
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<SparkyParlayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const byEvent = useMemo(() => {
    const m = new Map<string, SparkyGame>();
    games.forEach((g) => m.set(g.event_id, g));
    return m;
  }, [games]);

  const effectiveMax = Math.min(targetN, games.length, MAX_LEGS);
  const ready = selected.length === targetN;

  const changeTargetN = (n: number) => {
    const next = Math.max(MIN_LEGS, Math.min(MAX_LEGS, n));
    setTargetN(next);
    setResult(null);
    setError(null);
    // Trim selection if shrinking below current pick count.
    setSelected((prev) => (prev.length > next ? prev.slice(0, next) : prev));
  };

  const toggle = (eventId: string) => {
    setResult(null);
    setError(null);
    setSelected((prev) => {
      if (prev.includes(eventId)) return prev.filter((e) => e !== eventId);
      if (prev.length >= targetN) return prev; // cap at chosen N
      return [...prev, eventId];
    });
  };

  const run = async () => {
    if (!ready) return;
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

  if (games.length < MIN_LEGS) {
    return (
      <div className="sparky-card p-5 text-sm text-muted">
        Need at least {MIN_LEGS} games on the slate to build a parlay.
      </div>
    );
  }

  const expectedCombos = 2 ** targetN;

  return (
    <div className="space-y-5">
      <div className="sparky-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm">
            <span className="font-semibold text-white">Pick {targetN} games</span>{" "}
            <span className="text-muted">
              ({selected.length}/{targetN} selected) — Sparky will rank all{" "}
              <span className="tabular-nums">{expectedCombos.toLocaleString()}</span> winner combinations.
            </span>
          </div>
          <div className="flex items-center gap-2">
            {selected.length > 0 && (
              <button
                onClick={() => { setSelected([]); setResult(null); }}
                className="sparky-btn !py-1.5 !text-xs"
              >
                Clear
              </button>
            )}
            <button
              onClick={run}
              disabled={!ready || loading}
              className="sparky-btn sparky-btn--solid !py-1.5"
            >
              {loading ? "Ranking…" : `Rank ${targetN}-leg parlay`}
            </button>
          </div>
        </div>

        {/* Leg-count selector */}
        <div className="flex items-center gap-2 flex-wrap pt-1 border-t divider">
          <span className="text-[11px] uppercase tracking-wide text-muted">Legs:</span>
          {Array.from({ length: MAX_LEGS - MIN_LEGS + 1 }).map((_, i) => {
            const n = MIN_LEGS + i;
            const disabled = n > games.length;
            const active = n === targetN;
            return (
              <button
                key={n}
                onClick={() => changeTargetN(n)}
                disabled={disabled}
                className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-colors ${
                  active
                    ? "bg-emerald-500/20 border-emerald-400/60 text-emerald-200"
                    : "border-slate-700/60 text-muted hover:text-white hover:border-slate-500"
                } ${disabled ? "opacity-30 cursor-not-allowed" : ""}`}
                title={disabled ? `Slate only has ${games.length} games` : `${n}-leg parlay (${2 ** n} combinations)`}
              >
                {n}
              </button>
            );
          })}
          {effectiveMax < targetN && (
            <span className="text-[11px] text-amber-300">
              Slate only has {games.length} games.
            </span>
          )}
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
  const valueCount = result.parlays.filter((p) => p.is_value).length;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-white">
          Ranked combinations ({result.parlays.length})
          {valueCount > 0 && (
            <span className="ml-2 sparky-pill sparky-pill--bullish !text-[10px]">
              {valueCount} +EV
            </span>
          )}
        </h3>
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
  const ev = parlay.expected_value ?? 0;
  const isValue = parlay.is_value ?? ev > 0;
  void byEvent; // reserved for future per-leg game lookups
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
          {isValue && (
            <span className="sparky-pill sparky-pill--bullish !text-[10px]" title="Positive expected value">
              +EV
            </span>
          )}
          <div className="flex items-center gap-3 flex-wrap">
            {parlay.legs.map((leg) => (
              <span key={leg.event_id} className="flex items-center gap-1.5 text-sm" title={
                leg.expected_value !== undefined
                  ? `${leg.expected_value > 0 ? "+" : ""}${(leg.expected_value * 100).toFixed(1)}% EV per unit at ${americanOdds(leg.price_american)}`
                  : undefined
              }>
                {leg.team_id ? <TeamLogo teamId={leg.team_id} size={22} /> : null}
                <span className={leg.is_underdog ? "text-amber-300" : "text-white"}>
                  {leg.team_id}
                </span>
                <span className="text-[10px] text-muted tabular-nums">
                  {americanOdds(leg.price_american)}
                </span>
                {leg.is_value && (
                  <span className="text-[9px] text-emerald-400 font-semibold">+EV</span>
                )}
              </span>
            ))}
          </div>
        </div>
        <div className="text-right">
          <div className="text-base font-bold text-white tabular-nums">
            {americanOdds(parlay.parlay_odds_american)}
          </div>
          <div className="text-[10px] text-muted flex items-center justify-end">
            composite{" "}
            <span className="text-emerald-300 font-semibold ml-1">{parlay.composite_score.toFixed(0)}</span>
            <HelpTip label={TERMS.composite.label} body={TERMS.composite.body} />
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-2 text-center">
        <Metric
          label="Sparky says"
          caption="Model hit"
          tip={TERMS.combined_win_prob}
          value={pct(parlay.combined_win_prob, 1)}
        />
        <Metric
          label="Price says"
          caption="Implied"
          tip={TERMS.implied_prob}
          value={pct(parlay.implied_prob, 1)}
        />
        <Metric
          label="Sparky vs price"
          caption="Edge"
          tip={TERMS.edge}
          value={pctPoints((parlay.edge ?? 0) * 100, 1)}
          good={(parlay.edge ?? 0) > 0}
        />
        <Metric
          label="Profit / $1"
          caption="EV per unit"
          tip={TERMS.expected_value}
          value={pctPoints(ev * 100, 1)}
          good={ev > 0}
        />
        <Metric
          label="Suggested stake"
          caption="Kelly fraction"
          tip={TERMS.kelly}
          value={
            parlay.kelly_fraction !== undefined && parlay.kelly_fraction > 0
              ? pctPoints(parlay.kelly_fraction * 100, 1)
              : "—"
          }
        />
      </div>

      <p className="mt-3 text-xs text-slate-300/80 leading-relaxed">{parlay.explanation}</p>
    </div>
  );
}

function Metric({
  label,
  caption,
  value,
  good,
  tip,
}: {
  label: string;
  caption?: string;
  value: string;
  good?: boolean;
  tip?: { label: string; body: string };
}) {
  return (
    <div>
      <div className={`text-sm font-semibold tabular-nums ${good ? "text-emerald-300" : "text-white"}`}>
        {value}
      </div>
      <div className="text-[10px] text-muted uppercase tracking-wide inline-flex items-center justify-center">
        {label}
        {tip && <HelpTip label={tip.label} body={tip.body} />}
      </div>
      {caption && <div className="sparky-metric-caption">({caption})</div>}
    </div>
  );
}
