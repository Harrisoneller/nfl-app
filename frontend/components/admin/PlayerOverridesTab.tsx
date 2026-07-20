"use client";
import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { api, AdminOverride, WeeklyBoardPlayer } from "@/lib/api";
import { SeasonSelect } from "@/components/SeasonSelect";
import { TeamLogo } from "@/components/TeamLogo";
import { InlineEditCell } from "./InlineEditCell";
import { findOverride } from "./OverrideField";

/**
 * Player projection output pins — full-board table.
 *
 * Every projectable player in one filterable/sortable table so you can tune a
 * number while seeing surrounding teammates / position mates. Overrides
 * recenter the weekly distribution (stats → fantasy means → prop probs).
 * Filter by position to expose that pos's full stat kit as editable columns.
 */

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;

const STATS_BY_POS: Record<string, { key: string; label: string; step: number }[]> = {
  QB: [
    { key: "attempts", label: "Att", step: 1 },
    { key: "completions", label: "Cmp", step: 1 },
    { key: "passing_yards", label: "PassYd", step: 1 },
    { key: "passing_tds", label: "PassTD", step: 0.1 },
    { key: "interceptions", label: "INT", step: 0.1 },
    { key: "carries", label: "Car", step: 0.5 },
    { key: "rushing_yards", label: "RuYd", step: 1 },
    { key: "rushing_tds", label: "RuTD", step: 0.1 },
  ],
  RB: [
    { key: "carries", label: "Car", step: 0.5 },
    { key: "rushing_yards", label: "RuYd", step: 1 },
    { key: "rushing_tds", label: "RuTD", step: 0.1 },
    { key: "targets", label: "Tgt", step: 0.5 },
    { key: "receptions", label: "Rec", step: 0.5 },
    { key: "receiving_yards", label: "ReYd", step: 1 },
    { key: "receiving_tds", label: "ReTD", step: 0.1 },
  ],
  WR: [
    { key: "targets", label: "Tgt", step: 0.5 },
    { key: "receptions", label: "Rec", step: 0.5 },
    { key: "receiving_yards", label: "ReYd", step: 1 },
    { key: "receiving_tds", label: "ReTD", step: 0.1 },
  ],
  TE: [
    { key: "targets", label: "Tgt", step: 0.5 },
    { key: "receptions", label: "Rec", step: 0.5 },
    { key: "receiving_yards", label: "ReYd", step: 1 },
    { key: "receiving_tds", label: "ReTD", step: 0.1 },
  ],
};

// When viewing ALL, show a compact cross-position headline kit.
const ALL_STATS = [
  { key: "passing_yards", label: "PassYd", step: 1 },
  { key: "rushing_yards", label: "RuYd", step: 1 },
  { key: "receiving_yards", label: "ReYd", step: 1 },
  { key: "receptions", label: "Rec", step: 0.5 },
];

const SCORING = [
  { key: "ppr", label: "PPR" },
  { key: "half_ppr", label: "Half" },
  { key: "standard", label: "Std" },
] as const;

type SortKey =
  | "name"
  | "team"
  | "pos"
  | "pos_rank"
  | "fantasy"
  | "matchup"
  | "stat";

