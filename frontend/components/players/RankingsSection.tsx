"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Published custom ranking boards — the admin's hand-built rankings, one
 * toggle per board (PPR, Superflex, Dynasty…). Independent of the projection
 * engine by design: the "vs model" column shows where each ranking diverges
 * from the model's season leaderboard, which is the whole point.
 */

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;
const TIER_COLORS = ["#22c55e", "#84cc16", "#eab308", "#f59e0b", "#f97316", "#ef4444", "#a855f7", "#a3a3a3"];

const FORMAT_LABELS: Record<string, string> = {
  ppr: "PPR",
  half_ppr: "Half PPR",
  standard: "Standard",
  superflex: "Superflex",
  two_qb: "2QB",
  dynasty: "Dynasty",
  best_ball: "Best Ball",
  custom: "Custom",
};

export function RankingsSection() {
  const sets = useSWR(["fantasy-ranking-sets"], () => api.fantasyRankingSets(), {
    revalidateOnFocus: false,
  });
  const list = sets.data?.sets ?? [];
  const [activeId, setActiveId] = useState<number | null>(null);
  const effectiveId = activeId ?? list[0]?.id ?? null;

  if (sets.isLoading) {
    return <Card><p className="text-sm text-muted">Loading rankings…</p></Card>;
  }
  if (list.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No published ranking boards yet for this season. Model-driven values
          live in the <strong>ROS values</strong> section.
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-1.5 flex-wrap">
        {list.map((s) => (
          <button
            key={s.id}
            onClick={() => setActiveId(s.id)}
            className={`text-xs rounded px-3 py-1.5 border divider ${
              effectiveId === s.id ? "bg-team-primary text-white" : "bg-bg"
            }`}
            title={s.description || undefined}
          >
            {s.name}
            <span className="ml-1.5 text-[9px] uppercase opacity-70">
              {FORMAT_LABELS[s.format] ?? s.format}
            </span>
          </button>
        ))}
      </div>
      {effectiveId != null && <RankingBoard setId={effectiveId} />}
    </div>
  );
}

