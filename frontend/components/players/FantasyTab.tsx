"use client";
import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, RosPlayer, TradeResult } from "@/lib/api";
import { Card } from "@/components/Card";
import { LiveFeed } from "@/components/LiveFeed";

/**
 * Fantasy command center — ROS values (VORP), model-checked waiver targets,
 * a trade analyzer, Sleeper trending, and the AI roster advisor. All values
 * come from the same projection engine as every other number on the page.
 */

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;
const SCORING = [
  { key: "ppr", label: "PPR" },
  { key: "half_ppr", label: "Half" },
  { key: "standard", label: "Std" },
] as const;

const SECTIONS = [
  { id: "ros", label: "ROS values" },
  { id: "waivers", label: "Waiver wire" },
  { id: "trade", label: "Trade analyzer" },
  { id: "roster", label: "My roster + AI" },
  { id: "news", label: "News & trending" },
] as const;

export function FantasyTab() {
  const [section, setSection] = useState<string>("ros");
  const [scoring, setScoring] = useState("ppr");
  const [leagueSize, setLeagueSize] = useState(12);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 flex-wrap">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => setSection(s.id)}
              className={`text-xs rounded px-3 py-1.5 border divider ${
                section === s.id ? "bg-team-primary text-white" : "bg-bg"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex gap-1">
            {SCORING.map((s) => (
              <button
                key={s.key}
                onClick={() => setScoring(s.key)}
                className={`text-xs rounded px-2.5 py-1.5 border divider ${
                  scoring === s.key ? "bg-team-primary text-white" : "bg-bg"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <label className="text-[11px] text-muted flex items-center gap-1">
            Teams
            <select
              value={leagueSize}
              onChange={(e) => setLeagueSize(Number(e.target.value))}
              className="bg-bg border divider rounded px-1.5 py-1 text-xs"
            >
              {[8, 10, 12, 14, 16].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {section === "ros" && <RosSection scoring={scoring} leagueSize={leagueSize} />}
      {section === "waivers" && <WaiversSection scoring={scoring} />}
      {section === "trade" && <TradeSection scoring={scoring} leagueSize={leagueSize} />}
      {section === "roster" && <RosterSection />}
      {section === "news" && <NewsSection />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ROS values
// ---------------------------------------------------------------------------

const TIER_COLORS = ["#22c55e", "#84cc16", "#eab308", "#f59e0b", "#f97316", "#a3a3a3"];

function RosSection({ scoring, leagueSize }: { scoring: string; leagueSize: number }) {
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("ALL");
  const { data, isLoading } = useSWR(
    ["fantasy-ros", scoring, leagueSize, position],
    () =>
      api.fantasyRos({
        scoring,
        league_size: leagueSize,
        position: position === "ALL" ? undefined : position,
        limit: 200,
      }),
    { revalidateOnFocus: false },
  );

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1">
            {POSITIONS.map((p) => (
              <button
                key={p}
                onClick={() => setPosition(p)}
                className={`text-xs rounded px-3 py-1.5 border divider ${
                  position === p ? "bg-team-primary text-white" : "bg-bg"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
          {data?.replacement_levels && (
            <span className="ml-auto text-[10px] text-muted">
              Replacement PPG: {Object.entries(data.replacement_levels)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            </span>
          )}
        </div>
        <p className="text-[11px] text-muted mt-2">{data?.note}</p>
      </Card>

      <Card title={isLoading ? "Computing values…" : `${data?.count ?? 0} players · rest-of-season value`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-2">Ovr</th>
                <th className="pr-2">Pos</th>
                <th className="pr-3">Tier</th>
                <th className="pr-3">Player</th>
                <th className="pr-3">Team</th>
                <th className="pr-3" title="Projected fantasy points per game">PPG</th>
                <th className="pr-3" title="Value over replacement per game">VORP/gm</th>
                <th className="pr-3" title="Rest-of-season value over replacement">ROS VORP</th>
                <th className="pr-3" title="Projected rest-of-season points">ROS pts</th>
                <th className="pr-3" title="FantasyFootballCalculator average draft position (12-team)">ADP</th>
                <th className="pr-3" title="ADP overall rank minus our VORP rank. Positive (green) = the market drafts this player later than we rank him — model sees value.">vs ADP</th>
                <th className="pr-3">Gms</th>
                <th className="pr-3">Next</th>
              </tr>
            </thead>
            <tbody>
              {(data?.players || []).map((r) => (
                <tr key={`${r.player_id}-${r.pos_rank}`} className="border-t divider">
                  <td className="py-1.5 pr-2 text-muted tabular-nums">{r.overall_rank}</td>
                  <td className="pr-2 text-muted">{r.position}{r.pos_rank}</td>
                  <td className="pr-3">
                    <span
                      className="text-[10px] font-bold"
                      style={{ color: TIER_COLORS[Math.min(r.tier - 1, TIER_COLORS.length - 1)] }}
                    >
                      T{r.tier}
                    </span>
                  </td>
                  <td className="pr-3">
                    {r.player_id ? (
                      <Link href={`/players/${r.player_id}`} className="hover:underline font-medium">
                        {r.name}
                      </Link>
                    ) : (
                      <span className="font-medium">{r.name}</span>
                    )}
                    {r.injury_status && (
                      <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">
                        {r.injury_status}
                      </span>
                    )}
                  </td>
                  <td className="pr-3">{r.team ?? "—"}</td>
                  <td className="pr-3 tabular-nums">{r.per_game.toFixed(1)}</td>
                  <td className="pr-3 tabular-nums">{r.vorp_per_game.toFixed(1)}</td>
                  <td className="pr-3 tabular-nums font-semibold">{r.vorp_ros.toFixed(0)}</td>
                  <td className="pr-3 tabular-nums text-muted">
                    {r.ros_points.toFixed(0)}
                    <span className="text-[10px]"> ±{r.ros_sd.toFixed(0)}</span>
                  </td>
                  <td className="pr-3 tabular-nums text-muted">
                    {r.market?.adp != null ? r.market.adp.toFixed(1) : "—"}
                    {r.market?.trending_adds != null && r.market.trending_adds > 0 && (
                      <span className="ml-1 text-[9px] text-sky-300" title={`${r.market.trending_adds.toLocaleString()} Sleeper adds in 24h`}>
                        🔥
                      </span>
                    )}
                  </td>
                  <td className="pr-3 tabular-nums">
                    <ValueVsAdp value={r.market?.value_vs_adp ?? null} />
                  </td>
                  <td className="pr-3 tabular-nums text-muted">{r.games_remaining}</td>
                  <td className="pr-3 text-muted">
                    {r.next_game ? `${r.next_game.is_home ? "vs" : "@"} ${r.next_game.opponent}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

/** Signed "value vs ADP" badge: green = market drafts him later than we rank
 * him (model sees value), red = market is higher on him than we are. */
function ValueVsAdp({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted">—</span>;
  if (Math.abs(value) < 5) return <span className="text-muted">≈</span>;
  const tone = value > 0 ? "text-emerald-400" : "text-rose-400";
  return (
    <span className={`font-medium ${tone}`}>
      {value > 0 ? "+" : ""}
      {value}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Waivers
// ---------------------------------------------------------------------------

function WaiversSection({ scoring }: { scoring: string }) {
  const { data, isLoading } = useSWR(
    ["fantasy-waivers", scoring],
    () => api.fantasyWaivers({ scoring, limit: 25 }),
    { revalidateOnFocus: false },
  );

  return (
    <Card title={isLoading ? "Scoring the wire…" : `${data?.count ?? 0} waiver targets`}>
      <p className="text-[11px] text-muted mb-3">{data?.note}</p>
      {!isLoading && (data?.targets || []).length === 0 && (
        <p className="text-sm text-muted">
          No trending data yet — Sleeper refreshes every few minutes.
        </p>
      )}
      <div className="space-y-2">
        {(data?.targets || []).map((t, i) => (
          <div key={t.player_id ?? i} className="border divider rounded px-3 py-2 flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-muted tabular-nums text-xs w-5">{i + 1}</span>
            <div className="min-w-[180px]">
              {t.player_id ? (
                <Link href={`/players/${t.player_id}`} className="hover:underline font-medium text-sm">
                  {t.name}
                </Link>
              ) : (
                <span className="font-medium text-sm">{t.name}</span>
              )}
              <span className="text-muted text-xs ml-2">{t.position} · {t.team ?? "FA"}</span>
              {t.injury_status && (
                <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">{t.injury_status}</span>
              )}
            </div>
            <span className="text-xs tabular-nums" title="Projected fantasy points per game">
              {t.per_game.toFixed(1)} ppg
            </span>
            <span className="text-xs tabular-nums text-muted" title="Sleeper adds in the last 24h">
              +{t.trend_count.toLocaleString()} adds
            </span>
            {t.schedule_ease_next3 != null && (
              <span
                className="text-xs tabular-nums"
                style={{ color: t.schedule_ease_next3 >= 1.03 ? "#22c55e" : t.schedule_ease_next3 <= 0.97 ? "#ef4444" : undefined }}
                title="Avg positional-defense factor over the next 3 games (>1 = softer)"
              >
                sched {t.schedule_ease_next3.toFixed(2)}
              </span>
            )}
            <span className="ml-auto text-[10px] text-muted">{t.reasons.join(" · ")}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Trade analyzer
// ---------------------------------------------------------------------------

function TradeSection({ scoring, leagueSize }: { scoring: string; leagueSize: number }) {
  const [sideA, setSideA] = useState("Ja'Marr Chase");
  const [sideB, setSideB] = useState("Breece Hall\nDK Metcalf");
  const [result, setResult] = useState<TradeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const split = (s: string) => s.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
      setResult(await api.fantasyTrade(split(sideA), split(sideB), scoring, leagueSize));
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card title="Trade analyzer">
        <p className="text-sm text-muted mb-3">
          Enter the players each side gives up (one per line). The verdict is summed
          rest-of-season VORP with uncertainty carried through — close calls say toss-up.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-muted">Side A sends</label>
            <textarea
              value={sideA}
              onChange={(e) => setSideA(e.target.value)}
              rows={4}
              className="w-full bg-bg border divider rounded px-3 py-2 text-sm font-mono mt-1"
            />
          </div>
          <div>
            <label className="text-xs text-muted">Side B sends</label>
            <textarea
              value={sideB}
              onChange={(e) => setSideB(e.target.value)}
              rows={4}
              className="w-full bg-bg border divider rounded px-3 py-2 text-sm font-mono mt-1"
            />
          </div>
        </div>
        <button
          onClick={run}
          disabled={busy}
          className="mt-3 bg-team-primary text-white text-sm rounded px-4 py-2 disabled:opacity-50"
        >
          {busy ? "Analyzing…" : "Analyze trade"}
        </button>
        {err && <p className="text-sm text-red-400 mt-2">{err}</p>}
      </Card>

      {result && (
        <Card
          title={
            result.verdict === "toss-up"
              ? "Verdict: toss-up"
              : `Verdict: ${result.verdict === "side_a" ? "Side A" : "Side B"} gives up more value`
          }
        >
          <p className="text-sm text-muted mb-3">
            {result.detail} (Δ {result.difference_vorp} VORP, ±{result.uncertainty_sd})
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(["side_a", "side_b"] as const).map((k) => {
              const side = result[k];
              return (
                <div key={k} className="border divider rounded p-3">
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-xs uppercase tracking-wide text-muted">
                      {k === "side_a" ? "Side A" : "Side B"}
                    </span>
                    <span className="text-sm font-semibold tabular-nums">
                      {side.vorp_ros.toFixed(0)} VORP <span className="text-muted text-xs">±{side.sd.toFixed(0)}</span>
                    </span>
                  </div>
                  {side.players.map((p, i) => (
                    <div key={i} className="flex justify-between text-xs py-0.5">
                      <span>
                        {p.player_id ? (
                          <Link href={`/players/${p.player_id}`} className="hover:underline">{p.name}</Link>
                        ) : p.name}
                        <span className="text-muted ml-1">{p.position}</span>
                        {p.note && <span className="text-amber-500 ml-1" title={p.note}>*</span>}
                      </span>
                      <span className="tabular-nums">{(p.vorp_ros ?? 0).toFixed(0)}</span>
                    </div>
                  ))}
                  {side.missing.length > 0 && (
                    <p className="text-[10px] text-red-400 mt-1">Not found: {side.missing.join(", ")}</p>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-muted mt-2">{result.note}</p>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Roster analyzer + AI (ported from the old /fantasy page)
// ---------------------------------------------------------------------------

function RosterSection() {
  const [roster, setRoster] = useState(
    "Patrick Mahomes\nChristian McCaffrey\nJa'Marr Chase\nTravis Kelce",
  );
  const [analysisData, setAnalysisData] = useState<any>(null);
  const [aiResponse, setAiResponse] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const names = () => roster.split(/\n|,/).map((s) => s.trim()).filter(Boolean);

  async function analyze() {
    setErr(null);
    setLoading(true);
    try {
      setAnalysisData(await api.enrichRoster(names()));
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  async function askAi(question?: string) {
    setErr(null);
    setAiLoading(true);
    setAiResponse(null);
    try {
      const r = await api.fantasyAdvise(names(), question);
      setAiResponse(r.content);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setAiLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card title="Roster analyzer">
        <p className="text-sm text-muted mb-2">
          Paste your roster (one player per line). We enrich with team, position,
          injury and depth-chart context; the AI advisor adds start/sit and waiver takes.
        </p>
        <textarea
          value={roster}
          onChange={(e) => setRoster(e.target.value)}
          rows={6}
          className="w-full bg-bg border divider rounded px-3 py-2 text-sm font-mono"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={analyze} className="bg-team-primary text-white text-sm rounded px-4 py-2">
            Analyze roster
          </button>
          <button
            onClick={() => askAi()}
            disabled={aiLoading}
            className="bg-bg border divider text-sm rounded px-4 py-2 hover:border-team-primary disabled:opacity-50"
          >
            {aiLoading ? "Asking AI…" : "Ask AI: start/sit + waiver"}
          </button>
          <button
            onClick={() => askAi("Identify my biggest weaknesses by position and 3 trade targets to address them.")}
            disabled={aiLoading}
            className="bg-bg border divider text-sm rounded px-4 py-2 hover:border-team-primary disabled:opacity-50"
          >
            Trade targets
          </button>
        </div>
      </Card>

      {loading && <p className="text-sm text-muted">Enriching…</p>}
      {err && <p className="text-sm text-red-400">{err}</p>}

      {analysisData && (
        <Card title="Roster details">
          <table className="w-full text-sm">
            <thead className="text-left text-muted">
              <tr><th>Query</th><th>Player</th><th>Pos</th><th>Team</th><th>Status</th><th>Injury</th></tr>
            </thead>
            <tbody>
              {analysisData.rows.map((r: any, i: number) => (
                <tr key={i} className="border-t divider">
                  <td className="py-1">{r.query}</td>
                  <td>
                    {r.player_id ? (
                      <Link href={`/players/${r.player_id}`} className="hover:underline">{r.name}</Link>
                    ) : (
                      <span className="text-red-400">not found</span>
                    )}
                  </td>
                  <td>{r.position ?? ""}</td>
                  <td>{r.team ?? ""}</td>
                  <td className="text-muted">{r.status ?? ""}</td>
                  <td className="text-muted">{r.injury_status ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {aiResponse && (
        <Card title="AI advisor">
          <div className="text-sm whitespace-pre-wrap leading-relaxed">{aiResponse}</div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// News + trending
// ---------------------------------------------------------------------------

function NewsSection() {
  const { data: trendingAdds } = useSWR(
    ["fantasy-trending", "add"],
    () => api.fantasyTrending("add", 15),
    { refreshInterval: 5 * 60_000 },
  );
  const { data: trendingDrops } = useSWR(
    ["fantasy-trending", "drop"],
    () => api.fantasyTrending("drop", 15),
    { refreshInterval: 5 * 60_000 },
  );

  return (
    <div className="space-y-4">
      <LiveFeed
        title="Fantasy news & socials"
        cacheKey={["fantasy-news"]}
        fetcher={() => api.fantasyNews(40)}
        emptyText="Fantasy feeds load on the next news refresh (~5 min)."
        refreshMs={120_000}
      />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card title="Trending — adds (24h)">
          <TrendingTable rows={trendingAdds?.items || []} />
        </Card>
        <Card title="Trending — drops (24h)">
          <TrendingTable rows={trendingDrops?.items || []} />
        </Card>
      </div>
    </div>
  );
}

function TrendingTable({ rows }: { rows: any[] }) {
  if (!rows.length) {
    return <p className="text-sm text-muted">No data yet — Sleeper refreshes every few minutes.</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-muted">
        <tr>
          <th className="py-1 pr-3">Player</th>
          <th className="pr-3">Pos</th>
          <th className="pr-3">Team</th>
          <th className="pr-3">Status</th>
          <th className="pr-3 text-right">24h count</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.player_id} className="border-t divider">
            <td className="py-1 pr-3">
              {r.player_id ? (
                <Link href={`/players/${r.player_id}`} className="hover:underline">
                  {r.name ?? r.player_id}
                </Link>
              ) : (
                r.name ?? r.player_id
              )}
            </td>
            <td className="pr-3">{r.position ?? "—"}</td>
            <td className="pr-3">{r.team ?? "—"}</td>
            <td className="pr-3 text-muted">{r.injury_status ?? ""}</td>
            <td className="pr-3 text-right tabular-nums">{r.count?.toLocaleString() ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
