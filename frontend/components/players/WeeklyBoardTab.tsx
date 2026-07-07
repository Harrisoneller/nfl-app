"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, WeeklyBoardPlayer } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Weekly start/sit board — slate-wide per-game projections with tiers,
 * matchup grades, boom/bust bands, game environment and weather.
 */

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;
const SCORING = [
  { key: "ppr", label: "PPR" },
  { key: "half_ppr", label: "Half" },
  { key: "standard", label: "Std" },
] as const;

// Key per-game stat preview columns per position.
const PREVIEW_STATS: Record<string, [string, string][]> = {
  QB: [["passing_yards", "Pass yds"], ["passing_tds", "Pass TD"], ["rushing_yards", "Rush yds"]],
  RB: [["rushing_yards", "Rush yds"], ["receptions", "Rec"], ["rushing_tds", "Rush TD"]],
  WR: [["receiving_yards", "Rec yds"], ["receptions", "Rec"], ["targets", "Tgt"]],
  TE: [["receiving_yards", "Rec yds"], ["receptions", "Rec"], ["targets", "Tgt"]],
  ALL: [],
};

const TIER_STYLE: Record<string, string> = {
  "Must start": "bg-green-500/15 text-green-500 border-green-500/30",
  Start: "bg-teal-500/15 text-teal-500 border-teal-500/30",
  Flex: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  Stream: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  Sit: "text-muted border-transparent",
  Out: "bg-red-500/15 text-red-400 border-red-500/30",
  Bye: "text-muted border-transparent",
};

const GRADE_COLOR: Record<string, string> = {
  A: "#22c55e", B: "#84cc16", C: "#a1a1aa", D: "#f59e0b", F: "#ef4444",
};

