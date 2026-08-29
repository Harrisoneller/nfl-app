"use client";
import { useMemo, useState } from "react";
import {
  api,
  SparkyParlayResponse,
  SparkySameGame,
  SparkyValueBoard,
  SparkyValuePick,
} from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { HelpTip } from "./HelpTip";
import { americanOdds, isNflMatchup, pct, pctPoints } from "./format";
import { LegSelectCard, cardFromValuePick } from "./LegSelectCard";

/**
 * The leg cart — build a ticket out of any legs on the board.
 *
 * The builder used to work game-first: pick N games, and the engine picked one
 * market and one side inside each. That shape encoded two limits that were
 * never true of the pricing engine underneath it, and both cost real edge:
 *
 *   1. **You could not mix freely.** Wanting KC +3.5, BUF's moneyline and the
 *      Duke/Clemson over is an ordinary request; the old flow made you name the
 *      three games first and then steer inside each one, and capped the ticket
 *      at one leg per game.
 *   2. **Two legs from one game were refused outright.** They are now priced on
 *      the joint (margin, total) distribution, which is the only honest way to
 *      value them.
 *
 * This component sources its rows from the value board, deliberately. That
 * board is where every leg's probability is already calibrated, shrunk toward
 * the market by the fitted trust parameter, and labelled with the reason it did
 * or did not qualify. Sourcing the cart from anywhere else would give the same
 * leg two different numbers on two tabs, and the one in the parlay builder would
 * be the one nobody was auditing.
 *
 * Legs that the board *rejected* are still addable, and that is on purpose: the
 * user asked for this ticket. What we owe them is the honest number, not a
 * refusal to compute it. The tier chip travels with the row so a "no bet" leg
 * still looks like one inside the cart.
 */

const MAX_LEGS = 8;

type MarketFilter = "all" | "moneyline" | "spread" | "total";

const MARKETS: { id: MarketFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "moneyline", label: "Moneyline" },
  { id: "spread", label: "Spreads" },
  { id: "total", label: "Totals" },
];

function matchupLabel(p: SparkyValuePick): string {
  const m = p.matchup;
  if (m?.home_team_id && m?.away_team_id) return `${m.away_team_id} @ ${m.home_team_id}`;
  return p.event_id;
}

