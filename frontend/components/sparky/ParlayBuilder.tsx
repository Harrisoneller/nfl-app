"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  SparkyEvSummary,
  SparkyLegMenuGame,
  SparkyGame,
  SparkyLeg,
  SparkyParlay,
  SparkyParlayResponse,
  SparkyValueBoard,
} from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { PredictionCard } from "./PredictionCard";
import { LegPicker, LegPickerNote } from "./LegPicker";
import { LegCart } from "./LegCart";
import { HelpTip, TERMS } from "./HelpTip";
import { americanOdds, pct, pctPoints } from "./format";

const MIN_LEGS = 2;
const MAX_LEGS = 8;
const DEFAULT_LEGS = 3;

/**
 * Parlay Builder.
 *
 * Two modes, because they answer different questions:
 *
 *   - **Board** (default): "what is the best bet on this slate?" Sparky searches
 *     every priceable leg — moneyline, spread and total on every game — and
 *     returns only tickets that are still +EV after its edge is shrunk to what
 *     settled history supports and a selection penalty is applied. On many
 *     slates that is nothing, and the empty state says so rather than
 *     manufacturing a recommendation.
 *   - **Build**: "I want these specific games." Every combination is ranked and
 *     -EV ones are labelled instead of hidden, since the user asked.
 *
 * Both rank by expected bankroll growth rather than EV per dollar. Ranking by
 * EV% is maximized by maximum variance and fills the page with longshots.
 */
type Mode = "cart" | "board" | "build";

const MODE_LABEL: Record<Mode, string> = {
  cart: "Mix & match",
  board: "Best on the board",
  build: "Pick games",
};

