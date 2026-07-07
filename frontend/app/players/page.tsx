"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { api, LeaderboardPlayer, Player } from "@/lib/api";
import { Card } from "@/components/Card";
import { TabBar, TabPanel } from "@/components/Tabs";
import { playerMetricLabel } from "@/lib/metrics";
import { WeeklyBoardTab } from "@/components/players/WeeklyBoardTab";
import { PropFinderTab } from "@/components/players/PropFinderTab";
import { FantasyTab } from "@/components/players/FantasyTab";
import { CompareTab } from "@/components/players/CompareTab";

/**
 * Players hub — THE zone for player projections and modeling.
 *
 * Six surfaces off one projection engine: season-long distributions, the
 * weekly start/sit board, the Prop Finder workbench, the fantasy command
 * center (ROS values / waivers / trades / AI), side-by-side compare, and the
 * full directory. Deep-linkable via ?tab=.
 */

const TABS = [
  { id: "season", label: "Season projections" },
  { id: "weekly", label: "Weekly (start/sit)" },
  { id: "props", label: "Prop Finder" },
  { id: "fantasy", label: "Fantasy" },
  { id: "compare", label: "Compare" },
  { id: "directory", label: "Directory" },
];
const TAB_IDS = new Set(TABS.map((t) => t.id));

const POSITIONS = ["QB", "RB", "WR", "TE", "ALL"] as const;
const SCORING_OPTIONS = [
  { key: "ppr", label: "PPR" },
  { key: "half_ppr", label: "Half-PPR" },
  { key: "standard", label: "Standard" },
] as const;

// Stat columns per position (season totals) — the primary content of the board.
const STAT_COLS: Record<string, string[]> = {
  ALL: [],
  QB: ["passing_yards", "passing_tds", "interceptions", "attempts", "rushing_yards", "rushing_tds"],
  RB: ["rushing_yards", "rushing_tds", "carries", "receptions", "receiving_yards", "receiving_tds"],
  WR: ["receiving_yards", "receiving_tds", "receptions", "targets"],
  TE: ["receiving_yards", "receiving_tds", "receptions", "targets"],
};

// Default ranking stat per position tab.
const DEFAULT_SORT: Record<string, string> = {
  ALL: "fantasy",
  QB: "passing_yards",
  RB: "rushing_yards",
  WR: "receiving_yards",
  TE: "receiving_yards",
};

export default function PlayersPage() {
  return (
    <Suspense fallback={<div className="text-sm text-muted py-8">Loading…</div>}>
      <PlayersHub />
    </Suspense>
  );
}

function PlayersHub() {
  const router = useRouter();
  const params = useSearchParams();
  const urlTab = params.get("tab") || "season";
  const [tab, setTab] = useState<string>(TAB_IDS.has(urlTab) ? urlTab : "season");

  function changeTab(id: string) {
    setTab(id);
    router.replace(`/players?tab=${id}`, { scroll: false });
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">Players</h1>
        <span className="text-xs text-muted hidden md:inline">
          projections · start/sit · props · fantasy — one model, every number consistent
        </span>
      </div>
      <TabBar tabs={TABS} active={tab} onChange={changeTab} />

      <TabPanel active={tab} value="season">
        <ProjectionsTab />
      </TabPanel>
      <TabPanel active={tab} value="weekly">
        <WeeklyBoardTab />
      </TabPanel>
      <TabPanel active={tab} value="props">
        <PropFinderTab />
      </TabPanel>
      <TabPanel active={tab} value="fantasy">
        <FantasyTab />
      </TabPanel>
      <TabPanel active={tab} value="compare">
        <CompareTab />
      </TabPanel>
      <TabPanel active={tab} value="directory">
        <DirectoryTab />
      </TabPanel>
    </div>
  );
}

// =============================================================================
// Projections leaderboard
// =============================================================================

