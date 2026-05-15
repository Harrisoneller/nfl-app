"use client";
import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { LiveFeed } from "@/components/LiveFeed";

export default function FantasyPage() {
  const [roster, setRoster] = useState(
    "Patrick Mahomes\nChristian McCaffrey\nJa'Marr Chase\nTravis Kelce",
  );
  const [analysisData, setAnalysisData] = useState<any>(null);
  const [aiResponse, setAiResponse] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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

  async function analyze() {
    setErr(null);
    setLoading(true);
    try {
      const names = roster.split(/\n|,/).map((s) => s.trim()).filter(Boolean);
      setAnalysisData(await api.enrichRoster(names));
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
      const names = roster.split(/\n|,/).map((s) => s.trim()).filter(Boolean);
      const r = await api.fantasyAdvise(names, question);
      setAiResponse(r.content);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setAiLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Fantasy</h1>
      <p className="text-sm text-muted">
        Your fantasy hub: live analyst content, Sleeper-wide trending players, and a roster
        analyzer with optional AI advisor.
      </p>

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

      <Card title="Roster analyzer">
        <p className="text-sm text-muted mb-2">
          Paste your roster (player names, one per line). We enrich with team, position, status,
          injury, and depth-chart context. Then ask the AI for start/sit + waiver-wire takes.
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