export function WeeklyBoardTab() {
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("ALL");
  const [scoring, setScoring] = useState<string>("ppr");
  const [filter, setFilter] = useState("");
  const [showByes, setShowByes] = useState(false);

  const { data, isLoading } = useSWR(
    ["weekly-board", position, scoring],
    () =>
      api.weeklyBoard({
        position: position === "ALL" ? undefined : position,
        scoring,
        limit: position === "ALL" ? 400 : 200,
      }),
    { revalidateOnFocus: false },
  );

  const players = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (data?.players || []).filter((p) => {
      if (!showByes && p.bye) return false;
      if (!needle) return true;
      return (
        p.name.toLowerCase().includes(needle) ||
        (p.team ?? "").toLowerCase().includes(needle) ||
        (p.opponent ?? "").toLowerCase().includes(needle)
      );
    });
  }, [data, filter, showByes]);

  const maxMean = useMemo(
    () => Math.max(1, ...players.map((p) => p.fantasy?.[scoring]?.mean ?? 0)),
    [players, scoring],
  );

  const previewStats = PREVIEW_STATS[position] || [];

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
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter player, team, opp…"
            className="bg-bg border divider rounded px-3 py-1.5 text-xs w-44"
          />
          <label className="text-[11px] text-muted flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showByes}
              onChange={(e) => setShowByes(e.target.checked)}
            />
            Show byes
          </label>
          <div className="ml-auto flex items-center gap-1">
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
        </div>
        <p className="text-[11px] text-muted mt-2">
          Per-game projections for week {data?.week ?? "—"}, conditioned on the game
          model (implied points, game script, positional defense) plus weather and
          injury status. Floor/ceiling = p10/p90 of the same distribution the prop
          edges use. {data?.tier_note}
        </p>
      </Card>

      <Card
        title={
          isLoading
            ? "Computing the slate…"
            : `Week ${data?.week ?? "—"} · ${players.length} players · ${data?.model_version ?? ""}`
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-2">#</th>
                <th className="pr-3">Tier</th>
                <th className="pr-3">Player</th>
                {position === "ALL" && <th className="pr-3">Pos</th>}
                <th className="pr-3">Game</th>
                <th className="pr-3" title="Matchup grade vs the opponent's positional defense">Mtch</th>
                <th className="pr-3" title="Team implied points from the game model">Impl</th>
                <th className="pr-3">Script</th>
                {previewStats.map(([k, label]) => (
                  <th key={k} className="pr-3">{label}</th>
                ))}
                <th className="pr-3">Proj</th>
                <th className="pr-3">Floor</th>
                <th className="pr-3">Ceil</th>
                <th className="pr-3 min-w-[110px]">Range</th>
              </tr>
            </thead>
            <tbody>
              {players.map((p, i) => (
                <WeeklyRow
                  key={p.player_id}
                  p={p}
                  idx={i}
                  scoring={scoring}
                  maxMean={maxMean}
                  showPos={position === "ALL"}
                  previewStats={previewStats}
                />
              ))}
              {!isLoading && players.length === 0 && (
                <tr>
                  <td colSpan={12} className="py-4 text-muted">
                    No weekly projections — the season schedule may not have remaining games yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function WeeklyRow({
  p,
  idx,
  scoring,
  maxMean,
  showPos,
  previewStats,
}: {
  p: WeeklyBoardPlayer;
  idx: number;
  scoring: string;
  maxMean: number;
  showPos: boolean;
  previewStats: [string, string][];
}) {
  const f = p.fantasy?.[scoring];
  return (
    <tr className={`border-t divider ${p.bye || p.tier === "Out" ? "opacity-50" : ""}`}>
      <td className="py-1.5 pr-2 text-muted tabular-nums">{idx + 1}</td>
      <td className="pr-3">
        <span className={`text-[10px] rounded-full border px-1.5 py-0.5 whitespace-nowrap ${TIER_STYLE[p.tier] || "text-muted"}`}>
          {p.tier}
        </span>
      </td>
      <td className="pr-3">
        <Link href={`/players/${p.player_id}`} className="hover:underline font-medium">
          {p.name}
        </Link>
        <span className="text-muted ml-1.5 text-[10px]">{p.team ?? ""}</span>
        {p.rookie && <span className="ml-1 text-[9px] text-team-primary font-bold">R</span>}
        {p.injury_status && (
          <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">{p.injury_status}</span>
        )}
      </td>
      {showPos && <td className="pr-3">{p.position}</td>}
      <td className="pr-3 text-muted whitespace-nowrap">
        {p.bye ? "BYE" : `${p.is_home ? "vs" : "@"} ${p.opponent}`}
        {p.weather?.available && !p.weather.is_indoor && p.weather.summary && (
          <span className="ml-1" title={p.weather.summary}>☁</span>
        )}
      </td>
      <td className="pr-3">
        {p.matchup_grade && (
          <span
            className="font-bold"
            style={{ color: GRADE_COLOR[p.matchup_grade] }}
            title={`Positional defense factor ${p.defense_factor} (>1 = soft matchup)`}
          >
            {p.matchup_grade}
          </span>
        )}
      </td>
      <td className="pr-3 tabular-nums text-muted">
        {p.game_env ? p.game_env.team_implied_pts.toFixed(0) : "—"}
      </td>
      <td className="pr-3 text-muted text-[10px]">{p.game_env?.game_script ?? "—"}</td>
      {previewStats.map(([k]) => (
        <td key={k} className="pr-3 tabular-nums">
          {p.predicted?.[k] ? p.predicted[k].mean.toFixed(1) : "—"}
        </td>
      ))}
      <td className="pr-3 tabular-nums font-semibold">{f ? f.mean.toFixed(1) : "—"}</td>
      <td className="pr-3 tabular-nums text-muted">{f ? f.p10.toFixed(1) : "—"}</td>
      <td className="pr-3 tabular-nums text-muted">{f ? f.p90.toFixed(1) : "—"}</td>
      <td className="pr-3">
        {f && (
          <div
            className="relative h-2 rounded bg-bg border divider"
            title={`p10 ${f.p10} · mean ${f.mean} · p90 ${f.p90}`}
          >
            <div
              className="absolute h-full rounded bg-team-primary/30"
              style={{
                left: `${Math.max(0, (f.p10 / (maxMean * 1.6)) * 100)}%`,
                width: `${Math.max(2, ((f.p90 - f.p10) / (maxMean * 1.6)) * 100)}%`,
              }}
            />
            <div
              className="absolute w-1 h-full rounded bg-team-primary"
              style={{ left: `calc(${Math.max(0, (f.mean / (maxMean * 1.6)) * 100)}% - 2px)` }}
            />
          </div>
        )}
      </td>
    </tr>
  );
}