function RankingBoard({ setId }: { setId: number }) {
  const { data, isLoading } = useSWR(
    ["fantasy-rankings", setId],
    () => api.fantasyRankings(setId),
    { revalidateOnFocus: false },
  );
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("ALL");
  const [tier, setTier] = useState<number | "ALL">("ALL");
  const [q, setQ] = useState("");

  const tiers = useMemo(() => {
    const t = new Set<number>((data?.players ?? []).map((p) => p.tier));
    return Array.from(t).sort((a, b) => a - b);
  }, [data]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    return (data?.players ?? []).filter(
      (p) =>
        (position === "ALL" || p.position === position) &&
        (tier === "ALL" || p.tier === tier) &&
        (!query || (p.name ?? "").toLowerCase().includes(query)),
    );
  }, [data, position, tier, q]);

  // Positional rank within this board.
  const posRank = useMemo(() => {
    const counters: Record<string, number> = {};
    const out: Record<string, number> = {};
    for (const p of data?.players ?? []) {
      const pos = p.position ?? "?";
      counters[pos] = (counters[pos] ?? 0) + 1;
      out[p.player_id] = counters[pos];
    }
    return out;
  }, [data]);

  const exportCsv = () => {
    if (!data) return;
    const head = [
      "rank", "tier", "position", "pos_rank", "name", "team", "player_id",
      "model_rank", "vs_model", "model_points", "note",
    ];
    const esc = (v: unknown) => JSON.stringify(v ?? "");
    const lines = (data.players ?? []).map((p) =>
      [
        p.rank,
        p.tier,
        p.position ?? "",
        posRank[p.player_id] ?? "",
        esc(p.name ?? ""),
        p.team ?? "",
        p.player_id,
        p.model_rank ?? "",
        p.vs_model ?? "",
        p.model_points != null ? p.model_points.toFixed(1) : "",
        esc(p.note ?? ""),
      ].join(","),
    );
    const blob = new Blob([[head.join(","), ...lines].join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    const slug = (data.name ?? "board").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    const season = String(data.season ?? "");
    const base = season && !slug.includes(season) ? `${slug}_${season}` : slug;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${base}_v${data.version}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (isLoading || !data) {
    return <Card><p className="text-sm text-muted">Loading board…</p></Card>;
  }

  return (
    <div className="space-y-3">
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
          <select
            value={String(tier)}
            onChange={(e) =>
              setTier(e.target.value === "ALL" ? "ALL" : Number(e.target.value))
            }
            className="bg-bg border divider rounded px-2 py-1.5 text-xs"
          >
            <option value="ALL">All tiers</option>
            {tiers.map((t) => (
              <option key={t} value={t}>Tier {t}</option>
            ))}
          </select>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search player…"
            className="bg-bg border divider rounded px-2 py-1.5 text-xs w-44"
          />
          <button
            onClick={exportCsv}
            className="text-xs rounded px-3 py-1.5 border divider text-muted hover:text-white"
            title="Download this board as CSV, including the model-comparison columns"
          >
            Export CSV
          </button>
          <span className="ml-auto text-[10px] text-muted">
            v{data.version}
            {data.published_at &&
              ` · updated ${new Date(data.published_at).toLocaleDateString()}`}
          </span>
        </div>
        {data.description && (
          <p className="text-[11px] text-muted mt-2">{data.description}</p>
        )}
      </Card>

      <Card title={`${rows.length} of ${data.count} players`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-2">Rank</th>
                <th className="pr-2">Pos</th>
                <th className="pr-3">Tier</th>
                <th className="pr-3">Player</th>
                <th className="pr-3">Team</th>
                <th className="pr-3" title="The projection model's season leaderboard rank">Model</th>
                <th className="pr-3" title="Model rank minus board rank. Positive = ranked ahead of the model (higher conviction); negative = ranked below the model.">vs model</th>
                <th className="pr-3" title="Model projected season fantasy points">Proj pts</th>
                <th className="pr-3">Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.player_id} className="border-t divider">
                  <td className="py-1.5 pr-2 tabular-nums font-semibold">{p.rank}</td>
                  <td className="pr-2 text-muted">
                    {p.position}
                    {posRank[p.player_id] ?? ""}
                  </td>
                  <td className="pr-3">
                    <span
                      className="text-[10px] font-bold"
                      style={{
                        color:
                          TIER_COLORS[Math.min(p.tier - 1, TIER_COLORS.length - 1)],
                      }}
                    >
                      T{p.tier}
                    </span>
                  </td>
                  <td className="pr-3">
                    <Link
                      href={`/players/${p.player_id}`}
                      className="hover:underline font-medium"
                    >
                      {p.name ?? p.player_id}
                    </Link>
                    {p.injury_status && (
                      <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">
                        {p.injury_status}
                      </span>
                    )}
                  </td>
                  <td className="pr-3">{p.team ?? "FA"}</td>
                  <td className="pr-3 tabular-nums text-muted">
                    {p.model_rank != null ? `#${p.model_rank}` : "—"}
                  </td>
                  <td className="pr-3 tabular-nums">
                    <VsModel value={p.vs_model ?? null} />
                  </td>
                  <td className="pr-3 tabular-nums text-muted">
                    {p.model_points != null ? p.model_points.toFixed(0) : "—"}
                  </td>
                  <td className="pr-3 text-muted max-w-[220px] truncate" title={p.note || undefined}>
                    {p.note || ""}
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

/** Signed divergence badge: how far this board strays from the model. */
function VsModel({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted">—</span>;
  if (Math.abs(value) < 3) return <span className="text-muted">≈</span>;
  const tone = value > 0 ? "text-emerald-400" : "text-rose-400";
  return (
    <span className={`font-medium ${tone}`}>
      {value > 0 ? "+" : ""}
      {value}
    </span>
  );
}
