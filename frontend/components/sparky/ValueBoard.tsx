"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { SparkyValueBoard, SparkyValueDiagnostics, SparkyValuePick } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { HelpTip } from "./HelpTip";
import { americanOdds, isNflMatchup, kickoff, pct } from "./format";

/**
 * The Value Board — every game on the slate, priced.
 *
 * +EV sides are highlighted, not used as a gate. A week where the model and
 * the market agree is still a week people want to look at; hiding those games
 * behind "priced out" made the board look empty every time the bar wasn't
 * cleared. Negative EV and model-vs-market stay on the card.
 */

type MarketFilter = "all" | "moneyline" | "spread" | "total";

const MARKET_TABS: { id: MarketFilter; label: string }[] = [
  { id: "all", label: "All markets" },
  { id: "moneyline", label: "Moneyline" },
  { id: "spread", label: "Spreads" },
  { id: "total", label: "Totals" },
];

const MARKET_ORDER: SparkyValuePick["market"][] = ["moneyline", "spread", "total"];

type Strictness = "strict" | "balanced" | "loose";

const STRICTNESS: { id: Strictness; label: string; help: string }[] = [
  { id: "strict", label: "Strict", help: "3% EV, 62% confidence, 4+ books. Fewer bets, higher bar." },
  { id: "balanced", label: "Balanced", help: "1.5% EV, 55% confidence, 2+ books. The default." },
  { id: "loose", label: "Wide", help: "0.5% EV, 51% confidence, 1+ book. Shows nearly everything +EV." },
];

type ViewMode = "slate" | "value";

const FAULT_CODES = new Set(["no_model", "bad_quote", "model_fault"]);

type GameGroup = {
  eventId: string;
  awayId: string;
  homeId: string;
  away: string;
  home: string;
  commenceTime: string | null;
  legs: SparkyValuePick[];
  bestEv: number;
  valueCount: number;
};

function isNflPick(p: SparkyValuePick): boolean {
  return isNflMatchup(p.matchup?.home_team_id, p.matchup?.away_team_id);
}

function isValueSide(p: SparkyValuePick): boolean {
  return p.roi_pct > 0 && !FAULT_CODES.has(p.reason_code);
}

function uniqueLegs(board?: SparkyValueBoard): SparkyValuePick[] {
  const seen = new Map<string, SparkyValuePick>();
  for (const bucket of [
    board?.picks ?? [],
    board?.best_available ?? [],
    board?.priced_out ?? [],
  ]) {
    for (const p of bucket) {
      if (!seen.has(p.key) && isNflPick(p)) seen.set(p.key, p);
    }
  }
  return [...seen.values()];
}

function sideRank(p: SparkyValuePick): number {
  const s = (p.side ?? "").toLowerCase();
  if (s === "away" || s === "over") return 0;
  if (s === "home" || s === "under") return 1;
  const label = p.label.toLowerCase();
  if (label.startsWith("over")) return 0;
  if (label.startsWith("under")) return 1;
  return 2;
}

