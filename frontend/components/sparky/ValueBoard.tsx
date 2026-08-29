"use client";
import { useMemo, useState } from "react";
import { SparkyValueBoard, SparkyValueDiagnostics, SparkyValuePick } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { HelpTip } from "./HelpTip";
import { americanOdds, isNflMatchup, kickoff, pct } from "./format";

/**
 * The Value Board — what is actually worth betting on this slate.
 *
 * Replaces the confidence-sorted prediction grid that used to be the Sparky
 * dashboard. That grid answered "who wins", ranked the answer by how sure it
 * was, and therefore put 25-point favourites at the top of a page whose entire
 * purpose is finding bets. This one asks a different question of every side of
 * every market — moneyline, spread and total — and ranks by expected bankroll
 * growth among the bets that clear the bar.
 *
 * Three things this component is deliberate about:
 *
 * 1. **Nothing is hidden.** Rejected bets are one click away in "Priced out",
 *    each showing the reason it failed. "-2000 needs 96% to break even" teaches
 *    more than a missing row, and a board that filters silently is
 *    indistinguishable from a broken one.
 * 2. **The arithmetic is on the card.** EV, fair vs model, cents of value,
 *    our probability vs the break-even bar. Nothing on the card is a call
 *    to action or a suggested stake.
 * 3. **An empty board is a legitimate result** and says so in full sentences,
 *    rather than looking like a loading failure.
 */

type MarketFilter = "all" | "moneyline" | "spread" | "total";

const MARKET_TABS: { id: MarketFilter; label: string }[] = [
  { id: "all", label: "All markets" },
  { id: "moneyline", label: "Moneyline" },
  { id: "spread", label: "Spreads" },
  { id: "total", label: "Totals" },
];

type Strictness = "strict" | "balanced" | "loose";

const STRICTNESS: { id: Strictness; label: string; help: string }[] = [
  { id: "strict", label: "Strict", help: "3% EV, 62% confidence, 4+ books. Fewer bets, higher bar." },
  { id: "balanced", label: "Balanced", help: "1.5% EV, 55% confidence, 2+ books. The default." },
  { id: "loose", label: "Wide", help: "0.5% EV, 51% confidence, 1+ book. Shows nearly everything +EV." },
];