export function PlayerOverridesTab() {
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [week, setWeek] = useState<number | undefined>(undefined);
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>("ALL");
  const [team, setTeam] = useState("");
  const [search, setSearch] = useState("");
  const [onlyTuned, setOnlyTuned] = useState(false);
  const [scoring, setScoring] = useState<(typeof SCORING)[number]["key"]>("ppr");
  const [sortKey, setSortKey] = useState<SortKey>("pos_rank");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [limit, setLimit] = useState(120);

  const board = useSWR(["admin-weekly-board", season, week], () =>
    api.weeklyBoard({ season, week, limit: 600 }),
  );
  const effSeason = board.data?.season ?? null;
  const effWeek = board.data?.week ?? null;

  const ovs = useSWR(
    effSeason != null && effWeek != null
      ? ["admin-ovs-player", effSeason, effWeek]
      : null,
    () =>
      api.adminListOverrides({
        entity_type: "player",
        season: effSeason!,
        week: effWeek!,
      }),
  );

  const players = board.data?.players ?? [];
  const teams = useMemo(
    () =>
      Array.from(new Set(players.map((p) => p.team).filter(Boolean) as string[])).sort(),
    [players],
  );

  const overriddenIds = useMemo(() => {
    const ids = new Set<string>();
    for (const o of ovs.data?.overrides ?? []) ids.add(o.entity_id);
    return ids;
  }, [ovs.data]);

  const ovIndex = useMemo(() => {
    const m = new Map<string, AdminOverride>();
    for (const o of ovs.data?.overrides ?? []) {
      m.set(`${o.entity_id}::${o.field}`, o);
    }
    return m;
  }, [ovs.data]);

  const findOv = (playerId: string, field: string) =>
    ovIndex.get(`${playerId}::${field}`) ??
    findOverride(
      ovs.data?.overrides,
      "player",
      playerId,
      field,
      effSeason,
      effWeek,
    );

  const statCols = pos === "ALL" ? ALL_STATS : STATS_BY_POS[pos] || ALL_STATS;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = players.filter((p) => !p.bye);
    if (pos !== "ALL") rows = rows.filter((p) => p.position === pos);
    if (team) rows = rows.filter((p) => p.team === team);
    if (q) {
      rows = rows.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.team || "").toLowerCase().includes(q) ||
          (p.opponent || "").toLowerCase().includes(q),
      );
    }
    if (onlyTuned) rows = rows.filter((p) => overriddenIds.has(p.player_id));

    const dir = sortDir;
    rows = [...rows].sort((a, b) => {
      const av = sortVal(a, sortKey, scoring);
      const bv = sortVal(b, sortKey, scoring);
      if (typeof av === "string" && typeof bv === "string") {
        return dir * av.localeCompare(bv);
      }
      return dir * (Number(av) - Number(bv));
    });
    return rows;
  }, [players, pos, team, search, onlyTuned, overriddenIds, sortKey, sortDir, scoring]);

  const shown = filtered.slice(0, limit);

  const refresh = () => {
    void ovs.mutate();
    void board.mutate();
  };

  const save =
    (playerId: string, field: string) =>
    async (value: number, originalValue: number | null) => {
      await api.adminUpsertOverride({
        entity_type: "player",
        entity_id: playerId,
        field,
        value,
        season: effSeason,
        week: effWeek,
        original_value: originalValue,
      });
      refresh();
    };

  const revert = async (id: number) => {
    await api.adminDeleteOverride(id);
    refresh();
  };

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(k);
      setSortDir(k === "name" || k === "team" || k === "pos" ? 1 : 1);
    }
  };

  const sortMark = (k: SortKey) =>
    sortKey === k ? (sortDir === 1 ? " ↑" : " ↓") : "";

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-muted max-w-3xl">
        Weekly output pins for every projectable player. Filter by position to
        expose that group&apos;s full stat kit and tune while seeing surrounding
        teammates / rank neighbors. Stat overrides recenter the distribution
        (fantasy means and prop probs recompute); direct fantasy overrides win
        last. Amber = hand-tuned.
      </p>

      <div className="flex items-center gap-3 flex-wrap">
        <SeasonSelect value={season} onChange={setSeason} />
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Week</span>
          <select
            value={week ?? ""}
            onChange={(e) =>
              setWeek(e.target.value ? Number(e.target.value) : undefined)
            }
            className="bg-bg border divider rounded px-2 py-1.5 text-sm"
          >
            <option value="">Next</option>
            {Array.from({ length: 18 }, (_, i) => i + 1).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
        {effWeek != null && (
          <span className="text-xs text-muted">
            Editing {effSeason} · Week {effWeek}
          </span>
        )}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name / team / opp…"
          className="bg-bg border divider rounded px-2 py-1.5 text-sm w-48"
        />
        <div className="flex gap-1">
          {POSITIONS.map((p) => (
            <button
              key={p}
              onClick={() => setPos(p)}
              className={`px-2 py-1 rounded text-xs border ${
                pos === p
                  ? "bg-white/10 border-white/20 font-semibold"
                  : "divider text-muted hover:text-white"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <select
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          className="bg-bg border divider rounded px-2 py-1.5 text-sm"
        >
          <option value="">All teams</option>
          {teams.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          value={scoring}
          onChange={(e) =>
            setScoring(e.target.value as (typeof SCORING)[number]["key"])
          }
          className="bg-bg border divider rounded px-2 py-1.5 text-sm"
        >
          {SCORING.map((s) => (
            <option key={s.key} value={s.key}>
              Sort/show {s.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={onlyTuned}
            onChange={(e) => setOnlyTuned(e.target.checked)}
          />
          Tuned only
        </label>
        <span className="text-xs text-muted ml-auto">
          {shown.length}
          {filtered.length > limit ? ` / ${filtered.length}` : ""} players
          {overriddenIds.size > 0 && (
            <span className="text-amber-300"> · {overriddenIds.size} tuned</span>
          )}
        </span>
      </div>

      {board.isLoading && (
        <div className="panel p-6 text-sm text-muted">Loading projection board…</div>
      )}
      {board.error && (
        <div className="panel p-4 text-sm text-red-400">Failed to load board.</div>
      )}

      {!board.isLoading && !board.error && (
        <div className="panel overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted sticky top-0 bg-bg z-[1]">
              <tr className="border-b divider">
                <Th onClick={() => toggleSort("name")}>Player{sortMark("name")}</Th>
                <Th onClick={() => toggleSort("pos")}>Pos{sortMark("pos")}</Th>
                <Th onClick={() => toggleSort("team")}>Team{sortMark("team")}</Th>
                <th className="py-2 pr-3 font-medium">Opp</th>
                <Th onClick={() => toggleSort("matchup")}>Grd{sortMark("matchup")}</Th>
                <Th onClick={() => toggleSort("pos_rank")}>#Pos{sortMark("pos_rank")}</Th>
                <Th onClick={() => toggleSort("fantasy")}>
                  {scoring.toUpperCase().replace("_", " ")}
                  {sortMark("fantasy")}
                </Th>
                {statCols.map((s) => (
                  <th key={s.key} className="py-2 pr-3 font-medium whitespace-nowrap">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => {
                const tuned = overriddenIds.has(p.player_id);
                const fMean = p.fantasy?.[scoring]?.mean;
                return (
                  <tr
                    key={p.player_id}
                    className={`border-t divider hover:bg-white/[0.02] ${
                      tuned ? "bg-amber-500/[0.04]" : ""
                    }`}
                  >
                    <td className="py-1.5 pr-3 whitespace-nowrap">
                      <span className="font-medium">{p.name}</span>
                      {p.injury_status && (
                        <span className="ml-1 text-[10px] text-red-300">
                          {p.injury_status}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-muted">{p.position}</td>
                    <td className="py-1.5 pr-3">
                      <span className="inline-flex items-center gap-1">
                        {p.team && <TeamLogo teamId={p.team} size={14} />}
                        {p.team ?? "FA"}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap">
                      {p.is_home ? "vs" : "@"} {p.opponent ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums">
                      <span
                        className={
                          p.matchup_grade === "A" || p.matchup_grade === "B"
                            ? "text-emerald-300"
                            : p.matchup_grade === "D" || p.matchup_grade === "F"
                              ? "text-red-300"
                              : "text-muted"
                        }
                      >
                        {p.matchup_grade ?? "—"}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      <InlineEditCell
                        value={p.pos_rank}
                        override={findOv(p.player_id, "pos_rank")}
                        step={1}
                        widthClass="w-12"
                        onSave={save(p.player_id, "pos_rank")}
                        onRevert={revert}
                      />
                    </td>
                    <td className="py-1.5 pr-3">
                      <InlineEditCell
                        value={fMean}
                        override={findOv(p.player_id, `fantasy_points_${scoring}`)}
                        step={0.5}
                        widthClass="w-14"
                        onSave={save(p.player_id, `fantasy_points_${scoring}`)}
                        onRevert={revert}
                      />
                    </td>
                    {statCols.map((s) => {
                      const mean = p.predicted?.[s.key]?.mean;
                      // Hide empty cells for wrong-position stats on ALL view.
                      if (mean == null && pos === "ALL") {
                        return (
                          <td key={s.key} className="py-1.5 pr-3 text-muted/40">
                            —
                          </td>
                        );
                      }
                      return (
                        <td key={s.key} className="py-1.5 pr-3">
                          <InlineEditCell
                            value={mean}
                            override={findOv(p.player_id, s.key)}
                            step={s.step}
                            widthClass="w-14"
                            onSave={save(p.player_id, s.key)}
                            onRevert={revert}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              {!shown.length && (
                <tr>
                  <td colSpan={7 + statCols.length} className="py-8 text-center text-muted">
                    No players match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {filtered.length > limit && (
        <button
          onClick={() => setLimit((n) => n + 100)}
          className="text-xs text-sky-300 hover:text-sky-200"
        >
          Show more ({filtered.length - limit} remaining)
        </button>
      )}
    </div>
  );
}

function Th({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <th
      onClick={onClick}
      className={`py-2 pr-3 font-medium whitespace-nowrap ${
        onClick ? "cursor-pointer hover:text-fg select-none" : ""
      }`}
    >
      {children}
    </th>
  );
}

function sortVal(
  p: WeeklyBoardPlayer,
  key: SortKey,
  scoring: string,
): number | string {
  switch (key) {
    case "name":
      return (p.name || "").toLowerCase();
    case "team":
      return p.team || "zzz";
    case "pos":
      return p.position || "zzz";
    case "pos_rank":
      return p.pos_rank ?? 9999;
    case "fantasy":
      return -(p.fantasy?.[scoring as "ppr"]?.mean ?? -1e9);
    case "matchup":
      return p.matchup_grade || "Z";
    default:
      return 0;
  }
}