export function ParlayBuilder({
  games,
  valueBoard,
  valueBoardLoading,
}: {
  games: SparkyGame[];
  /** Source for the mix-and-match cart. It is the value board on purpose: every
   *  leg's probability there is already calibrated and shrunk, so a leg cannot
   *  show one number on that tab and a different one inside a ticket. */
  valueBoard?: SparkyValueBoard;
  valueBoardLoading?: boolean;
}) {
  const [mode, setMode] = useState<Mode>("cart");
  const [targetN, setTargetN] = useState<number>(DEFAULT_LEGS);
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<SparkyParlayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Two-step build: pick games, then pick the actual leg in each. `chosen` maps
  // event_id -> leg key, with "auto" meaning "let the engine choose the market
  // and side in this game" — the right default for anyone without a specific
  // opinion, and what the builder did implicitly before the picker existed.
  const [menu, setMenu] = useState<SparkyLegMenuGame[] | null>(null);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [menuLoading, setMenuLoading] = useState(false);

  const byEvent = useMemo(() => {
    const m = new Map<string, SparkyGame>();
    games.forEach((g) => m.set(g.event_id, g));
    return m;
  }, [games]);

  const ready = selected.length === targetN;

  const loadBoard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // strict=true is still the gate for what Sparky will *stand behind*; the
      // backend now also returns `best_available` when that gate is empty, so
      // the page can show what the board actually offers instead of going blank.
      setResult(await api.sparkyParlayBoard({ legs: [2, 3, 4], topN: 6, strict: true }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load the parlay board");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mode === "board" && !result && !loading && !error) void loadBoard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const changeTargetN = (n: number) => {
    const next = Math.max(MIN_LEGS, Math.min(MAX_LEGS, n));
    setTargetN(next);
    setResult(null);
    setError(null);
    setMenu(null);
    setChosen({});
    setSelected((prev) => (prev.length > next ? prev.slice(0, next) : prev));
  };

  const toggle = (eventId: string) => {
    setResult(null);
    setError(null);
    setMenu(null);
    setChosen({});
    setSelected((prev) => {
      if (prev.includes(eventId)) return prev.filter((e) => e !== eventId);
      if (prev.length >= targetN) return prev;
      return [...prev, eventId];
    });
  };

  /** Step 1: fetch every priceable side of the chosen games. */
  const loadMenu = async () => {
    if (!ready) return;
    setMenuLoading(true);
    setError(null);
    try {
      const m = await api.sparkyParlayLegs(selected);
      setMenu(m.games);
      setChosen(Object.fromEntries(selected.map((e) => [e, "auto"])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load the leg menu");
    } finally {
      setMenuLoading(false);
    }
  };

  /** Step 2: price it.
   *
   * Two different endpoints on purpose. If every game is left on "best
   * available" the engine still has a choice to make within each one, so this
   * is a search and the selection penalty applies. If every leg is named, no
   * search happened and it does not. A mix goes through the search path, which
   * is the conservative side to err on.
   */
  const run = async () => {
    if (!ready) return;
    const keys = selected.map((e) => chosen[e]).filter((k) => k && k !== "auto");
    const fullyManual = keys.length === selected.length;

    setLoading(true);
    setError(null);
    try {
      setResult(
        fullyManual
          ? await api.sparkyParlayPrice(keys)
          : await api.sparkyParlay(selected),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to price parlay");
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setResult(null);
    setError(null);
    setMenu(null);
    setChosen({});
  };

  if (games.length < MIN_LEGS) {
    return (
      <div className="sparky-card p-5 text-sm text-muted">
        Need at least {MIN_LEGS} games on the slate to build a parlay.
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="sparky-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="inline-flex rounded-lg border border-slate-700/60 overflow-hidden">
            {(["cart", "board", "build"] as const).map((m) => (
              <button
                key={m}
                onClick={() => switchMode(m)}
                className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                  mode === m
                    ? "bg-emerald-500/20 text-emerald-200"
                    : "text-muted hover:text-white"
                }`}
              >
                {MODE_LABEL[m]}
              </button>
            ))}
          </div>

          {mode === "cart" ? null : mode === "board" ? (
            <button
              onClick={loadBoard}
              disabled={loading}
              className="sparky-btn sparky-btn--solid !py-1.5"
            >
              {loading ? "Searching…" : "Re-run search"}
            </button>
          ) : (
            <div className="flex items-center gap-2">
              {selected.length > 0 && (
                <button
                  onClick={() => {
                    setSelected([]);
                    setResult(null);
                  }}
                  className="sparky-btn !py-1.5 !text-xs"
                >
                  Clear
                </button>
              )}
              {menu ? (
                <button
                  onClick={run}
                  disabled={!ready || loading}
                  className="sparky-btn sparky-btn--solid !py-1.5"
                >
                  {loading ? "Pricing…" : "Price this ticket"}
                </button>
              ) : (
                <button
                  onClick={loadMenu}
                  disabled={!ready || menuLoading}
                  className="sparky-btn sparky-btn--solid !py-1.5"
                >
                  {menuLoading ? "Loading legs…" : `Pick legs (${targetN} games)`}
                </button>
              )}
            </div>
          )}
        </div>

        {mode === "cart" ? (
          <p className="text-xs text-muted leading-relaxed">
            Build a ticket out of anything on the board — a spread in one game, a
            moneyline in another, a total in a third, alternate numbers included. Legs
            from the <em>same</em> game are welcome too: those are priced on that
            game&apos;s joint outcome distribution rather than by multiplying their
            prices, which is the difference between valuing a favourite-cover-plus-over
            correctly and being several points of probability wrong about it.
          </p>
        ) : mode === "board" ? (
          <p className="text-xs text-muted leading-relaxed">
            Sparky searches every moneyline, spread and total on the slate. Tickets that
            stay positive after its edge is cut to what settled results support are
            recommended; when none do — the usual outcome — you still get the best tickets
            the board offers, priced honestly, so you can see exactly what you would be
            giving up. Nothing is hidden and nothing is manufactured.
          </p>
        ) : (
          <>
            <div className="text-sm">
              <span className="font-semibold text-white">Pick {targetN} games</span>{" "}
              <span className="text-muted">
                ({selected.length}/{targetN} selected) — Sparky ranks every market and side
                in those games, not just who wins, and prices the result whether or not it
                is +EV. Picking games you like is allowed; being told what they are worth
                is the point.
              </span>
            </div>
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
                    title={disabled ? `Slate only has ${games.length} games` : `${n}-leg parlay`}
                  >
                    {n}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      {error && <div className="sparky-card p-4 text-sm text-red-400">{error}</div>}

      {mode === "cart" ? (
        <LegCart board={valueBoard} isLoading={valueBoardLoading} />
      ) : result ? (
        <ParlayResults
          result={result}
          byEvent={byEvent}
          onReset={mode === "build" ? () => setResult(null) : undefined}
        />
      ) : mode === "build" && menu ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 className="text-sm font-semibold text-white">Choose your legs</h3>
              <p className="text-[11px] text-muted mt-0.5">
                Every side of every market in the games you picked. Leave a game on
                &ldquo;best available&rdquo; to let Sparky choose it.
              </p>
            </div>
            <button
              onClick={() => {
                setMenu(null);
                setChosen({});
              }}
              className="sparky-btn !py-1 !px-3 !text-xs"
            >
              ← Change games
            </button>
          </div>

          <LegPickerNote
            anyManual={selected.every((e) => chosen[e] && chosen[e] !== "auto")}
          />

          <LegPicker
            games={menu}
            chosen={chosen}
            onChoose={(eventId, legKey) =>
              setChosen((prev) => ({ ...prev, [eventId]: legKey }))
            }
          />
        </div>
      ) : mode === "build" ? (
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
      ) : null}
    </div>
  );
}

/** How the engine was configured, so a user can see why a slate came back empty. */
function TrustBar({ meta }: { meta?: SparkyParlayResponse["meta"] }) {
  const trust = meta?.trust;
  if (!trust) return null;
  const lam = trust.edge_shrink?.lambda;
  const fitted = trust.edge_shrink?.fitted;
  const n = trust.edge_shrink?.n_rows ?? 0;
  return (
    <div className="sparky-card p-3 text-[11px] text-muted flex flex-wrap items-center gap-x-4 gap-y-1">
      <span>
        Edge trust{" "}
        <span className="text-white font-semibold tabular-nums">
          {lam !== undefined ? `${(lam * 100).toFixed(0)}%` : "—"}
        </span>{" "}
        {fitted ? (
          <span className="text-emerald-300">fitted from {n} settled picks</span>
        ) : (
          <span className="text-amber-300">
            prior only ({n} settled — not enough to fit yet)
          </span>
        )}
      </span>
      {meta?.pool_size !== undefined && (
        <span>
          Searched{" "}
          <span className="text-white font-semibold tabular-nums">{meta.pool_size}</span>{" "}
          qualifying legs
          {meta.pool_by_market && (
            <span>
              {" "}
              ({Object.entries(meta.pool_by_market)
                .map(([k, v]) => `${v} ${k}`)
                .join(", ")})
            </span>
          )}
        </span>
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
  onReset?: () => void;
}) {
  const valueCount = result.parlays.filter((p) => p.is_value).length;
  const fallback = result.best_available ?? [];
  const unavailable = result.unavailable_events ?? [];
  const shown = result.parlays.length > 0 ? result.parlays : fallback;
  const showingFallback = result.parlays.length === 0 && fallback.length > 0;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-white">
          {result.parlays.length > 0
            ? `Recommended tickets (${result.parlays.length})`
            : showingFallback
              ? `Best available (${fallback.length})`
              : "No tickets"}
          {valueCount > 0 && (
            <span className="ml-2 sparky-pill sparky-pill--bullish !text-[10px]">
              {valueCount} +EV
            </span>
          )}
        </h3>
        {onReset && (
          <button onClick={onReset} className="sparky-btn !py-1 !px-3 !text-xs">
            ← Change picks
          </button>
        )}
      </div>

      <EvSummaryBar summary={result.ev_summary} showingFallback={showingFallback} />

      <TrustBar meta={result.meta} />

      {/* A picked game that could not be priced. A parlay needs one leg per
          game, so this is the whole reason there is no ticket — and it used to
          surface as an HTTP 400 that named neither the game nor the cause. */}
      {unavailable.length > 0 && (
        <div className="sparky-card p-4 border border-amber-500/40 bg-amber-950/15">
          <div className="text-sm font-semibold text-amber-300">
            {unavailable.length} of your picks could not be priced
          </div>
          <p className="text-xs text-slate-300/80 mt-1.5">
            A parlay needs one priceable side from every game, so no ticket can be built
            until these are available.
          </p>
          <div className="mt-2 space-y-1.5">
            {unavailable.map((u) => {
              const g = byEvent.get(u.event_id);
              const name = g ? `${g.away_team_id} @ ${g.home_team_id}` : u.event_id;
              return (
                <div key={u.event_id} className="text-xs">
                  <span className="text-slate-200 font-medium">{name}:</span>{" "}
                  <span className="text-muted">{u.reason}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {showingFallback && (
        <div className="sparky-card p-4 text-xs text-slate-300/85 leading-relaxed border border-amber-500/30">
          <span className="text-amber-300 font-semibold">
            Nothing here is +EV — Sparky is not recommending these.
          </span>{" "}
          {result.message ??
            "No ticket on this slate stays positive once the edge is cut to what history supports."}{" "}
          They are shown because you should be able to see what the board offers and
          exactly how much it costs, rather than an empty page.
        </div>
      )}

      {shown.length === 0 && unavailable.length === 0 && (
        <div className="sparky-card p-5 text-sm text-slate-300/90 leading-relaxed">
          {result.message ?? "No ticket could be built from these games."}
        </div>
      )}

      {shown.map((p) => (
        <ParlayRow key={`${p.rank}-${p.parlay_odds_american}`} parlay={p} byEvent={byEvent} />
      ))}
    </div>
  );
}

/**
 * EV headline for whatever was built.
 *
 * The builder's contract is that it always prices what you asked for, and that
 * obliges it to report EV whether or not the news is good. A tool that shows
 * expected value only when it is positive is not showing expected value.
 */
function EvSummaryBar({
  summary,
  showingFallback,
}: {
  summary?: SparkyEvSummary;
  showingFallback?: boolean;
}) {
  if (!summary || !summary.count) return null;
  const best = summary.best_ev_pct ?? 0;
  const positive = summary.any_positive;

  return (
    <div className="sparky-card p-3 flex flex-wrap items-center gap-x-6 gap-y-2">
      <div>
        <div
          className={`text-xl font-bold tabular-nums ${
            positive ? "text-emerald-300" : "text-amber-300"
          }`}
        >
          {best >= 0 ? "+" : ""}
          {best.toFixed(2)}%
        </div>
        <div className="text-[11px] text-muted flex items-center">
          best ticket EV
          <HelpTip
            label="Expected value"
            body="Expected profit per unit staked on the best ticket here, after correlation and push are priced, the model's edge is shrunk to what settled results support, and a selection penalty is charged for having picked these out of a slate. Negative means the ticket loses money on average — that is not a bug, it is what most parlays are."
          />
        </div>
      </div>

      <div>
        <div className="text-sm text-slate-200 tabular-nums">
          {summary.positive_count ?? 0} of {summary.count}
        </div>
        <div className="text-[11px] text-muted">tickets +EV</div>
      </div>

      {summary.worst_ev_pct !== undefined && summary.count > 1 && (
        <div>
          <div className="text-sm text-slate-200 tabular-nums">
            {summary.worst_ev_pct >= 0 ? "+" : ""}
            {summary.worst_ev_pct.toFixed(2)}%
          </div>
          <div className="text-[11px] text-muted">worst shown</div>
        </div>
      )}

      <p className="text-[11px] text-muted/80 flex-1 min-w-[16rem] leading-relaxed">
        {positive
          ? "Ranked by expected bankroll growth, not by EV percent — ranking on EV alone is maximized by maximum variance and fills the page with longshots."
          : showingFallback
            ? "Every ticket below loses money on average. Ranked least-bad first, because expected growth is identically zero once nothing is positive."
            : "Every ticket below loses money on average at these prices. Ranked least-bad first."}
      </p>
    </div>
  );
}

const MARKET_BADGE: Record<string, string> = {
  moneyline: "ML",
  spread: "ATS",
  total: "O/U",
};

function LegChip({ leg }: { leg: SparkyLeg }) {
  const badge = MARKET_BADGE[leg.market ?? "moneyline"] ?? "ML";
  return (
    <span
      className="flex items-center gap-1.5 text-sm"
      title={
        leg.expected_value !== undefined
          ? `${leg.label} — ${leg.edge !== undefined ? `${(leg.edge * 100).toFixed(1)}pt edge, ` : ""}` +
            `${leg.expected_value > 0 ? "+" : ""}${(leg.expected_value * 100).toFixed(1)}% EV at ${americanOdds(leg.price_american)}` +
            (leg.book ? ` (${leg.book})` : "") +
            (leg.push_prob ? ` · ${(leg.push_prob * 100).toFixed(1)}% push` : "")
          : undefined
      }
    >
      {leg.team_id ? <TeamLogo teamId={leg.team_id} size={22} /> : null}
      <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-slate-700/70 text-slate-300">
        {badge}
      </span>
      <span className="text-white">{leg.label ?? leg.team_id}</span>
      <span className="text-[10px] text-muted tabular-nums">
        {americanOdds(leg.price_american)}
      </span>
      {leg.push_prob !== undefined && leg.push_prob > 0.005 && (
        <span
          className="text-[9px] text-sky-300"
          title={`${(leg.push_prob * 100).toFixed(1)}% chance this leg pushes and voids out of the ticket`}
        >
          push {(leg.push_prob * 100).toFixed(0)}%
        </span>
      )}
    </span>
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
  const evAdj = parlay.ev_adjusted ?? parlay.expected_value ?? 0;
  const isValue = parlay.is_value ?? evAdj > 0;
  const pricing = parlay.pricing;
  void byEvent;

  return (
    <div className={`sparky-card p-4 ${top ? "sparky-card--rank1" : ""}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`text-xs font-bold w-7 h-7 rounded-full grid place-items-center ${
              top ? "bg-emerald-400 text-emerald-950" : "bg-slate-700 text-slate-200"
            }`}
          >
            #{parlay.rank}
          </span>
          {isValue ? (
            <span className="sparky-pill sparky-pill--bullish !text-[10px]" title="Positive expected value after all deductions">
              +EV
            </span>
          ) : (
            <span className="sparky-pill !text-[10px] !text-amber-300" title="Negative expected value after all deductions">
              -EV
            </span>
          )}
          <div className="flex items-center gap-3 flex-wrap">
            {parlay.legs.map((leg) => (
              <LegChip key={leg.key ?? `${leg.event_id}-${leg.market}-${leg.side}`} leg={leg} />
            ))}
          </div>
        </div>
        <div className="text-right">
          <div className="text-base font-bold text-white tabular-nums">
            {americanOdds(parlay.parlay_odds_american)}
          </div>
          <div className="text-[10px] text-muted">
            {parlay.n_legs} leg{parlay.n_legs === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-2 text-center">
        <Metric
          label="Sparky says"
          caption="Chance it cashes"
          tip={TERMS.combined_win_prob}
          value={pct(parlay.hit_prob ?? parlay.combined_win_prob, 1)}
        />
        <Metric
          label="Price says"
          caption="Implied"
          tip={TERMS.implied_prob}
          value={pct(parlay.implied_prob, 1)}
        />
        <Metric
          label="Raw edge"
          caption="Before deductions"
          tip={TERMS.expected_value}
          value={pctPoints((parlay.expected_value ?? 0) * 100, 1)}
          good={(parlay.expected_value ?? 0) > 0}
        />
        <Metric
          label="After deductions"
          caption="EV per $1"
          tip={{
            label: "Adjusted expected value",
            body:
              "Raw edge, less the penalty for having selected these legs out of the whole slate " +
              "rather than being handed them. Picking the best-looking legs from a big pool " +
              "selects partly for real edge and partly for estimation noise; this charges for the noise.",
          }}
          value={pctPoints(evAdj * 100, 1)}
          good={evAdj > 0}
        />
        <Metric
          label="Suggested stake"
          caption="Fractional Kelly"
          tip={TERMS.kelly}
          value={
            parlay.kelly_fraction !== undefined && parlay.kelly_fraction > 0
              ? `${(parlay.kelly_fraction * 100).toFixed(2)}%`
              : "—"
          }
        />
      </div>

      {pricing && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted border-t divider pt-2">
          {Math.abs(pricing.correlation_effect ?? 0) >= 0.002 && (
            <span title="How much the legs moving together is worth, versus the book's assumption that they are independent.">
              Correlation{" "}
              <span
                className={
                  (pricing.correlation_effect ?? 0) > 0 ? "text-emerald-300" : "text-amber-300"
                }
              >
                {pctPoints((pricing.correlation_effect ?? 0) * 100, 1)}
              </span>
            </span>
          )}
          {(pricing.push_effect ?? 0) >= 0.002 && (
            <span title="Value of the pushes: a leg landing exactly on its number voids and the ticket re-prices at one fewer leg instead of losing.">
              Push protection{" "}
              <span className="text-emerald-300">
                {pctPoints((pricing.push_effect ?? 0) * 100, 1)}
              </span>
            </span>
          )}
          {(parlay.selection_penalty ?? 0) >= 0.001 && (
            <span title="Charged because these legs were chosen as the best-looking out of the slate.">
              Selection penalty{" "}
              <span className="text-amber-300">
                −{((parlay.selection_penalty ?? 0) * 100).toFixed(1)}%
              </span>
            </span>
          )}
        </div>
      )}

      {parlay.warnings && parlay.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {parlay.warnings.map((w, i) => (
            <li key={i} className="text-[11px] text-amber-300/90">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}

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
