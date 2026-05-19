"use client";
import { useEffect, useState } from "react";
import { api, Player } from "@/lib/api";
import { Card } from "@/components/Card";
import { BetaBanner, BetaPill } from "@/components/BetaBanner";
import Link from "next/link";

export default function PlayersPage() {
  const [q, setQ] = useState("");
  const [pos, setPos] = useState("");
  const [team, setTeam] = useState("");
  const [rows, setRows] = useState<Player[]>([]);
  const [loading, setLoading] = useState(false);

  async function search() {
    setLoading(true);
    try {
      const r = await api.listPlayers({
        query: q || undefined,
        position: pos || undefined,
        team_id: team || undefined,
        limit: 100,
      });
      setRows(r);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { search(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">Players</h1>
        <BetaPill />
      </div>
      <BetaBanner title="Player data is still maturing">
        Profile loads and predictions can be slow for less-popular players, and
        some season data is unavailable during the offseason. Top fantasy
        players are pre-cached and fast; everyone else loads on demand.
      </BetaBanner>
      <Card>
        <div className="flex flex-wrap gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by name…"
            className="bg-bg border divider rounded px-3 py-2 text-sm flex-1 min-w-[200px]"
          />
          <select value={pos} onChange={(e) => setPos(e.target.value)}
            className="bg-bg border divider rounded px-3 py-2 text-sm">
            <option value="">All positions</option>
            {["QB", "RB", "WR", "TE", "K", "DEF", "OL", "DL", "LB", "DB"].map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <input
            value={team}
            onChange={(e) => setTeam(e.target.value.toUpperCase())}
            placeholder="Team (e.g. PHI)"
            className="bg-bg border divider rounded px-3 py-2 text-sm w-32"
          />
          <button
            onClick={search}
            className="bg-team-primary text-white text-sm rounded px-4 py-2"
          >
            Search
          </button>
        </div>
      </Card>

      <Card title={loading ? "Loading…" : `${rows.length} players`}>
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr><th className="py-1">Name</th><th>Pos</th><th>Team</th><th>#</th><th>Status</th></tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="border-t divider">
                <td className="py-1">
                  <Link href={`/players/${p.id}`} className="hover:underline">{p.full_name}</Link>
                </td>
                <td>{p.position}</td>
                <td>{p.team_id ?? "—"}</td>
                <td>{p.jersey_number ?? "—"}</td>
                <td className="text-muted">{p.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