function groupGames(legs: SparkyValuePick[]): GameGroup[] {
  const byEvent = new Map<string, SparkyValuePick[]>();
  for (const p of legs) {
    if (p.is_alt) continue;
    const list = byEvent.get(p.event_id) ?? [];
    list.push(p);
    byEvent.set(p.event_id, list);
  }

  const games: GameGroup[] = [];
  for (const [eventId, eventLegs] of byEvent) {
    const sample = eventLegs[0];
    const homeId = sample.matchup?.home_team_id ?? "";
    const awayId = sample.matchup?.away_team_id ?? "";
    games.push({
      eventId,
      awayId,
      homeId,
      away: sample.matchup?.away_team ?? awayId,
      home: sample.matchup?.home_team ?? homeId,
      commenceTime: sample.commence_time,
      legs: eventLegs,
      bestEv: Math.max(...eventLegs.map((p) => p.roi_pct)),
      valueCount: eventLegs.filter(isValueSide).length,
    });
  }

  games.sort((a, b) => {
    if (b.valueCount !== a.valueCount) return b.valueCount - a.valueCount;
    if (b.bestEv !== a.bestEv) return b.bestEv - a.bestEv;
    return (a.commenceTime ?? "").localeCompare(b.commenceTime ?? "");
  });
  return games;
}

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
  const [view, setView] = useState<ViewMode>("slate");

  const allLegs = useMemo(() => uniqueLegs(board), [board]);

  const consensus = useMemo(
    () => allLegs.filter((p) => !p.is_alt && (market === "all" || p.market === market)),
    [allLegs, market],
  );

  const valueLegs = useMemo(
    () =>
      consensus
        .filter(isValueSide)
        .sort((a, b) => b.roi_pct - a.roi_pct),
    [consensus],
  );

  const games = useMemo(() => groupGames(consensus), [consensus]);

  const visibleGames = useMemo(() => {
    if (view === "value" && valueLegs.length > 0) {
      return games.filter((g) => g.valueCount > 0);
    }
    return games;
  }, [view, games, valueLegs.length]);

  if (isLoading) {
    return <div className="sparky-card p-6 text-sm text-muted">Pricing the slate…</div>;
  }

  const noGames = visibleGames.length === 0;

  return (
    <div className="space-y-5">
      <HealthBanner diagnostics={board?.diagnostics} onRebuild={onRebuild} />

      <BoardHeader board={board} games={games.length} valueSides={valueLegs.length} />

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
          body="Widens what counts as a highlighted value side — minimum EV, minimum confidence, book count and the moneyline floor. It does not hide the rest of the slate, and it does not touch either safety check (the model/market sanity limit or the bad-quote limit). Even the widest setting still refuses to recommend heavy chalk."
        />
      </div>

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
        <span className="hidden sm:inline mx-1 text-muted/40">·</span>
        <button
          onClick={() => setView("slate")}
          className={`sparky-tab ${view === "slate" ? "sparky-tab--active" : ""}`}
        >
          All games
        </button>
        <button
          onClick={() => setView("value")}
          className={`sparky-tab ${view === "value" ? "sparky-tab--active" : ""}`}
        >
          Value only
          {valueLegs.length > 0 && (
            <span className="ml-1.5 text-[10px] tabular-nums">{valueLegs.length}</span>
          )}
        </button>
      </div>

      {noGames && (
        <EmptyBoard message={board?.message ?? "No games have been priced on this slate yet."} onRebuild={onRebuild} />
      )}

      {valueLegs.length === 0 && games.length > 0 && (
        <div className="sparky-card p-4">
          <div className="text-sm font-medium text-white">No +EV sides this week</div>
          <p className="text-xs text-muted mt-1 leading-relaxed max-w-2xl">
            After shrinking the model toward what settled history supports, nothing
            clears the bar. Every game is still on the board below with EV, the
            model price, and the market price — a pass is a number, not a missing
            card.
          </p>
        </div>
      )}

      {view === "value" && valueLegs.length === 0 && games.length > 0 && (
        <p className="text-[11px] text-muted">
          Nothing is +EV at this strictness, so the full slate is showing instead.
        </p>
      )}

      {visibleGames.length > 0 && (
        <div className="space-y-3">
          {visibleGames.map((g) => (
            <GameCard key={g.eventId} game={g} market={market} />
          ))}
        </div>
      )}

      <RejectionSummary diagnostics={board?.diagnostics} />

      <TrustFooter meta={board?.meta ?? {}} />
    </div>
  );
}

