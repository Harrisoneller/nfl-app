"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api, OddsLine } from "@/lib/api";
import { Card } from "@/components/Card";
import { TeamLogo } from "@/components/TeamLogo";

/**
 * Layman-friendly odds page.
 *
 * Each game shows up as its own card. For every market (spread / total /
 * moneyline) we surface the *best line per side* across books — the casual
 * user sees "PHI -3.5 (-105)" rather than a sea of identical numbers from
 * eight different sportsbooks. Each card has a one-line plain-English
 * explanation of how to read it.
 */

const oddsFetcher = () => api.odds(undefined, 400);
const statusFetcher = () => api.oddsStatus();

// Map full-name strings from The Odds API back to our 3-letter team ids.
import { NFL_TEAM_NAMES } from "@/lib/team-names";

export default function OddsPage() {
  const { data, isLoading, error } = useSWR(["odds-all"], oddsFetcher);
  const { data: status } = useSWR(["odds-status"], statusFetcher);
  const [showExplainer, setShowExplainer] = useState(true);
  const [showBoard, setShowBoard] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setShowBoard(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  const grouped = useMemo(() => groupByEvent(data ?? []), [data]);

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">Odds board</h1>
          <p className="text-sm text-muted mt-1">
            Best lines across major sportsbooks. Click a game to see every book.
          </p>
          {status?.last_updated && (
            <p className="text-xs text-muted/80 mt-1" title={freshnessTooltip(status)}>
              {freshnessLabel(status)}
            </p>
          )}
        </div>
        <button
          onClick={() => setShowExplainer((v) => !v)}
          className="text-xs px-3 py-1.5 rounded-full border divider hover:border-team-primary text-muted hover:text-text"
        >
          {showExplainer ? "Hide explainer" : "How do I read this?"}
        </button>
      </div>

      {showExplainer && <ExplainerCard />}

      {isLoading && <Card><p className="text-sm text-muted">Loading lines…</p></Card>}
      {error && <Card><p className="text-sm text-red-400">Couldn't load odds.</p></Card>}
      {!isLoading && grouped.length === 0 && (
        <OddsEmptyState status={status} />
      )}

      {!showBoard ? (
        <Card><p className="text-sm text-muted">Loading matchup cards…</p></Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {grouped.map((g) => (
            <GameCard key={g.eventId} game={g} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Empty state — uses /odds/status for actionable copy
// ============================================================================

function OddsEmptyState({
  status,
}: {
  status?: { configured: boolean; lines_in_db: number; ready: boolean; last_updated?: string | null };
}) {
  if (!status?.configured) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No odds in the database yet. Add a free key from{" "}
          <a
            href="https://the-odds-api.com"
            className="text-team-primary hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            the-odds-api.com
          </a>{" "}
          as <code className="text-xs px-1 bg-bg rounded">ODDS_API_KEY</code> in the repo-root{" "}
          <code className="text-xs px-1 bg-bg rounded">.env</code> (key only on that line — no
          inline comment), restart the backend, then run{" "}
          <code className="text-xs px-1 bg-bg rounded">curl -X POST http://localhost:8000/admin/refresh/odds</code>.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <p className="text-sm text-muted">
        <code className="text-xs px-1 bg-bg rounded">ODDS_API_KEY</code> is set, but no lines are
        stored ({status.lines_in_db} in DB). The Odds API likely rejected the key (401) or returned
        no NFL games. Confirm the key at{" "}
        <a
          href="https://the-odds-api.com/account"
          className="text-team-primary hover:underline"
          target="_blank"
          rel="noreferrer"
        >
          your account
        </a>
        , restart the backend after updating <code className="text-xs px-1 bg-bg rounded">.env</code>
        , then refresh:{" "}
        <code className="text-xs px-1 bg-bg rounded">curl -X POST http://localhost:8000/admin/refresh/odds</code>.
        The response includes <code className="text-xs px-1 bg-bg rounded">status</code> and{" "}
        <code className="text-xs px-1 bg-bg rounded">message</code> if the upstream call failed.
      </p>
    </Card>
  );
}

// ============================================================================
// Plain-English explainer (collapsible)
// ============================================================================

function ExplainerCard() {
  return (
    <Card title="Quick guide to reading these numbers">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
        <div>
          <div className="font-semibold mb-1">Spread</div>
          <p className="text-muted text-xs leading-relaxed">
            A handicap. "PHI -3.5" means the Eagles must win by 4+ for that bet
            to cash. The other team being "+3.5" means they can lose by 3 (or
            win outright) and still cover.
          </p>
        </div>
        <div>
          <div className="font-semibold mb-1">Total (Over/Under)</div>
          <p className="text-muted text-xs leading-relaxed">
            The combined points the books expect. Bet "Over 47.5" if you
            think the game will be high-scoring, "Under" if you think it'll
            be a defensive battle.
          </p>
        </div>
        <div>
          <div className="font-semibold mb-1">Moneyline</div>
          <p className="text-muted text-xs leading-relaxed">
            Straight-up "who wins?" — no spread. A number like{" "}
            <span className="font-mono">-150</span> means risk $150 to win $100
            (favorite). <span className="font-mono">+170</span> means risk $100 to win $170 (underdog).
          </p>
        </div>
      </div>
    </Card>
  );
}

// Relative "as of" string for the lines-freshness label.
function formatAsOf(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

// Format the cron's next-fire timestamp as a relative "in 3h" hint.
function formatInFuture(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "soon";
  const mins = Math.round((then - Date.now()) / 60000);
  if (mins <= 0) return "imminently";
  if (mins < 60) return `in ${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `in ${hrs}h`;
  const days = Math.round(hrs / 24);
  return `in ${days}d`;
}

// Context-aware freshness copy. The cron fires every 12h, but in offseason the
// budget guard correctly *skips* the pull (no point spending API credits when no
// game kicks off within ~10 days). When that happens we say so explicitly
// instead of misleadingly claiming "refreshed twice daily" while showing 9d-old
// lines.
type OddsStatus = {
  configured: boolean;
  lines_in_db: number;
  ready: boolean;
  last_updated: string | null;
  last_attempt?: { at: string | null; status: string | null; lines_in_db: number | null } | null;
  next_refresh_at?: string | null;
  refresh_hours_utc?: string | null;
  lookahead_days?: number | null;
};

function freshnessLabel(status: OddsStatus): string {
  if (!status.last_updated) return "No lines yet.";
  const asOf = `Lines as of ${formatAsOf(status.last_updated)}`;
  const attemptStatus = status.last_attempt?.status;
  const nextHint = status.next_refresh_at
    ? ` · next check ${formatInFuture(status.next_refresh_at)}`
    : "";

  if (attemptStatus === "skipped_offseason") {
    const days = status.lookahead_days ?? 10;
    return `${asOf} · offseason — pulls pause until a game is within ~${days} days${nextHint}`;
  }
  if (attemptStatus === "error") {
    return `${asOf} · last refresh failed${nextHint}`;
  }
  if (attemptStatus === "disabled") {
    return `${asOf} · refresh disabled (no ODDS_API_KEY set)`;
  }
  // ok / skipped_fresh / unknown — normal twice-daily cadence.
  return `${asOf} · refreshed twice daily${nextHint}`;
}

function freshnessTooltip(status: OddsStatus): string {
  const parts: string[] = [];
  if (status.last_updated) parts.push(`Lines written: ${new Date(status.last_updated).toLocaleString()}`);
  if (status.last_attempt?.at) {
    parts.push(
      `Last cron attempt: ${new Date(status.last_attempt.at).toLocaleString()} (${status.last_attempt.status ?? "unknown"})`,
    );
  }
  if (status.next_refresh_at) parts.push(`Next: ${new Date(status.next_refresh_at).toLocaleString()}`);
  if (status.refresh_hours_utc) parts.push(`Cron hours (UTC): ${status.refresh_hours_utc}`);
  return parts.join("\n");
}

// ============================================================================
// Game card — three rows (spread, total, ML) with best line surfaced
// ============================================================================

type GroupedGame = {
  eventId: string;
  homeName: string;
  awayName: string;
  homeId: string | null;
  awayId: string | null;
  kickoff: string | null;
  lines: OddsLine[];
};

function GameCard({ game }: { game: GroupedGame }) {
  const [expanded, setExpanded] = useState(false);

  const spread = bestSpread(game);
  const total = bestTotal(game);
  const ml = bestMoneyline(game);

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <Matchup g={game} />
        <div className="text-[11px] text-muted text-right">{prettyKickoff(game.kickoff)}</div>
      </div>

      <div className="space-y-2.5">
        <Row label="Spread" hint="Handicap">
          {spread ? (
            <div className="grid grid-cols-2 gap-2">
              <SideBox
                teamId={game.awayId}
                teamName={game.awayName}
                primary={`${formatSpreadNum(spread.away.point)}`}
                secondary={formatAmerican(spread.away.price)}
                book={spread.book}
              />
              <SideBox
                teamId={game.homeId}
                teamName={game.homeName}
                primary={`${formatSpreadNum(spread.home.point)}`}
                secondary={formatAmerican(spread.home.price)}
                book={spread.book}
              />
            </div>
          ) : <NoLine />}
        </Row>

        <Row label="Total" hint="Combined points">
          {total ? (
            <div className="grid grid-cols-2 gap-2">
              <SideBox
                teamId={null}
                teamName="Over"
                primary={`${total.point != null ? "O " + total.point : "—"}`}
                secondary={formatAmerican(total.overPrice)}
                book={total.book}
              />
              <SideBox
                teamId={null}
                teamName="Under"
                primary={`${total.point != null ? "U " + total.point : "—"}`}
                secondary={formatAmerican(total.underPrice)}
                book={total.book}
              />
            </div>
          ) : <NoLine />}
        </Row>

        <Row label="Moneyline" hint="Straight-up winner">
          {ml ? (
            <div className="grid grid-cols-2 gap-2">
              <SideBox
                teamId={game.awayId}
                teamName={game.awayName}
                primary={formatAmerican(ml.away)}
                secondary={impliedProb(ml.away)}
                book={ml.book}
              />
              <SideBox
                teamId={game.homeId}
                teamName={game.homeName}
                primary={formatAmerican(ml.home)}
                secondary={impliedProb(ml.home)}
                book={ml.book}
              />
            </div>
          ) : <NoLine />}
        </Row>
      </div>

      <div className="mt-3 flex justify-between items-center">
        <button
          onClick={() => setExpanded((e) => !e)}
          className="text-[11px] text-muted hover:text-text underline"
        >
          {expanded ? "Hide" : "See"} all books ({uniqueBooks(game.lines)})
        </button>
        {game.homeId && game.awayId && (
          <Link
            href={`/h2h/${game.awayId}/${game.homeId}`}
            className="text-[11px] text-team-primary hover:underline"
          >
            Full matchup →
          </Link>
        )}
      </div>

      {expanded && <AllBooksTable lines={game.lines} />}
    </Card>
  );
}

function Row({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</span>
        <span className="text-[10px] text-muted">{hint}</span>
      </div>
      {children}
    </div>
  );
}

function SideBox({
  teamId, teamName, primary, secondary, book,
}: {
  teamId: string | null;
  teamName: string;
  primary: string;
  secondary?: string;
  book?: string;
}) {
  return (
    <div className="bg-bg/70 border divider rounded-lg px-3 py-2 flex items-center gap-2.5">
      {teamId ? (
        <TeamLogo teamId={teamId} size={28} />
      ) : (
        <div className="w-7 h-7 rounded bg-bg flex items-center justify-center text-[10px] text-muted">
          {teamName === "Over" ? "↑" : teamName === "Under" ? "↓" : "—"}
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-xs text-muted truncate">{teamName}</div>
        <div className="font-semibold tabular-nums leading-tight">{primary}</div>
        {secondary && <div className="text-[10px] text-muted tabular-nums">{secondary}</div>}
      </div>
      {book && <div className="text-[9px] text-muted whitespace-nowrap">{book}</div>}
    </div>
  );
}

function NoLine() {
  return <p className="text-xs text-muted">Not yet available.</p>;
}

function Matchup({ g }: { g: GroupedGame }) {
  return (
    <div className="flex items-center gap-2 text-sm font-medium">
      {g.awayId ? <TeamLogo teamId={g.awayId} size={28} /> : null}
      {g.awayId ? (
        <Link href={`/teams/${g.awayId}`} className="hover:underline">{g.awayId}</Link>
      ) : (
        <span>{g.awayName}</span>
      )}
      <span className="text-muted text-xs">@</span>
      {g.homeId ? <TeamLogo teamId={g.homeId} size={28} /> : null}
      {g.homeId ? (
        <Link href={`/teams/${g.homeId}`} className="hover:underline">{g.homeId}</Link>
      ) : (
        <span>{g.homeName}</span>
      )}
    </div>
  );
}

function AllBooksTable({ lines }: { lines: OddsLine[] }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-muted">
          <tr>
            <th className="pr-2 py-1">Book</th>
            <th className="pr-2">Market</th>
            <th className="pr-2">Outcome</th>
            <th className="pr-2 text-right">Line</th>
            <th className="pr-2 text-right">Price</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.id} className="border-t divider">
              <td className="pr-2 py-1">{l.bookmaker}</td>
              <td className="pr-2 text-muted">{marketLabel(l.market)}</td>
              <td className="pr-2">{l.label}</td>
              <td className="pr-2 text-right tabular-nums">{l.point ?? "—"}</td>
              <td className="pr-2 text-right tabular-nums">{l.price != null ? formatAmerican(l.price) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================================
// Grouping + best-line selection
// ============================================================================

function groupByEvent(lines: OddsLine[]): GroupedGame[] {
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
  // Sort by kickoff
  return Array.from(map.values()).sort((a, b) => {
    if (!a.kickoff) return 1;
    if (!b.kickoff) return -1;
    return a.kickoff.localeCompare(b.kickoff);
  });
}

// Best spread = best price for each side, ideally at the same line. Simple
// heuristic: take the modal (most-common) line, then the best price for each
// side at that line.
function bestSpread(game: GroupedGame) {
  const spreads = game.lines.filter((l) => l.market === "spreads" && l.point != null);
  if (spreads.length === 0) return null;
  // Pick the most-common spread value (closest to consensus)
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

function bestTotal(game: GroupedGame) {
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
    book: bestO?.bookmaker === bestU?.bookmaker ? (bestO?.bookmaker ?? "") : "best",
    point: modalNum,
    overPrice: bestO?.price ?? null,
    underPrice: bestU?.price ?? null,
  };
}

function bestMoneyline(game: GroupedGame) {
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

function uniqueBooks(lines: OddsLine[]): number {
  return new Set(lines.map((l) => l.bookmaker)).size;
}

// ============================================================================
// Formatters
// ============================================================================

function formatAmerican(price: number | null | undefined): string {
  if (price == null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

function formatSpreadNum(p: number | null | undefined): string {
  if (p == null) return "—";
  return p > 0 ? `+${p}` : `${p}`;
}

function impliedProb(price: number | null | undefined): string {
  if (price == null) return "";
  const p = price > 0 ? 100 / (price + 100) : -price / (-price + 100);
  return `${(p * 100).toFixed(0)}% implied`;
}

function prettyKickoff(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function marketLabel(m: string): string {
  if (m === "h2h") return "Moneyline";
  if (m === "spreads") return "Spread";
  if (m === "totals") return "Total";
  return m;
}
