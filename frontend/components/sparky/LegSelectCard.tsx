"use client";
import { SparkyLeg, SparkyLegMenuGame, SparkyValuePick } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { HelpTip } from "./HelpTip";
import {
  americanOdds,
  centsOfValue,
  impliedToAmerican,
  kickoff,
  pct,
} from "./format";

/**
 * Clickable leg card for the parlay builder.
 *
 * The mix-and-match cart used to render a one-line row (label, price, EV).
 * That hid the same numbers the Value Board leads with — fair vs model,
 * cents of value, our probability vs the break-even bar — so a user picking
 * legs for a ticket had to flip tabs to know whether the pick was any good.
 * This card is that board's row, sized for a selectable list.
 */

const MARKET_LABEL: Record<string, string> = {
  moneyline: "Moneyline",
  spread: "Spread",
  total: "Total",
};

const TIER_LABEL: Record<string, string> = {
  strong: "Strong",
  playable: "Playable",
  thin: "Thin",
  pass: "No bet",
};

export type LegCardModel = {
  key: string;
  label: string;
  teamId?: string | null;
  homeId?: string | null;
  awayId?: string | null;
  market: string;
  marketLabel: string;
  priceAmerican: number;
  book?: string | null;
  commenceTime?: string | null;
  matchup: string;
  isAlt?: boolean;
  tier?: string;
  evPct: number;
  fairAmerican?: number | null;
  modelAmerican?: number | null;
  centsOfValue?: number | null;
  ours?: number | null;
  need?: number | null;
  nBooks?: number | null;
  pushProb?: number | null;
};

export function cardFromValuePick(p: SparkyValuePick): LegCardModel {
  const home = p.matchup?.home_team_id ?? p.matchup?.home_team ?? "";
  const away = p.matchup?.away_team_id ?? p.matchup?.away_team ?? "";
  return {
    key: p.key,
    label: p.label,
    teamId: p.team_id,
    homeId: p.matchup?.home_team_id,
    awayId: p.matchup?.away_team_id,
    market: p.market,
    marketLabel: p.market_label || MARKET_LABEL[p.market] || p.market,
    priceAmerican: p.price_american,
    book: p.book,
    commenceTime: p.commence_time,
    matchup: home && away ? `${away} @ ${home}` : p.event_id,
    isAlt: p.is_alt,
    tier: p.tier,
    evPct: p.roi_pct,
    fairAmerican: p.fair_price_american,
    modelAmerican: p.model_price_american,
    centsOfValue: p.cents_of_value,
    ours: p.prob,
    need: p.breakeven_vs_ours,
    nBooks: p.n_books,
    pushProb: p.push_prob,
  };
}

export function cardFromMenuLeg(leg: SparkyLeg, game: SparkyLegMenuGame): LegCardModel {
  const fairAm = impliedToAmerican(leg.fair_prob);
  const modelAm = impliedToAmerican(leg.prob ?? leg.model_prob);
  const price = leg.price_american;
  const push = leg.push_prob ?? 0;
  const dec =
    price > 0 ? 1 + price / 100 : price < 0 ? 1 + 100 / -price : 2;
  const be = 1 / dec;
  const need = push > 0 && push < 1 ? Math.min(1, be / (1 - push)) : be;
  const home = game.home_team_id ?? game.home_team ?? "";
  const away = game.away_team_id ?? game.away_team ?? "";
  const market = leg.market ?? "moneyline";
  return {
    key: leg.key ?? `${leg.event_id}:${market}:${leg.side}:${leg.line ?? ""}`,
    label: leg.label ?? leg.team_id ?? "Leg",
    teamId: leg.team_id,
    homeId: game.home_team_id,
    awayId: game.away_team_id,
    market,
    marketLabel: MARKET_LABEL[market] ?? market,
    priceAmerican: price,
    book: leg.book,
    commenceTime: game.commence_time,
    matchup: home && away ? `${away} @ ${home}` : game.event_id,
    isAlt: leg.is_alt,
    evPct: (leg.expected_value ?? 0) * 100,
    fairAmerican: fairAm,
    modelAmerican: modelAm,
    centsOfValue:
      fairAm != null ? centsOfValue(price, fairAm) : null,
    ours: leg.prob ?? leg.model_prob,
    need,
    nBooks: leg.n_books,
    pushProb: leg.push_prob,
  };
}