export function ValueBoard({
  board,
  isLoading,
  strictness,
  onStrictnessChange,
  onRebuild,
}: {
  board?: SparkyValueBoard;
  isLoading?: boolean;
  strictness: Strictness;
  onStrictnessChange: (s: Strictness) => void;
  onRebuild?: () => void;
}) {
  const [market, setMarket] = useState<MarketFilter>("all");
  const [showPricedOut, setShowPricedOut] = useState(false);

  const meta = board?.meta ?? {};

  // The `?? []` defaults live inside the memos on purpose: written outside,
  // each render produces a fresh array literal, which changes the dependency
  // identity every time and makes the memoization a no-op.
  // Thin edges are shown by default now. They are genuinely +EV; hiding them
  // behind a toggle was part of why the board could read as completely empty.
  const fbsPicks = useMemo(
    () =>
      (board?.picks ?? []).filter((p) =>
        isNflMatchup(p.matchup?.home_team_id, p.matchup?.away_team_id),
      ),
    [board?.picks],
  );
  const fbsBest = useMemo(
    () =>
      (board?.best_available ?? []).filter((p) =>
        isNflMatchup(p.matchup?.home_team_id, p.matchup?.away_team_id),
      ),
    [board?.best_available],
  );
  const fbsPricedOut = useMemo(
    () =>
      (board?.priced_out ?? []).filter((p) =>
        isNflMatchup(p.matchup?.home_team_id, p.matchup?.away_team_id),
      ),
    [board?.priced_out],
  );

  const visible = useMemo(
    () => fbsPicks.filter((p) => market === "all" || p.market === market),
    [fbsPicks, market],
  );

  const bestAvailable = useMemo(
    () => fbsBest.filter((p) => market === "all" || p.market === market),
    [fbsBest, market],
  );

  const visiblePricedOut = useMemo(
    () => fbsPricedOut.filter((p) => market === "all" || p.market === market),
    [fbsPricedOut, market],
  );

  if (isLoading) {
    return <div className="sparky-card p-6 text-sm text-muted">Pricing the slate…</div>;
  }

  return (
    <div className="space-y-5">
      <HealthBanner diagnostics={board?.diagnostics} onRebuild={onRebuild} />

      <BoardHeader board={board} />

      {/* Strictness */}
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        <span className="text-muted">How wide to cast the net</span>
        {STRICTNESS.map((s) => (
          <button
            key={s.id}
            onClick={() => onStrictnessChange(s.id)}
            title={s.help}
            className={`sparky-btn !py-1 !px-3 !text-[11px] ${
              strictness === s.id ? "sparky-btn--solid" : ""
            }`}
          >
            {s.label}
          </button>
        ))}
        <HelpTip
          label="Strictness"
          body="Widens the search gates only — minimum EV, minimum confidence, book count and the moneyline floor. It does not touch either safety check (the model/market sanity limit or the bad-quote limit), and even the widest setting still refuses heavy chalk. Widening a search and switching off a safety rail are different things and do not share a control."
        />
      </div>

      {/* Market filter */}
      <div className="flex items-center gap-2 flex-wrap">
        {MARKET_TABS.map((t) => {
          const summary = t.id === "all" ? null : board?.by_market?.[t.id];
          return (
            <button
              key={t.id}
              onClick={() => setMarket(t.id)}
              className={`sparky-tab ${market === t.id ? "sparky-tab--active" : ""}`}
            >
              {t.label}
              {summary ? (
                <span className="ml-1.5 text-[10px] text-muted tabular-nums">
                  {summary.priced}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {board?.message && visible.length === 0 && (
        <EmptyBoard message={board.message} onRebuild={onRebuild} />
      )}

      {/* The picks */}
      {visible.length > 0 && (
        <div className="space-y-3">
          {visible.map((p) => (
            <ValueRow key={p.key} pick={p} />
          ))}
        </div>
      )}

      {/* Never a blank page: when nothing qualified, show what came closest. */}
      {visible.length === 0 && bestAvailable.length > 0 && (
        <div className="space-y-3">
          <div>
            <h3 className="home-section-title">Closest numbers</h3>
            <p className="text-[11px] text-muted mt-0.5">
              Nothing cleared the EV / confidence bar on this slate. These are the
              best prices by EV, with what stopped each one — not a suggestion to
              take them.
            </p>
          </div>
          {bestAvailable.map((p) => (
            <ValueRow key={p.key} pick={p} />
          ))}
        </div>
      )}

      {/* Priced out — the rejected bets and why. */}
      {visiblePricedOut.length > 0 && (
        <div className="sparky-card p-4">
          <button
            onClick={() => setShowPricedOut((v) => !v)}
            className="flex items-center justify-between w-full text-left"
          >
            <div>
              <div className="text-sm font-medium text-white">
                Priced out · {visiblePricedOut.length}
              </div>
              <div className="text-[11px] text-muted mt-0.5">
                Bets Sparky considered and rejected, each with the reason. This is where
                heavy favorites live — along with quotes that looked too good to be a
                real price.
              </div>
            </div>
            <span className="text-cyan-300 text-xs shrink-0 ml-3">
              {showPricedOut ? "Hide" : "Show"}
            </span>
          </button>

          {showPricedOut && (
            <div className="mt-3 space-y-2 border-t border-white/5 pt-3">
              {visiblePricedOut.map((p) => (
                <div
                  key={p.key}
                  className="flex items-start justify-between gap-3 text-xs py-1.5"
                >
                  <div className="min-w-0 flex items-center gap-2">
                    {p.team_id ? <TeamLogo teamId={p.team_id} size={20} /> : <div className="w-5" />}
                    <span className="text-slate-300 shrink-0">{p.label}</span>
                    <span className="text-muted tabular-nums shrink-0">
                      {americanOdds(p.price_american)}
                    </span>
                  </div>
                  <span className="text-muted/80 text-right min-w-0">{p.reasons[0]}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <RejectionSummary diagnostics={board?.diagnostics} />

      <TrustFooter meta={meta} />
    </div>
  );
}

/* --------------------------------------------------------------------------
 * Header: what the board found, at a glance.
 * ----------------------------------------------------------------------- */

function BoardHeader({ board }: { board?: SparkyValueBoard }) {
  const m = board?.meta ?? {};

  return (
    <div className="grid grid-cols-2 gap-3">
      <Stat
        value={String(m.legs_priced ?? 0)}
        label="sides priced"
        help="Every side of every market on the slate — moneyline, spread and total, both ways — that had enough of a market to price at all."
      />
      <Stat
        value={String(m.games_considered ?? 0)}
        label="games"
        help="NFL games on the slate with a usable model distribution and a book price."
      />
    </div>
  );
}

function Stat({
  value,
  label,
  help,
  accent = "text-white",
}: {
  value: string;
  label: string;
  help: string;
  accent?: string;
}) {
  return (
    <div className="sparky-stat">
      <div className={`sparky-stat__value ${accent}`}>{value}</div>
      <div className="sparky-stat__label flex items-center">
        {label}
        <HelpTip label={label} body={help} />
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------
 * One bet.
 * ----------------------------------------------------------------------- */

function ValueRow({ pick }: { pick: SparkyValuePick }) {
  const away = pick.matchup?.away_team_id ?? pick.matchup?.away_team ?? "";
  const home = pick.matchup?.home_team_id ?? pick.matchup?.home_team ?? "";
  const tone = pick.roi_pct > 0 ? "playable" : "pass";

  return (
    <div className={`sparky-card p-4 sparky-value sparky-value--${tone}`}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {/* Left: the line */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[11px] text-muted flex-wrap">
            {pick.reason_code === "no_model" && (
              <span className="sparky-chip sparky-tier--pass">No model view</span>
            )}
            <span className="uppercase tracking-wide">{pick.market_label}</span>
            <span>·</span>
            <span>
              {away} @ {home}
            </span>
            <span>·</span>
            <span>{kickoff(pick.commence_time)}</span>
          </div>

          <div className="mt-2 flex items-center gap-2.5">
            {pick.team_id ? <TeamLogo teamId={pick.team_id} size={30} /> : null}
            <span className="text-lg font-semibold text-white">{pick.label}</span>
            <span className="text-lg tabular-nums text-emerald-300">
              {americanOdds(pick.price_american)}
            </span>
            {pick.book && <span className="text-[11px] text-muted">at {pick.book}</span>}
          </div>

          {/* The arithmetic, on the card. */}
          <div className="mt-2.5 flex items-center gap-x-4 gap-y-1 flex-wrap text-[11px] text-muted tabular-nums">
            <Metric
              label="EV"
              value={`${pick.roi_pct >= 0 ? "+" : ""}${pick.roi_pct.toFixed(1)}%`}
              accent={pick.roi_pct > 0 ? "text-emerald-300" : "text-red-400"}
              help="Expected profit per unit staked, at the best major-book price we found and after our edge is shrunk to what settled history supports. +5% means a 1u bet returns 0.05u on average."
            />
            <Metric
              label="fair"
              value={americanOdds(pick.fair_price_american)}
              help="What this side is worth with the book's vig removed, from the multi-book consensus of major US books. The gap between this and the offered price is the whole number."
            />
            <Metric
              label="model"
              value={americanOdds(pick.model_price_american)}
              accent="text-cyan-300"
              help="What our probability implies as a fair American price."
            />
            <Metric
              label="value"
              value={`${pick.cents_of_value >= 0 ? "+" : ""}${pick.cents_of_value}¢`}
              accent={pick.cents_of_value > 0 ? "text-emerald-300" : undefined}
              help="How many cents better than fair the offered price is. Ten cents of value at -110 is a serious number; a book that never gives it up is a book to stop using."
            />
            <Metric
              label="need"
              value={pct(pick.breakeven_vs_ours, 1)}
              help="Win probability required just to break even at this price, stated on the same basis as our number beside it so the two are directly comparable. On a whole-number line, pushes refund the stake, which is why this differs slightly from the raw implied odds."
            />
            <Metric
              label="ours"
              value={pct(pick.prob, 1)}
              accent="text-cyan-300"
              help="Our probability after calibration and after shrinking the model's disagreement with the market down to what history says is real."
            />
            {pick.push_prob > 0.005 && (
              <Metric
                label="push"
                value={pct(pick.push_prob, 1)}
                help="Chance the game lands exactly on this number and the stake is refunded. Priced from the discrete margin distribution, not a lookup table."
              />
            )}
            <Metric
              label="books"
              value={String(pick.n_books)}
              help="How many major US books quoted this market (DraftKings, FanDuel, BetMGM, Caesars, and peers). Offshore and sharp books are excluded."
            />
          </div>
        </div>

        {/* Right: confidence the number is real, not a stake. */}
        <div className="text-right shrink-0">
          <div className="text-[11px] text-muted flex items-center justify-end">
            <span className="tabular-nums text-slate-300">{pct(pick.p_edge_real, 0)}</span>
            <span className="ml-1">edge is real</span>
            <HelpTip
              label="Confidence the edge is real"
              body="Probability this number is genuinely +EV rather than our estimation noise pointing the right way by luck. It falls off toward the ends of the price curve, where the model has least resolution."
            />
          </div>
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-300/80 leading-relaxed">{pick.explanation}</p>
    </div>
  );
}

function Metric({
  label,
  value,
  help,
  accent,
}: {
  label: string;
  value: string;
  help: string;
  accent?: string;
}) {
  return (
    <span className="flex items-center">
      <span className="text-muted/70">{label}</span>
      <span className={`ml-1 font-semibold ${accent ?? "text-slate-200"}`}>{value}</span>
      <HelpTip label={label} body={help} />
    </span>
  );
}

/* --------------------------------------------------------------------------
 * Empty state and the trust footer.
 * ----------------------------------------------------------------------- */

function EmptyBoard({ message, onRebuild }: { message: string; onRebuild?: () => void }) {
  return (
    <div className="sparky-card p-6">
      <h3 className="text-base font-semibold text-white">No +EV sides on this slate</h3>
      <p className="text-sm text-muted mt-2 max-w-2xl leading-relaxed">{message}</p>
      <p className="text-xs text-muted/70 mt-3 max-w-2xl leading-relaxed">
        An empty board is a legitimate result, not a bug. Open &ldquo;Priced out&rdquo;
        below to see what was considered and why each one missed.
      </p>
      {onRebuild && (
        <button onClick={onRebuild} className="sparky-btn mt-4 !py-1.5 !px-4 !text-xs">
          Rebuild from the latest odds
        </button>
      )}
    </div>
  );
}

function TrustFooter({ meta }: { meta: SparkyValueBoard["meta"] }) {
  const bands = meta?.trust?.bands ?? {};
  const rules = meta?.rules ?? {};
  const anyFitted = Object.values(bands).some((b) => b?.fitted);

  return (
    <div className="sparky-card p-4 text-[11px] text-muted leading-relaxed">
      <div className="text-slate-300 font-medium mb-1.5">How much Sparky trusts itself</div>
      <p>
        Every edge below is shrunk toward the market before it is priced. How much survives
        depends on where the bet sits on the price curve — we discount claimed edge on heavy
        favorites far harder than in the middle of the board, because that is where the model
        has the least resolution and the de-vig is least reliable.
      </p>
      <div className="mt-2 flex gap-4 flex-wrap tabular-nums">
        {(["dog", "mid", "chalk"] as const).map((b) => (
          <span key={b}>
            <span className="text-muted/70">{b}</span>{" "}
            <span className="text-slate-200 font-semibold">
              λ {(bands[b]?.lambda ?? 0).toFixed(2)}
            </span>
            <span className="text-muted/60">
              {" "}
              ({bands[b]?.fitted ? "fitted" : "prior"})
            </span>
          </span>
        ))}
      </div>
      {!anyFitted && (
        <p className="mt-2 text-amber-300/80">
          No band has enough settled history to fit its own trust factor yet, so these are
          conservative priors rather than measurements. They will tighten on their own as
          results settle — including upward, if the model turns out to beat closing lines on
          favorites.
        </p>
      )}
      <p className="mt-2 text-muted/60">
        Display gates: {((rules.min_ev ?? 0) * 100).toFixed(0)}% EV ·{" "}
        {((rules.min_p_edge_real ?? 0) * 100).toFixed(0)}% confidence the edge is real ·{" "}
        {rules.min_books ?? 0}+ major books · moneylines shorter than{" "}
        {americanOdds(rules.ml_price_floor ?? 0)} are shown as priced-out rather than
        in the main list. Prices come from DraftKings, FanDuel, BetMGM, Caesars, and
        other major US books — not offshore or sharp shops.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------------------
 * Diagnostics: why the board looks the way it does.
 * ----------------------------------------------------------------------- */

/**
 * Data-health banner.
 *
 * This exists because an empty Value Board has two completely different causes
 * that look identical from the outside: the market really is efficient today,
 * or our own pipeline gave the scorer nothing to work with. The second is far
 * more common and it used to be invisible — the board reported "no edge at this
 * price" on every row, which reads as a claim about the market rather than an
 * admission that the prediction store was cold. Each warning names the fix.
 */
function HealthBanner({
  diagnostics,
  onRebuild,
}: {
  diagnostics?: SparkyValueDiagnostics;
  onRebuild?: () => void;
}) {
  const health = diagnostics?.health ?? [];
  if (!health.length) return null;

  return (
    <div className="space-y-2">
      {health.map((h) => (
        <div
          key={h.title}
          className={`sparky-card p-4 border ${
            h.level === "error"
              ? "border-red-500/40 bg-red-950/20"
              : "border-amber-500/40 bg-amber-950/15"
          }`}
        >
          <div
            className={`text-sm font-semibold ${
              h.level === "error" ? "text-red-300" : "text-amber-300"
            }`}
          >
            {h.title}
          </div>
          <p className="text-xs text-slate-300/80 mt-1.5 leading-relaxed">{h.detail}</p>
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            <span className="text-xs text-slate-200">
              <span className="text-muted">Fix:</span> {h.fix}
            </span>
            {onRebuild && (
              <button onClick={onRebuild} className="sparky-btn !py-1 !px-3 !text-[11px]">
                Rebuild slate
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * Where the board's sides went, as counts.
 *
 * Forty rows each carrying a sentence is not a summary. This is: one line per
 * rejection class, so "the model had no view on any of it" is legible at a
 * glance instead of requiring someone to read and tally the page by hand.
 */
function RejectionSummary({ diagnostics }: { diagnostics?: SparkyValueDiagnostics }) {
  const [open, setOpen] = useState(false);
  const rejections = diagnostics?.rejections ?? [];
  if (!rejections.length) return null;

  const found = diagnostics?.markets_found;
  const quoted = diagnostics?.markets_quoted;

  return (
    <div className="sparky-card p-4">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full text-left"
      >
        <div>
          <div className="text-sm font-medium text-white">Where every side went</div>
          <div className="text-[11px] text-muted mt-0.5">
            {diagnostics?.games_with_model ?? 0} of {diagnostics?.games_on_slate ?? 0} games
            had a model view · {rejections.reduce((a, r) => a + r.count, 0)} sides priced
          </div>
        </div>
        <span className="text-cyan-300 text-xs shrink-0 ml-3">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="mt-3 border-t border-white/5 pt-3 space-y-3">
          <div className="space-y-1">
            {rejections.map((r) => (
              <div key={r.code} className="flex items-center justify-between text-xs">
                <span className={r.code === "ok" ? "text-emerald-300" : "text-slate-300"}>
                  {r.label}
                </span>
                <span className="text-muted tabular-nums">{r.count}</span>
              </div>
            ))}
          </div>

          {found && quoted && (
            <div className="text-[11px] text-muted leading-relaxed border-t border-white/5 pt-2.5">
              <span className="text-slate-300">Markets priced:</span>{" "}
              {found.moneyline} moneyline · {found.spread} spread · {found.total} total.
              {(found.spread === 0 || found.total === 0) &&
                (quoted.spread > 0 || quoted.total > 0) && (
                  <>
                    {" "}
                    Books quoted spreads on {quoted.spread} games and totals on{" "}
                    {quoted.total}. If those markets are still missing, the usual
                    cause is a team-name mismatch between the odds feed and the
                    prediction row — not a missing odds pull.
                  </>
                )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
