"use client";
import { Fragment, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, PropBoardRow } from "@/lib/api";
import { Card } from "@/components/Card";
import { TrackBetButton } from "@/components/betting/TrackBetButton";

/**
 * Prop Finder — the slate-wide prop workbench.
 *
 * Every upcoming player prop with per-book prices, the model's probability at
 * each book's EXACT line, best price per side, one-tap bet tracking, and a
 * custom-line calculator wired to the same projection distribution.
 */

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;

// Odds API market key → our stat key (for the custom-line calculator).
const MARKET_TO_STAT: Record<string, string> = {
  player_pass_yds: "passing_yards",
  player_pass_tds: "passing_tds",
  player_pass_attempts: "attempts",
  player_pass_completions: "completions",
  player_pass_interceptions: "interceptions",
  player_rush_yds: "rushing_yards",
  player_rush_attempts: "carries",
  player_receptions: "receptions",
  player_reception_yds: "receiving_yards",
  player_anytime_td: "anytime_td",
};

function fmtAmerican(price: number | null | undefined): string {
  if (price == null) return "—";
  return price > 0 ? `+${price}` : String(price);
}

function pct(v: number | null | undefined): string {
  return v != null ? `${(v * 100).toFixed(1)}%` : "—";
}

export function PropFinderTab() {
  const [market, setMarket] = useState("");
  const [eventId, setEventId] = useState("");
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("ALL");
  const [search, setSearch] = useState("");
  const [minEdge, setMinEdge] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useSWR(
    ["prop-board", market, eventId, position],
    () =>
      api.propBoard({
        market: market || undefined,
        event_id: eventId || undefined,
        position: position === "ALL" ? undefined : position,
        limit: 400,
      }),
    { revalidateOnFocus: false },
  );

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.props || []).filter((r) => {
      if (needle && !r.player_name.toLowerCase().includes(needle)) return false;
      if (minEdge > 0 && (r.best_edge == null || r.best_edge < minEdge / 100)) return false;
      return true;
    });
  }, [data, search, minEdge]);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={market}
            onChange={(e) => setMarket(e.target.value)}
            className="bg-bg border divider rounded px-2 py-1.5 text-xs"
          >
            <option value="">All markets</option>
            {(data?.markets || []).map((m) => (
              <option key={m} value={m}>{data?.market_labels?.[m] ?? m}</option>
            ))}
          </select>
          <select
            value={eventId}
            onChange={(e) => setEventId(e.target.value)}
            className="bg-bg border divider rounded px-2 py-1.5 text-xs max-w-[190px]"
          >
            <option value="">All games</option>
            {(data?.games || []).map((g) => (
              <option key={g.event_id} value={g.event_id}>
                {g.away_team_id ?? "?"} @ {g.home_team_id ?? "?"}
              </option>
            ))}
          </select>
          <div className="flex gap-1">
            {POSITIONS.map((p) => (
              <button
                key={p}
                onClick={() => setPosition(p)}
                className={`text-xs rounded px-2.5 py-1.5 border divider ${
                  position === p ? "bg-team-primary text-white" : "bg-bg"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Player…"
            className="bg-bg border divider rounded px-3 py-1.5 text-xs w-36"
          />
          <label className="ml-auto text-[11px] text-muted flex items-center gap-1.5">
            Min edge
            <input
              type="number"
              min={0}
              max={25}
              step={1}
              value={minEdge}
              onChange={(e) => setMinEdge(Number(e.target.value))}
              className="bg-bg border divider rounded px-2 py-1 text-xs w-14"
            />
            %
          </label>
        </div>
        <p className="text-[11px] text-muted mt-2">
          Model probability is computed at each book&apos;s exact line from the same
          distribution behind the projections. Best price = the book with the
          biggest model edge on that side. Click a row for the full book-by-book
          board and a custom-line calculator. Advisory only; not betting advice.
        </p>
      </Card>

      <Card title={isLoading ? "Scanning the slate…" : `${rows.length} props (${data?.total ?? 0} on the board)`}>
        {!isLoading && rows.length === 0 && (
          <p className="text-sm text-muted">
            Nothing matches. Books post player props a few days before kickoff —
            during the season this board fills up as lines arrive.
          </p>
        )}
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted">
                <tr>
                  <th className="py-1 pr-3">Player</th>
                  <th className="pr-3">Market</th>
                  <th className="pr-3">Line</th>
                  <th className="pr-3" title="Number of books quoting this prop">Bks</th>
                  <th className="pr-3" title="De-vigged consensus P(over)">Mkt P(O)</th>
                  <th className="pr-3" title="Model P(over) at the consensus line">Model P(O)</th>
                  <th className="pr-3">Best over</th>
                  <th className="pr-3">Best under</th>
                  <th className="pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const key = `${r.event_id}:${r.market}:${r.player_name}`;
                  return (
                    <Fragment key={key}>
                      <tr
                        className="border-t divider cursor-pointer hover:bg-bg/60"
                        onClick={() => setExpanded(expanded === key ? null : key)}
                      >
                        <td className="py-1.5 pr-3">
                          {r.player_id ? (
                            <Link
                              href={`/players/${r.player_id}`}
                              className="hover:underline font-medium"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {r.player_name}
                            </Link>
                          ) : (
                            <span className="font-medium">{r.player_name}</span>
                          )}
                          <span className="text-muted ml-1.5 text-[10px]">
                            {r.away_team_id} @ {r.home_team_id}
                          </span>
                        </td>
                        <td className="pr-3">{r.market_label}</td>
                        <td className="pr-3 tabular-nums">{r.consensus_line ?? "—"}</td>
                        <td className="pr-3 tabular-nums">{r.books_count}</td>
                        <td className="pr-3 tabular-nums">{pct(r.market_over_prob)}</td>
                        <td className="pr-3 tabular-nums" title={r.model_mean != null ? `model mean ${r.model_mean} ± ${r.model_sd}` : undefined}>
                          {pct(r.model_over_prob)}
                        </td>
                        <BestCell best={r.best_over} row={r} side="over" />
                        <BestCell best={r.best_under} row={r} side="under" />
                        <td className="pr-1 text-muted">{expanded === key ? "▾" : "▸"}</td>
                      </tr>
                      {expanded === key && (
                        <tr className="border-t divider bg-bg/40">
                          <td colSpan={9} className="py-2 px-2">
                            <BookBreakdown row={r} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function BestCell({
  best,
  row,
  side,
}: {
  best: PropBoardRow["best_over"];
  row: PropBoardRow;
  side: "over" | "under";
}) {
  if (!best) return <td className="pr-3 text-muted">—</td>;
  const good = best.edge >= 0.03;
  return (
    <td className="pr-3 whitespace-nowrap">
      <span className="tabular-nums">{fmtAmerican(best.price)}</span>
      <span className="text-muted text-[10px] ml-1">{best.book}</span>
      <span
        className="ml-1 tabular-nums font-semibold"
        style={{ color: good ? "#22c55e" : best.edge >= 0 ? "#a3a3a3" : "#ef4444" }}
        title={`model edge vs ${best.book}'s implied probability`}
      >
        {(best.edge * 100).toFixed(1)}%
      </span>
      <span className="ml-1.5" onClick={(e) => e.stopPropagation()}>
        <TrackBetButton
          compact
          source="odds"
          leg={{
            market: "player_prop",
            selection: side,
            selection_label: `${row.player_name} ${row.market_label} ${side === "over" ? "O" : "U"} ${best.line ?? ""} (${best.book})`,
            line: best.line,
            odds_american: best.price,
            event_id: row.event_id,
            home_team_id: row.home_team_id,
            away_team_id: row.away_team_id,
            commence_time: row.commence_time,
            player_name: row.player_name,
            prop_market: row.market,
          }}
        />
      </span>
    </td>
  );
}

function BookBreakdown({ row }: { row: PropBoardRow }) {
  return (
    <div className="space-y-3">
      <table className="text-xs w-full max-w-2xl">
        <thead className="text-left text-muted">
          <tr>
            <th className="py-1 pr-3">Book</th>
            <th className="pr-3">Line</th>
            <th className="pr-3">Over</th>
            <th className="pr-3">Under</th>
            <th className="pr-3" title="Model P(over) at this book's line">Model P(O)</th>
            <th className="pr-3">Edge O</th>
            <th className="pr-3">Edge U</th>
          </tr>
        </thead>
        <tbody>
          {row.books.map((b, i) => (
            <tr key={i} className="border-t divider">
              <td className="py-1 pr-3">{b.book}</td>
              <td className="pr-3 tabular-nums">{b.line ?? "—"}</td>
              <td className="pr-3 tabular-nums">{fmtAmerican(b.over_price)}</td>
              <td className="pr-3 tabular-nums">{fmtAmerican(b.under_price)}</td>
              <td className="pr-3 tabular-nums">{pct(b.model_over_prob)}</td>
              <td className="pr-3 tabular-nums" style={{ color: (b.edge_over ?? 0) > 0 ? "#22c55e" : undefined }}>
                {b.edge_over != null ? `${(b.edge_over * 100).toFixed(1)}%` : "—"}
              </td>
              <td className="pr-3 tabular-nums" style={{ color: (b.edge_under ?? 0) > 0 ? "#22c55e" : undefined }}>
                {b.edge_under != null ? `${(b.edge_under * 100).toFixed(1)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {row.player_id && <LineCalculator row={row} />}
    </div>
  );
}

function LineCalculator({ row }: { row: PropBoardRow }) {
  const [line, setLine] = useState<string>(String(row.consensus_line ?? ""));
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const stat = MARKET_TO_STAT[row.market];

  if (!stat || !row.player_id) return null;

  async function calc() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api.playerOverProb(row.player_id!, stat, Number(line) || 0);
      if (r.error) setResult(r.error);
      else if (r.prob != null) setResult(`P(anytime TD) = ${(r.prob * 100).toFixed(1)}%`);
      else setResult(
        `P(over ${line}) = ${((r.over_prob ?? 0) * 100).toFixed(1)}% · P(under) = ${((r.under_prob ?? 0) * 100).toFixed(1)}%`,
      );
    } catch (e: any) {
      setResult(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted">Custom line:</span>
      <input
        value={line}
        onChange={(e) => setLine(e.target.value)}
        className="bg-bg border divider rounded px-2 py-1 w-20 tabular-nums"
      />
      <button
        onClick={calc}
        disabled={busy}
        className="bg-team-primary text-white rounded px-3 py-1 disabled:opacity-50"
      >
        {busy ? "…" : "Calc"}
      </button>
      {result && <span className="text-muted">{result}</span>}
    </div>
  );
}