export function LegSelectCard({
  data,
  selected,
  disabled,
  disabledReason,
  onToggle,
}: {
  data: LegCardModel;
  selected: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  onToggle: () => void;
}) {
  const tier = data.tier ?? (data.evPct > 3 ? "playable" : data.evPct > 0 ? "thin" : "pass");

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-pressed={selected}
      aria-disabled={disabled || undefined}
      title={disabledReason ?? undefined}
      onClick={() => {
        if (!disabled) onToggle();
      }}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
      className={`sparky-card p-4 sparky-value sparky-value--${tier} sparky-leg-card text-left w-full
        ${selected ? "sparky-leg-card--active" : ""}
        ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[11px] text-muted flex-wrap">
            {data.tier && (
              <span className={`sparky-chip sparky-tier--${data.tier}`}>
                {TIER_LABEL[data.tier] ?? data.tier}
              </span>
            )}
            <span className="uppercase tracking-wide">{data.marketLabel}</span>
            {data.isAlt && (
              <span className="sparky-chip sparky-leg-card__alt">alt</span>
            )}
            <span>·</span>
            <span>{data.matchup}</span>
            {data.commenceTime && (
              <>
                <span>·</span>
                <span>{kickoff(data.commenceTime)}</span>
              </>
            )}
          </div>

          <div className="mt-2 flex items-center gap-2.5 min-w-0">
            <LegLogos
              teamId={data.teamId}
              homeId={data.homeId}
              awayId={data.awayId}
              market={data.market}
            />
            <span className="text-lg font-semibold text-white truncate">
              {data.label}
            </span>
            <span className="text-lg tabular-nums text-emerald-300 shrink-0">
              {americanOdds(data.priceAmerican)}
            </span>
            {data.book && (
              <span className="text-[11px] text-muted shrink-0">at {data.book}</span>
            )}
          </div>

          <div
            className="mt-2.5 flex items-center gap-x-4 gap-y-1 flex-wrap text-[11px] text-muted tabular-nums"
            onClick={(e) => e.stopPropagation()}
          >
            <Metric
              label="EV"
              value={`${data.evPct >= 0 ? "+" : ""}${data.evPct.toFixed(1)}%`}
              accent={data.evPct > 0 ? "text-emerald-300" : "text-red-400"}
              help="Expected profit per unit staked at this price, after the model's edge is shrunk to what settled history supports."
            />
            {data.fairAmerican != null && (
              <Metric
                label="market"
                value={americanOdds(data.fairAmerican)}
                help="No-vig consensus — what the market says this side is worth with the book's hold removed."
              />
            )}
            {data.modelAmerican != null && (
              <Metric
                label="model"
                value={americanOdds(data.modelAmerican)}
                accent="text-cyan-300"
                help="What our probability implies as a fair American price. The gap versus market is the whole bet."
              />
            )}
            {data.centsOfValue != null && (
              <Metric
                label="value"
                value={`${data.centsOfValue >= 0 ? "+" : ""}${data.centsOfValue}¢`}
                accent={data.centsOfValue > 0 ? "text-emerald-300" : undefined}
                help="How many cents better than fair the offered price is."
              />
            )}
            {data.ours != null && (
              <Metric
                label="ours"
                value={pct(data.ours, 1)}
                accent="text-cyan-300"
                help="Our win probability after calibration and edge shrinkage."
              />
            )}
            {data.need != null && (
              <Metric
                label="need"
                value={pct(data.need, 1)}
                help="Win probability required to break even at this price, on the same basis as 'ours'."
              />
            )}
            {data.pushProb != null && data.pushProb > 0.005 && (
              <Metric
                label="push"
                value={pct(data.pushProb, 1)}
                help="Chance the game lands exactly on this number and the stake is refunded."
              />
            )}
            {data.nBooks != null && data.nBooks > 0 && (
              <Metric
                label="books"
                value={String(data.nBooks)}
                help="How many books quoted this market."
              />
            )}
          </div>
        </div>

        <span
          className={`shrink-0 w-9 h-9 rounded-full grid place-items-center text-lg leading-none border
            ${selected ? "border-emerald-400/70 text-emerald-300 bg-emerald-500/15" : "border-white/15 text-muted"}`}
          aria-hidden
        >
          {selected ? "−" : "+"}
        </span>
      </div>
    </div>
  );
}

function LegLogos({
  teamId,
  homeId,
  awayId,
  market,
}: {
  teamId?: string | null;
  homeId?: string | null;
  awayId?: string | null;
  market: string;
}) {
  if (market === "total" || !teamId) {
    return (
      <span className="flex items-center shrink-0">
        {awayId ? <TeamLogo teamId={awayId} size={28} /> : null}
        {homeId ? <TeamLogo teamId={homeId} size={28} className="-ml-1" /> : null}
      </span>
    );
  }
  return <TeamLogo teamId={teamId} size={30} />;
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
