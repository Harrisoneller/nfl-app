"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";

type Mode = "teams" | "team-vs-league" | "players";

export default function ComparePage() {
  const [mode, setMode] = useState<Mode>("teams");
  const [season, setSeason] = useState(2024);
  const [teams, setTeams] = useState("PHI,SF");
  const [team, setTeam] = useState("PHI");
  const [players, setPlayers] = useState("Patrick Mahomes, Josh Allen");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setErr(null);
    setLoading(true);
    try {
      if (mode === "teams") {
        setData(await api.compareTeams(teams.split(",").map((s) => s.trim().toUpperCase()), season));
      } else if (mode === "team-vs-league") {
        setData(await api.compareTeamVsLeague(team.toUpperCase(), season));
      } else {
        setData(await api.comparePlayers(players.split(",").map((s) => s.trim()), season));
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">Compare</h1>
      <Card>
        <div className="flex flex-wrap gap-2 items-center">
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}
            className="bg-bg border divider rounded px-3 py-2 text-sm">
            <option value="teams">Teams (N)</option>
            <option value="team-vs-league">Team vs. League</option>
            <option value="players">Players (N)</option>
          </select>
          {mode === "teams" && (
            <input value={teams} onChange={(e) => setTeams(e.target.value)}
              className="bg-bg border divider rounded px-3 py-2 text-sm flex-1 min-w-[260px]"
              placeholder="PHI,SF,KC,BUF" />
          )}
          {mode === "team-vs-league" && (
            <input value={team} onChange={(e) => setTeam(e.target.value)}
              className="bg-bg border divider rounded px-3 py-2 text-sm w-32" placeholder="PHI" />
          )}
          {mode === "players" && (
            <input value={players} onChange={(e) => setPlayers(e.target.value)}
              className="bg-bg border divider rounded px-3 py-2 text-sm flex-1 min-w-[260px]"
              placeholder="Patrick Mahomes, Josh Allen" />
          )}
          <input type="number" value={season} onChange={(e) => setSeason(Number(e.target.value))}
            className="bg-bg border divider rounded px-3 py-2 text-sm w-24" />
          <button onClick={run} className="bg-team-primary text-white text-sm rounded px-4 py-2">
            Compare
          </button>
        </div>
      </Card>

      {loading && <p className="text-sm text-muted">Crunching numbers… (first run can be slow as nfl-data-py caches)</p>}
      {err && <p className="text-sm text-red-400">{err}</p>}
      {data && (
        <Card title="Result">
          <pre className="text-xs overflow-x-auto bg-bg p-3 rounded-md border divider">
            {JSON.stringify(data, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  );
}