export function LegCart({
  board,
  isLoading,
}: {
  board?: SparkyValueBoard;
  isLoading?: boolean;
}) {
  const [market, setMarket] = useState<MarketFilter>("all");
  const [query, setQuery] = useState("");
  const [altOnly, setAltOnly] = useState(false);
  const [positiveOnly, setPositiveOnly] = useState(false);
  const [cart, setCart] = useState<SparkyValuePick[]>([]);
  const [result, setResult] = useState<SparkyParlayResponse | null>(null);
  const [pricing, setPricing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Every priceable leg on the slate, from all three board buckets.
   *  `picks` alone would hide exactly the legs a builder most often wants — the
   *  other side of a game, or a leg the board declined to recommend. */
  const allLegs = useMemo(() => {
    const seen = new Map<string, SparkyValuePick>();
    for (const bucket of [
      board?.picks ?? [],
      board?.best_available ?? [],
      board?.priced_out ?? [],
    ]) {
      for (const p of bucket) {
        if (!seen.has(p.key) && isNflMatchup(p.matchup?.home_team_id, p.matchup?.away_team_id)) {
          seen.set(p.key, p);
        }
      }
    }
    return [...seen.values()];
  }, [board?.picks, board?.best_available, board?.priced_out]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return allLegs
      .filter((p) => market === "all" || p.market === market)
      .filter((p) => !altOnly || p.is_alt)
      .filter((p) => !positiveOnly || p.expected_value > 0)
      .filter(
        (p) =>
          !q ||
          p.label.toLowerCase().includes(q) ||
          matchupLabel(p).toLowerCase().includes(q),
      )
      .sort((a, b) => b.expected_value - a.expected_value);
  }, [allLegs, market, altOnly, positiveOnly, query]);

  const inCart = useMemo(() => new Set(cart.map((c) => c.key)), [cart]);

  const byGame = useMemo(() => {
    const m = new Map<string, SparkyValuePick[]>();
    cart.forEach((c) => {
      const list = m.get(c.event_id) ?? [];
      list.push(c);
      m.set(c.event_id, list);
    });
    return m;
  }, [cart]);

  /** Why a leg cannot join the cart. Returning a sentence rather than just
   *  disabling the button matters: "you already have the other side of this
   *  total" is a different problem from "the ticket is full", and a greyed-out
   *  button says neither. */
  const blockedReason = (p: SparkyValuePick): string | null => {
    if (inCart.has(p.key)) return null;
    if (cart.length >= MAX_LEGS) return `A ticket holds at most ${MAX_LEGS} legs.`;
    const sameGame = byGame.get(p.event_id) ?? [];
    const opposite = sameGame.find((c) => c.market === p.market && c.side !== p.side);
    if (opposite) {
      return `You already have ${opposite.label}. Both sides of the same market cannot both win, so that is not a parlay.`;
    }
    const sameSide = sameGame.find(
      (c) => c.market === p.market && c.side === p.side && c.line !== p.line,
    );
    if (sameSide) {
      return `You already have ${sameSide.label} — the same opinion at a different number, not a second leg.`;
    }
    return null;
  };

  const toggle = (p: SparkyValuePick) => {
    setResult(null);
    setError(null);
    setCart((prev) =>
      prev.some((c) => c.key === p.key)
        ? prev.filter((c) => c.key !== p.key)
        : prev.length >= MAX_LEGS
          ? prev
          : [...prev, p],
    );
  };

  const price = async () => {
    if (cart.length < 2) return;
    setPricing(true);
    setError(null);
    try {
      setResult(await api.sparkyParlayPrice(cart.map((c) => c.key)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not price this ticket");
    } finally {
      setPricing(false);
    }
  };

  const sameGameCount = [...byGame.values()].filter((v) => v.length > 1).length;

  if (isLoading) {
    return <div className="sparky-card p-6 text-sm text-muted">Pricing the slate…</div>;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px] items-start">
      {/* ---------------------------------------------------------------- */}
      {/* The board                                                         */}
      {/* ---------------------------------------------------------------- */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {MARKETS.map((m) => (
            <button
              key={m.id}
              onClick={() => setMarket(m.id)}
              className={`sparky-tab ${market === m.id ? "sparky-tab--active" : ""}`}
            >
              {m.label}
            </button>
          ))}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by team or bet…"
            className="flex-1 min-w-[150px] rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-[12px] outline-none focus:border-white/25"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={positiveOnly}
              onChange={(e) => setPositiveOnly(e.target.checked)}
            />
            +EV only
          </label>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={altOnly}
              onChange={(e) => setAltOnly(e.target.checked)}
            />
            Alternate lines only
          </label>
          <HelpTip
            label="Alternate lines"
            body="Books do not all hang the same number. When one has -6.5 and the consensus is -7.5, that half point is worth roughly two points of win probability at the key numbers — 3 and 7 especially — and it costs nothing to take. Every number actually quoted is listed here as its own bet. Where no book quotes both sides at that number, its fair price is read off the market's own implied distribution rather than de-vigged from a single book's two-way price, which would just be that book's hold."
          />
          <span className="ml-auto tabular-nums">
            {filtered.length} of {allLegs.length} legs
          </span>
        </div>

        <div className="space-y-2 max-h-[75vh] overflow-y-auto pr-1">
          {filtered.map((p) => {
            const blocked = blockedReason(p);
            const active = inCart.has(p.key);
            return (
              <LegSelectCard
                key={p.key}
                data={cardFromValuePick(p)}
                selected={active}
                disabled={!!blocked}
                disabledReason={blocked}
                onToggle={() => toggle(p)}
              />
            );
          })}
          {filtered.length === 0 && (
            <div className="sparky-card p-4 text-[12px] text-muted">
              No legs match these filters. The board itself may also be empty — check
              the Value Board tab, which explains why when that happens.
            </div>
          )}
        </div>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* The cart                                                          */}
      {/* ---------------------------------------------------------------- */}
      <div className="space-y-3 lg:sticky lg:top-4">
        <div className="sparky-card p-3 space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="home-section-title !mb-0">Your ticket</h3>
            <span className="text-[11px] text-muted tabular-nums">
              {cart.length}/{MAX_LEGS}
            </span>
          </div>

          {cart.length === 0 && (
            <p className="text-[11px] text-muted">
              Add any two legs from the board. They can come from different games and
              different markets, or from the same game — a spread and a total in one
              game are priced off that game&apos;s joint outcome distribution rather
              than by multiplying their prices.
            </p>
          )}

          {[...byGame.entries()].map(([eventId, group]) => (
            <div
              key={eventId}
              className={`rounded-md ${group.length > 1 ? "border border-violet-500/25 bg-violet-500/[0.04] p-1.5" : ""}`}
            >
              {group.length > 1 && (
                <div className="text-[9px] uppercase tracking-wide text-violet-300 px-1 pb-1">
                  Same game · {group.length} legs
                </div>
              )}
              {group.map((c) => (
                <div
                  key={c.key}
                  className="flex items-center gap-2 py-1 px-1 text-[12px]"
                >
                  {c.team_id ? <TeamLogo teamId={c.team_id} size={18} /> : null}
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{c.label}</div>
                    <div className="text-[10px] text-muted truncate">
                      {matchupLabel(c)}
                    </div>
                  </div>
                  <span className="tabular-nums shrink-0">
                    {americanOdds(c.price_american)}
                  </span>
                  <button
                    onClick={() => toggle(c)}
                    className="shrink-0 text-muted hover:text-white text-[14px] leading-none"
                    aria-label={`Remove ${c.label}`}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))}

          {cart.length > 0 && (
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={price}
                disabled={cart.length < 2 || pricing}
                className="sparky-btn sparky-btn--solid !py-1.5 !text-[12px] flex-1"
              >
                {pricing
                  ? "Pricing…"
                  : cart.length < 2
                    ? "Add one more leg"
                    : `Price this ${cart.length}-leg ticket`}
              </button>
              <button
                onClick={() => {
                  setCart([]);
                  setResult(null);
                }}
                className="sparky-btn !py-1.5 !text-[12px]"
              >
                Clear
              </button>
            </div>
          )}

          {sameGameCount > 0 && (
            <p className="text-[10px] text-violet-300/80">
              {sameGameCount === 1 ? "One game contributes" : `${sameGameCount} games contribute`}{" "}
              more than one leg. Those are priced jointly — see the fair price below
              once you price the ticket, and compare it with what your book quotes.
            </p>
          )}
        </div>

        {error && (
          <div className="sparky-card p-3 text-[12px] text-rose-300">{error}</div>
        )}

        {result && <TicketResult result={result} />}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- */

function TicketResult({ result }: { result: SparkyParlayResponse }) {
  const ticket = result.parlays?.[0];
  if (!ticket) {
    return (
      <div className="sparky-card p-3 text-[12px] text-muted">
        {result.message ?? "This combination could not be priced."}
      </div>
    );
  }
  const ev = ticket.expected_value ?? 0;
  const sgp = result.same_game ?? [];
  const pricing = ticket.pricing;

  return (
    <div className="space-y-3">
      <div className="sparky-card p-3 space-y-2">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] text-muted">Ticket price</span>
          <span className="text-lg tabular-nums font-semibold">
            {americanOdds(ticket.parlay_odds_american)}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[11px]">
          <Stat label="Hit chance" value={pct(ticket.hit_prob, 1)} />
          <Stat
            label="Expected value"
            value={pctPoints(ev * 100)}
            tone={ev > 0 ? "good" : "bad"}
          />
          <Stat label="Every leg wins" value={pct(ticket.all_win_prob, 1)} />
          <Stat label="Suggested stake" value={`${((ticket.kelly_fraction ?? 0) * 100).toFixed(2)}%`} />
        </div>

        {/* The receipts. A price with no account of where it came from is
            indistinguishable from a guess. */}
        {pricing && (
          <div className="pt-1 border-t border-white/5 space-y-1 text-[10px] text-muted">
            <Receipt
              label="Correlation vs multiplying the legs"
              value={`${(pricing.correlation_effect ?? 0) >= 0 ? "+" : ""}${((pricing.correlation_effect ?? 0) * 100).toFixed(2)}%`}
              help="What a naive builder gets wrong. Multiplying leg prices assumes the legs are independent; ours are not, and the sign says whether that helps or hurts this particular ticket."
            />
            <Receipt
              label="Value of pushes"
              value={`+${((pricing.push_effect ?? 0) * 100).toFixed(2)}%`}
              help="A whole-number leg can push, which voids that leg and re-prices the ticket smaller rather than losing it. A win/lose engine treats a push as a loss and undervalues the ticket by exactly this much."
            />
            <Receipt
              label="No selection penalty"
              value="0.00%"
              help="The winner's-curse discount applies to tickets the engine searched out and presented as best-of-N. You picked these legs, so there is no order statistic to correct for and none was charged."
            />
          </div>
        )}
      </div>

      {sgp.map((q) => (
        <SameGamePanel key={q.event_id} quote={q} />
      ))}

      {result.message && (
        <div className="sparky-card p-3 text-[11px] text-muted leading-relaxed">
          {result.message}
        </div>
      )}

      {(ticket.warnings ?? []).length > 0 && (
        <ul className="sparky-card p-3 space-y-1 text-[11px] text-amber-300/90 list-disc list-inside">
          {(ticket.warnings ?? []).map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      {ticket.explanation && (
        <p className="text-[11px] text-muted leading-relaxed px-1">{ticket.explanation}</p>
      )}
    </div>
  );
}

/**
 * The same-game panel leads with a fair price, not an EV — and the copy says
 * why in one sentence.
 *
 * This is the honest half of the feature. We can compute what a correlated
 * combination is worth; we cannot see what a book charges for it, because books
 * reprice same-game parlays instead of multiplying the legs and that number is
 * in no feed this app has. Showing an EV against the multiplied price would be
 * quoting the value of a bet nobody is offering — the single most common way a
 * same-game tool ends up recommending losers with a straight face.
 */
function SameGamePanel({ quote }: { quote: SparkySameGame }) {
  const lift = quote.correlation_effect;
  return (
    <div className="sparky-card p-3 space-y-2 border-violet-500/25">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-violet-300">
          Same-game legs
        </span>
        <HelpTip
          label="How this is priced"
          body="These legs share one game, so they are settled together on a discrete grid of every possible (margin, total) outcome, built from the game's own distribution and carrying real push mass at the key numbers. Each leg's win probability on that grid is fitted to match exactly what it shows on the value board, so the marginals come from the trusted single-leg pipeline and only the dependence between them comes from the model."
        />
      </div>

      <div className="flex items-baseline justify-between">
        <span className="text-[11px] text-muted">Worth at least</span>
        <span className="text-lg tabular-nums font-semibold text-violet-200">
          {americanOdds(quote.fair_american)}
        </span>
      </div>

      <p className="text-[11px] text-muted leading-relaxed">
        Your book will not pay {americanOdds(
          Math.round(
            quote.multiplied_decimal >= 2
              ? (quote.multiplied_decimal - 1) * 100
              : -100 / (quote.multiplied_decimal - 1),
          ),
        )}{" "}
        for these legs — it reprices correlated combinations, and we cannot see its
        number. Take the bet only if it quotes better than{" "}
        <strong className="text-violet-200">{americanOdds(quote.fair_american)}</strong>.
        That leaves{" "}
        <strong className={quote.shade_room > 0 ? "text-emerald-300" : "text-rose-300"}>
          {pct(quote.shade_room, 1)}
        </strong>{" "}
        of shading before it stops being worth taking.
      </p>

      <div className="grid grid-cols-2 gap-2 text-[11px] pt-1 border-t border-white/5">
        <Stat label="Both/all legs win" value={pct(quote.all_win_prob, 1)} />
        <Stat label="Ticket survives" value={pct(quote.survive_prob, 1)} />
        <Stat
          label="Correlation lift"
          value={`${lift >= 0 ? "+" : ""}${(lift * 100).toFixed(1)}%`}
          tone={lift >= 0 ? "good" : "bad"}
        />
        <Stat label="Chance of a push" value={pct(quote.push_any_prob, 1)} />
      </div>

      <p className="text-[10px] text-muted leading-relaxed">
        {lift >= 0
          ? "These legs pull in the same direction: when one lands the other is more likely to, so the combination is worth more than multiplying the two prices suggests. That gap is the whole reason a book prices these itself."
          : "These legs partly cancel: one landing makes the other less likely, so the combination is worth less than multiplying the two prices suggests."}
      </p>

      {quote.notes.map((n, i) => (
        <p key={i} className="text-[10px] text-amber-300/80">
          {n}
        </p>
      ))}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  return (
    <div>
      <div className="text-[10px] text-muted">{label}</div>
      <div
        className={`tabular-nums ${
          tone === "good" ? "text-emerald-400" : tone === "bad" ? "text-rose-300" : ""
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function Receipt({
  label,
  value,
  help,
}: {
  label: string;
  value: string;
  help: string;
}) {
  return (
    <div className="flex items-center justify-between gap-2" title={help}>
      <span>{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}