function ProjectionsTab() {
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("QB");
  const [scoring, setScoring] = useState<string>("ppr");
  const [sortKey, setSortKey] = useState<string>(DEFAULT_SORT.QB);
  const [filter, setFilter] = useState<string>("");

  function switchPosition(p: (typeof POSITIONS)[number]) {
    setPosition(p);
    setSortKey(DEFAULT_SORT[p]);
  }

  const { data, isLoading } = useSWR(
    ["projection-leaderboard", position],
    () =>
      api.projectionLeaderboard({
        position: position === "ALL" ? undefined : position,
        sort: position === "ALL" ? "fantasy" : DEFAULT_SORT[position],
        limit: position === "ALL" ? 200 : 120,
      }),
    { revalidateOnFocus: false },
  );

  const statCols = STAT_COLS[position] || [];
  const fantasyKey = `fantasy_${scoring}` as keyof LeaderboardPlayer;

  // Client-side re-sort (server sort only decides truncation; per-position
  // requests return the whole pool so re-sorting is lossless).
  const players = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const rows = (data?.players || []).filter(
      (p) =>
        !needle ||
        p.name.toLowerCase().includes(needle) ||
        (p.team ?? "").toLowerCase().includes(needle),
    );
    const val = (p: LeaderboardPlayer): number => {
      if (sortKey === "fantasy") {
        const f = p[fantasyKey] as LeaderboardPlayer["fantasy_ppr"] | undefined;
        return f?.mean ?? -1;
      }
      return p.stats?.[sortKey]?.mean ?? -1;
    };
    rows.sort((a, b) => val(b) - val(a));
    return rows;
  }, [data, sortKey, fantasyKey, filter]);

  // Scale the band bar against the top player's p90 for the active sort stat.
  const maxP90 = useMemo(() => {
    let m = 0;
    for (const p of players) {
      const s = sortKey === "fantasy"
        ? (p[fantasyKey] as LeaderboardPlayer["fantasy_ppr"] | undefined)
        : p.stats?.[sortKey];
      if (s?.p90 && s.p90 > m) m = s.p90;
    }
    return m || 1;
  }, [players, sortKey, fantasyKey]);

  const sortHeaderClass = (k: string) =>
    `pr-3 cursor-pointer select-none hover:text-fg ${sortKey === k ? "text-team-primary font-semibold" : ""}`;

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1">
            {POSITIONS.map((p) => (
              <button
                key={p}
                onClick={() => switchPosition(p)}
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
            placeholder="Filter name or team…"
            className="bg-bg border divider rounded px-3 py-1.5 text-xs w-44"
          />
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted">Fantasy col</span>
            <select
              value={scoring}
              onChange={(e) => setScoring(e.target.value)}
              className="bg-bg border divider rounded px-2 py-1.5 text-xs"
            >
              {SCORING_OPTIONS.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-[11px] text-muted mt-2">
          Full-season statistical projections, roster-active players only. Click
          any stat header to rank by it — hover a value for its p10–p90 range.
          Priors update weekly as games are played; each game is conditioned on
          the game model (implied points, game script, positional defense).
          {position === "ALL" && " The ALL view ranks across positions using the fantasy composite (the only cross-position comparator)."}
        </p>
        <CoverageLine coverage={data?.coverage} position={position} />
      </Card>

      <Card title={isLoading ? "Computing projections…" : `${players.length} players · ${data?.season ?? ""} season${data?.model_version ? ` · ${data.model_version}` : ""}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-2">#</th>
                <th className="pr-3">Player</th>
                {position === "ALL" && <th className="pr-3">Pos</th>}
                <th className="pr-3">Team</th>
                <th className="pr-3">Next</th>
                <th className="pr-3">Gms</th>
                {statCols.map((k) => (
                  <th
                    key={k}
                    className={sortHeaderClass(k)}
                    onClick={() => setSortKey(k)}
                    title="Click to rank by this stat"
                  >
                    {playerMetricLabel(k)}{sortKey === k ? " ↓" : ""}
                  </th>
                ))}
                <th className="pr-3 min-w-[120px]">
                  {sortKey === "fantasy" ? "Fantasy range" : `${playerMetricLabel(sortKey)} p10–p90`}
                </th>
                <th
                  className={`${sortHeaderClass("fantasy")} text-muted`}
                  onClick={() => setSortKey("fantasy")}
                  title="Fantasy composite (supplemental) — click to rank by it"
                >
                  Fty {SCORING_OPTIONS.find((s) => s.key === scoring)?.label}{sortKey === "fantasy" ? " ↓" : ""}
                </th>
              </tr>
            </thead>
            <tbody>
              {players.map((p, i) => {
                const f = p[fantasyKey] as LeaderboardPlayer["fantasy_ppr"] | undefined;
                const bandStat = sortKey === "fantasy" ? f : p.stats?.[sortKey];
                return (
                  <tr key={p.gsis_id} className="border-t divider">
                    <td className="py-1.5 pr-2 text-muted tabular-nums">{i + 1}</td>
                    <td className="pr-3">
                      {p.player_id ? (
                        <Link href={`/players/${p.player_id}`} className="hover:underline font-medium">
                          {p.name}
                        </Link>
                      ) : (
                        <span className="font-medium">{p.name}</span>
                      )}
                      {p.rookie && (
                        <span
                          className="ml-1.5 text-[9px] text-team-primary font-bold uppercase"
                          title="No NFL history yet — projected from a position/draft-capital archetype prior"
                        >
                          R
                        </span>
                      )}
                      {p.injury_status && (
                        <span className="ml-1.5 text-[9px] text-amber-500 font-bold uppercase">
                          {p.injury_status}
                        </span>
                      )}
                      {p.role && p.role.multiplier < 1 && (
                        <span
                          className="ml-1.5 text-[9px] text-muted font-bold uppercase"
                          title={`Depth chart ${p.role.depth_chart_order ?? "?"} — projected for ${Math.round(p.role.multiplier * 100)}% of a starter's role`}
                        >
                          {p.position}{p.role.depth_chart_order ?? ""}
                        </span>
                      )}
                    </td>
                    {position === "ALL" && <td className="pr-3">{p.position}</td>}
                    <td className="pr-3">{p.team ?? "—"}</td>
                    <td className="pr-3 text-muted">
                      {p.next_game
                        ? `${p.next_game.is_home ? "vs" : "@"} ${p.next_game.opponent}`
                        : "—"}
                    </td>
                    <td className="pr-3 tabular-nums text-muted">{p.games_remaining}</td>
                    {statCols.map((k) => {
                      const s = p.stats?.[k];
                      return (
                        <td
                          key={k}
                          className={`pr-3 tabular-nums ${sortKey === k ? "font-semibold" : ""}`}
                          title={s ? `p10–p90: ${s.p10}–${s.p90}` : undefined}
                        >
                          {s ? s.mean.toFixed(0) : "—"}
                        </td>
                      );
                    })}
                    <td className="pr-3">
                      {bandStat && (
                        <BandBar p10={bandStat.p10} mean={bandStat.mean} p90={bandStat.p90} max={maxP90} />
                      )}
                    </td>
                    <td className="pr-3 tabular-nums text-muted">
                      {f ? `${f.mean.toFixed(0)} (${f.per_game.toFixed(1)}/gm)` : "—"}
                    </td>
                  </tr>
                );
              })}
              {!isLoading && players.length === 0 && (
                <tr>
                  <td colSpan={9 + statCols.length} className="py-4 text-muted">
                    No projections yet — weekly data may still be syncing.
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

function CoverageLine({
  coverage,
  position,
}: {
  coverage?: Record<string, { teams: number; total_teams: number; missing: string[] }>;
  position: string;
}) {
  if (!coverage) return null;
  const entries =
    position === "ALL"
      ? Object.entries(coverage)
      : Object.entries(coverage).filter(([pos]) => pos === position);
  if (!entries.length) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5">
      {entries.map(([pos, c]) => {
        const full = c.teams >= c.total_teams;
        return (
          <span
            key={pos}
            className={`text-[10px] ${full ? "text-muted" : "text-amber-500"}`}
            title={c.missing.length ? `No projected ${pos} for: ${c.missing.join(", ")}` : undefined}
          >
            {pos}: {c.teams}/{c.total_teams} teams
            {!full && c.missing.length > 0 && ` (missing ${c.missing.join(", ")})`}
          </span>
        );
      })}
    </div>
  );
}

function BandBar({ p10, mean, p90, max }: { p10: number; mean: number; p90: number; max: number }) {
  const left = Math.max(0, (p10 / max) * 100);
  const width = Math.max(2, ((p90 - p10) / max) * 100);
  const dot = Math.max(0, (mean / max) * 100);
  return (
    <div
      className="relative h-2 rounded bg-bg border divider"
      title={`p10 ${p10.toFixed(0)} · mean ${mean.toFixed(0)} · p90 ${p90.toFixed(0)}`}
    >
      <div
        className="absolute h-full rounded bg-team-primary/30"
        style={{ left: `${left}%`, width: `${width}%` }}
      />
      <div
        className="absolute w-1 h-full rounded bg-team-primary"
        style={{ left: `calc(${dot}% - 2px)` }}
      />
    </div>
  );
}

// =============================================================================
// Directory (the original searchable list)
// =============================================================================

function DirectoryTab() {
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
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
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