function BoardHeader({
  board,
  games,
  valueSides,
}: {
  board?: SparkyValueBoard;
  games: number;
  valueSides: number;
}) {
  const m = board?.meta ?? {};

  return (
    <div className="grid grid-cols-3 gap-2 sm:gap-3">
      <Stat
        value={String(games || m.games_considered || 0)}
        label="games"
        help="NFL games on the slate with a usable model distribution and a book price. Every one of them is on a card below, whether or not it is +EV."
      />
      <Stat
        value={String(m.legs_priced ?? 0)}
        label="sides priced"
        help="Every side of every market — moneyline, spread and total, both ways — that had enough of a market to price at all."
      />
      <Stat
        value={String(valueSides)}
        label="+EV sides"
        accent={valueSides > 0 ? "text-emerald-300" : "text-white"}
        help="Sides that stay positive after the model's edge is shrunk to what settled history supports. Zero is a legitimate week, not an empty board."
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

function GameCard({ game, market }: { game: GameGroup; market: MarketFilter }) {
  const grouped = new Map<SparkyValuePick["market"], SparkyValuePick[]>();
  for (const p of game.legs) {
    const list = grouped.get(p.market) ?? [];
    list.push(p);
    grouped.set(p.market, list);
  }
  for (const list of grouped.values()) list.sort((a, b) => sideRank(a) - sideRank(b));
  const byMarket = MARKET_ORDER.filter((m) => market === "all" || m === market)
    .map((m) => ({ market: m, sides: grouped.get(m) ?? [] }))
    .filter((block) => block.sides.length > 0);

  const tone =
    game.valueCount > 0 ? "playable" : game.bestEv < 0 ? "pass" : "thin";

  return (
    <div
      className={`sparky-card p-4 border-l-[3px] ${
        tone === "playable"
          ? "border-l-emerald-400/70"
          : tone === "pass"
            ? "border-l-slate-400/30"
            : "border-l-amber-400/45"
      }`}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] text-muted flex-wrap">
            <span>{kickoff(game.commenceTime)}</span>
            {game.valueCount > 0 ? (
              <span className="sparky-chip text-emerald-300 bg-emerald-500/15">
                {game.valueCount} value {game.valueCount === 1 ? "side" : "sides"}
              </span>
            ) : (
              <span className="sparky-chip text-slate-300 bg-slate-400/15">no edge</span>
            )}
          </div>
          <div className="mt-2 flex items-center gap-2.5 min-w-0">
            {game.awayId ? <TeamLogo teamId={game.awayId} size={28} /> : null}
            <span className="text-base font-semibold text-white truncate">{game.awayId || game.away}</span>
            <span className="text-muted text-sm">@</span>
            {game.homeId ? <TeamLogo teamId={game.homeId} size={28} /> : null}
            <span className="text-base font-semibold text-white truncate">{game.homeId || game.home}</span>
          </div>
        </div>
        <Link
          href={`/sparky/${encodeURIComponent(game.eventId)}`}
          className="text-[11px] text-cyan-300 hover:underline shrink-0"
        >
          Open game →
        </Link>
      </div>

      <div className="mt-3 space-y-3">
        {byMarket.map((block) => (
          <div key={block.market}>
            <div className="text-[10px] uppercase tracking-wide text-muted/80 mb-1">
              {block.sides[0]?.market_label ?? block.market}
            </div>
            <div className="space-y-1">
              {block.sides.map((p) => (
                <SideRow key={p.key} pick={p} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SideRow({ pick }: { pick: SparkyValuePick }) {
  const value = isValueSide(pick);
  const evClass =
    pick.roi_pct > 0 ? "text-emerald-300" : pick.roi_pct < 0 ? "text-red-400" : "text-slate-300";

  return (
    <div
      className={`rounded-lg px-2 py-1.5 ${value ? "bg-emerald-500/10 shadow-[inset_2px_0_0_#34d399]" : ""}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        {pick.team_id ? <TeamLogo teamId={pick.team_id} size={18} /> : <div className="w-[18px]" />}
        <span className="text-sm text-slate-100 font-medium truncate min-w-0">{pick.label}</span>
        <span className="text-sm tabular-nums text-slate-200 shrink-0">
          {americanOdds(pick.price_american)}
        </span>
        {pick.book && <span className="text-[10px] text-muted shrink-0 hidden sm:inline">at {pick.book}</span>}
        <span className={`ml-auto text-sm tabular-nums font-semibold shrink-0 ${evClass}`}>
          {pick.roi_pct >= 0 ? "+" : ""}
          {pick.roi_pct.toFixed(1)}% EV
        </span>
      </div>
      <div className="mt-1 ml-7 flex items-center gap-x-3 gap-y-0.5 flex-wrap text-[11px] text-muted tabular-nums">
        <Metric
          label="model"
          value={americanOdds(pick.model_price_american)}
          accent="text-cyan-300"
          help="What our probability implies as a fair American price."
        />
        <Metric
          label="market"
          value={americanOdds(pick.fair_price_american)}
          help="No-vig consensus from major US books. The gap between this and the offered price is cents of value."
        />
        <Metric
          label="value"
          value={`${pick.cents_of_value >= 0 ? "+" : ""}${pick.cents_of_value}¢`}
          accent={pick.cents_of_value > 0 ? "text-emerald-300" : undefined}
          help="How many cents better than fair the offered price is."
        />
        <Metric
          label="ours"
          value={pct(pick.prob, 1)}
          accent="text-cyan-300"
          help="Our probability after calibration and after shrinking the model's disagreement with the market down to what history says is real."
        />
        <Metric
          label="need"
          value={pct(pick.breakeven_vs_ours, 1)}
          help="Win probability required just to break even at this price, on the same basis as our number."
        />
        {pick.push_prob > 0.005 && (
          <Metric
            label="push"
            value={pct(pick.push_prob, 1)}
            help="Chance the game lands exactly on this number and the stake is refunded. Priced from the discrete margin distribution — NFL ties and key numbers included."
          />
        )}
        {pick.reason_code !== "ok" && pick.reason_code !== "no_edge" && (
          <span className="text-muted/70 truncate max-w-[14rem]" title={pick.reasons[0]}>
            {pick.reason_label}
          </span>
        )}
      </div>
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

function EmptyBoard({ message, onRebuild }: { message: string; onRebuild?: () => void }) {
  return (
    <div className="sparky-card p-6">
      <h3 className="text-base font-semibold text-white">Nothing priced on this slate</h3>
      <p className="text-sm text-muted mt-2 max-w-2xl leading-relaxed">{message}</p>
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
        Highlight gates: {((rules.min_ev ?? 0) * 100).toFixed(0)}% EV ·{" "}
        {((rules.min_p_edge_real ?? 0) * 100).toFixed(0)}% confidence the edge is real ·{" "}
        {rules.min_books ?? 0}+ major books · moneylines shorter than{" "}
        {americanOdds(rules.ml_price_floor ?? 0)} are flagged, not recommended, but still
        shown with the EV. Prices come from DraftKings, FanDuel, BetMGM, Caesars, and
        other major US books — not offshore or sharp shops.
      </p>
    </div>
  );
}

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
