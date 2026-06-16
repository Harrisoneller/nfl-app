// Odds grouping + best-line selection. Ported verbatim from the web app's
// app/odds/page.tsx helpers so the mobile board surfaces identical numbers.

import { type OddsLine, type BetLegInput, type BetMarket } from "./api";
import { NFL_TEAM_NAMES } from "./team-names";

export type GroupedGame = {
  eventId: string;
  homeName: string;
  awayName: string;
  homeId: string | null;
  awayId: string | null;
  kickoff: string | null;
  lines: OddsLine[];
};

export function groupByEvent(lines: OddsLine[]): GroupedGame[] {
  const map = new Map<string, GroupedGame>();
  for (const l of lines) {
    const key = l.event_id ?? `${l.home_team}|${l.away_team}|${l.commence_time}`;
    if (!map.has(key)) {
      map.set(key, {
        eventId: key,
        homeName: l.home_team ?? "Home",
        awayName: l.away_team ?? "Away",
        homeId: NFL_TEAM_NAMES[(l.home_team ?? "").toLowerCase()] ?? null,
        awayId: NFL_TEAM_NAMES[(l.away_team ?? "").toLowerCase()] ?? null,
        kickoff: l.commence_time,
        lines: [],
      });
    }
    map.get(key)!.lines.push(l);
  }
  return Array.from(map.values()).sort((a, b) => {
    if (!a.kickoff) return 1;
    if (!b.kickoff) return -1;
    return a.kickoff.localeCompare(b.kickoff);
  });
}

export function bestSpread(game: GroupedGame) {
  const spreads = game.lines.filter((l) => l.market === "spreads" && l.point != null);
  if (spreads.length === 0) return null;
  const valueCount: Record<string, number> = {};
  for (const s of spreads) {
    const k = `${Math.abs(s.point ?? 0).toFixed(1)}`;
    valueCount[k] = (valueCount[k] ?? 0) + 1;
  }
  const modal = Object.entries(valueCount).sort(([, a], [, b]) => b - a)[0]?.[0];
  if (!modal) return null;
  const modalNum = parseFloat(modal);

  const atModal = spreads.filter((s) => Math.abs(Math.abs(s.point!) - modalNum) < 0.05);
  const home = atModal.filter((s) => s.label === game.homeName);
  const away = atModal.filter((s) => s.label === game.awayName);
  const bestHome = home.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  const bestAway = away.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  if (!bestHome || !bestAway) return null;
  return {
    book: bestHome.bookmaker === bestAway.bookmaker ? bestHome.bookmaker : "best",
    home: { point: bestHome.point, price: bestHome.price },
    away: { point: bestAway.point, price: bestAway.price },
  };
}

export function bestTotal(game: GroupedGame) {
  const totals = game.lines.filter((l) => l.market === "totals" && l.point != null);
  if (totals.length === 0) return null;
  const valueCount: Record<string, number> = {};
  for (const t of totals) {
    const k = (t.point ?? 0).toFixed(1);
    valueCount[k] = (valueCount[k] ?? 0) + 1;
  }
  const modal = Object.entries(valueCount).sort(([, a], [, b]) => b - a)[0]?.[0];
  if (!modal) return null;
  const modalNum = parseFloat(modal);
  const atModal = totals.filter((t) => Math.abs((t.point ?? 0) - modalNum) < 0.05);
  const overs = atModal.filter((t) => t.label.toLowerCase() === "over");
  const unders = atModal.filter((t) => t.label.toLowerCase() === "under");
  const bestO = overs.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  const bestU = unders.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  return {
    book: bestO?.bookmaker === bestU?.bookmaker ? bestO?.bookmaker ?? "" : "best",
    point: modalNum,
    overPrice: bestO?.price ?? null,
    underPrice: bestU?.price ?? null,
  };
}

export function bestMoneyline(game: GroupedGame) {
  const mls = game.lines.filter((l) => l.market === "h2h");
  if (mls.length === 0) return null;
  const home = mls.filter((l) => l.label === game.homeName);
  const away = mls.filter((l) => l.label === game.awayName);
  const bestHome = home.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  const bestAway = away.sort((a, b) => (b.price ?? -9999) - (a.price ?? -9999))[0];
  if (!bestHome || !bestAway) return null;
  return {
    book: bestHome.bookmaker === bestAway.bookmaker ? bestHome.bookmaker : "best",
    home: bestHome.price,
    away: bestAway.price,
  };
}

export function uniqueBooks(lines: OddsLine[]): number {
  return new Set(lines.map((l) => l.bookmaker)).size;
}

export function impliedProb(price: number | null | undefined): string {
  if (price == null) return "";
  const p = price > 0 ? 100 / (price + 100) : -price / (-price + 100);
  return `${(p * 100).toFixed(0)}% implied`;
}

export function marketLabel(m: string): string {
  if (m === "h2h") return "Moneyline";
  if (m === "spreads") return "Spread";
  if (m === "totals") return "Total";
  return m;
}

/** Build a prefilled bet leg from a priced side for one-tap tracking. */
export function legFor(
  game: GroupedGame,
  market: BetMarket,
  selection: string | null,
  line: number | null | undefined,
  price: number | null | undefined,
): BetLegInput | null {
  if (!selection || price == null) return null;
  if ((market === "spread" || market === "total") && line == null) return null;
  const label =
    market === "moneyline"
      ? `${selection} ML`
      : market === "spread"
        ? `${selection} ${line! > 0 ? "+" : ""}${line}`
        : `${selection === "over" ? "Over" : "Under"} ${line}`;
  return {
    market,
    selection,
    selection_label: label,
    line: line ?? null,
    odds_american: price,
    event_id: game.eventId,
    home_team_id: game.homeId,
    away_team_id: game.awayId,
    commence_time: game.kickoff,
  };
}
