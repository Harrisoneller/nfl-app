"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { api, ProjectionsBoardRow } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Holistic projections board — every projectable player in one sortable,
 * filterable table: weekly + season fantasy, ranks, key stats, usage inputs
 * (baseline vs lever), matchup context, and override flags. This is the
 * read-side view for deciding WHAT to tune; the editing lives in the other
 * tabs (Player Projections, Model Inputs, Parameters).
 *
 * Amber = something is hand-tuned on that row. Click a row to expand the
 * full stat kit + inputs + active overrides.
 */

type SortKey =
  | "season_rank"
  | "season_fantasy"
  | "season_ppg"
  | "week_fantasy"
  | "week_pos_rank"
  | "adp"
  | "defense_factor"
  | "name"
  | "team"
  | "stat"; // sorts by the selected stat column

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;

// Stat column choices (weekly means / season totals share keys).
const STAT_OPTIONS: { key: string; label: string }[] = [
  { key: "passing_yards", label: "Pass yds" },
  { key: "passing_tds", label: "Pass TD" },
  { key: "interceptions", label: "INT" },
  { key: "carries", label: "Carries" },
  { key: "rushing_yards", label: "Rush yds" },
  { key: "rushing_tds", label: "Rush TD" },
  { key: "targets", label: "Targets" },
  { key: "receptions", label: "Rec" },
  { key: "receiving_yards", label: "Rec yds" },
  { key: "receiving_tds", label: "Rec TD" },
];

const LEVER_LABELS: Record<string, string> = {
  target_share: "Tgt%",
  rush_share: "Rush%",
  yards_per_target: "Y/T",
  yards_per_carry: "Y/C",
  snap_rate: "Snap%",
};

export function ProjectionsBoardTab() {
  const [scoring, setScoring] = useState("ppr");
  const board = useSWR(["admin-proj-board", scoring], () =>
    api.adminProjectionsBoard({ scoring }),
  { revalidateOnFocus: false });

  const [pos, setPos] = useState<(typeof POSITIONS)[number]>("ALL");
  const [search, setSearch] = useState("");
  const [team, setTeam] = useState("");
  const [onlyTuned, setOnlyTuned] = useState(false);
  const [statKey, setStatKey] = useState("receiving_yards");
  const [statScope, setStatScope] = useState<"week" | "season">("week");
  const [sortKey, setSortKey] = useState<SortKey>("season_rank");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(150);

  const rows = board.data?.players || [];

  const teams = useMemo(
    () => Array.from(new Set(rows.map((r) => r.team).filter(Boolean))).sort() as string[],
    [rows],
  );

  const statOf = (r: ProjectionsBoardRow): number | null => {
    const src = statScope === "week" ? r.week.stats : r.season.stats;
    const v = src?.[statKey];
    return v == null ? null : Number(v);
  };

  const sortVal = (r: ProjectionsBoardRow): number | string => {
    switch (sortKey) {
      case "season_rank": return r.season.rank ?? 999999;
      case "season_fantasy": return -(r.season.fantasy ?? -1e9);
      case "season_ppg": return -(r.season.fantasy_per_game ?? -1e9);
      case "week_fantasy": return -(r.week.fantasy ?? -1e9);
      case "week_pos_rank": return r.week.pos_rank ?? 999999;
      case "adp": return r.week.market?.adp ?? 999999;
      case "defense_factor": return -(r.week.defense_factor ?? -1e9);
      case "name": return (r.name || "").toLowerCase();
      case "team": return r.team || "zzz";
      case "stat": return -(statOf(r) ?? -1e9);
    }
  };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let out = rows.filter((r) => {
      if (pos !== "ALL" && r.position !== pos) return false;
      if (team && r.team !== team) return false;
      if (onlyTuned && r.override_count === 0) return false;
      if (q && !(r.name || "").toLowerCase().includes(q)) return false;
      return true;
    });
    out = [...out].sort((a, b) => {
      const va = sortVal(a), vb = sortVal(b);
      const cmp = typeof va === "string" || typeof vb === "string"
        ? String(va).localeCompare(String(vb))
        : (va as number) - (vb as number);
      return cmp * sortDir;
    });
    return out;
  }, [rows, pos, team, onlyTuned, search, sortKey, sortDir, statKey, statScope]);

  const setSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(k);
      setSortDir(1);
    }
  };

  const exportCsv = () => {
    const head = [
      "name", "position", "team", "season_rank", "season_pos_rank",
      "season_fantasy", "season_ppg", "week", "opponent", "week_fantasy",
      "week_pos_rank", "tier", "matchup", "defense_factor", "adp",
      `week_${statKey}`, `season_${statKey}`, "levers", "overrides",
    ];
    const lines = filtered.map((r) => [
      JSON.stringify(r.name || ""), r.position, r.team,
      r.season.rank ?? "", r.season.pos_rank ?? "",
      r.season.fantasy ?? "", r.season.fantasy_per_game ?? "",
      r.week.week ?? "", r.week.bye ? "BYE" : r.week.opponent ?? "",
      r.week.fantasy ?? "", r.week.pos_rank ?? "", r.week.tier ?? "",
      r.week.matchup_grade ?? "", r.week.defense_factor ?? "",
      r.week.market?.adp ?? "",
      r.week.stats?.[statKey] ?? "", r.season.stats?.[statKey] ?? "",
      JSON.stringify(Object.entries(r.inputs.levers).map(([k, v]) => `${k}=${v}`).join("; ")),
      r.override_count,
    ].join(","));
    const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `projections_board_${board.data?.season}_wk${board.data?.week ?? "x"}_${scoring}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (board.error) {
    return <Card className="p-4 text-sm text-red-400">Failed to load board.</Card>;
  }
  if (!board.data) {
    return <Card className="p-4 text-sm text-muted">Building projections board…</Card>;
  }

  const tunedCount = rows.filter((r) => r.override_count > 0).length;

  const Th = ({ k, children, title }: { k: SortKey; children: React.ReactNode; title?: string }) => (
    <th
      onClick={() => setSort(k)}
      title={title}
      className={`py-1.5 px-2 text-left cursor-pointer select-none whitespace-nowrap hover:text-fg ${
        sortKey === k ? "text-fg" : "text-muted"
      }`}
    >
      {children}
      {sortKey === k && <span className="ml-0.5">{sortDir === 1 ? "▲" : "▼"}</span>}
    </th>
  );

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <div className="flex gap-1">
          {POSITIONS.map((p) => (
            <button
              key={p}
              onClick={() => setPos(p)}
              className={`px-2.5 py-1 rounded text-xs border ${
                pos === p ? "bg-white/10 border-white/30 text-fg" : "divider text-muted hover:text-fg"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
        <select
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          className="bg-bg border divider rounded px-2 py-1 text-xs"
        >
          <option value="">All teams</option>
          {teams.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          value={scoring}
          onChange={(e) => setScoring(e.target.value)}
          className="bg-bg border divider rounded px-2 py-1 text-xs"
        >
          <option value="ppr">PPR</option>
          <option value="half_ppr">Half PPR</option>
          <option value="standard">Standard</option>
        </select>
        <span className="flex items-center gap-1 text-xs text-muted">
          Stat:
          <select
            value={statKey}
            onChange={(e) => setStatKey(e.target.value)}
            className="bg-bg border divider rounded px-2 py-1 text-xs"
          >
            {STAT_OPTIONS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <select
            value={statScope}
            onChange={(e) => setStatScope(e.target.value as "week" | "season")}
            className="bg-bg border divider rounded px-2 py-1 text-xs"
          >
            <option value="week">week</option>
            <option value="season">season</option>
          </select>
        </span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search player…"
          className="bg-bg border divider rounded px-2 py-1 text-xs w-44"
        />
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input type="checkbox" checked={onlyTuned} onChange={(e) => setOnlyTuned(e.target.checked)} />
          Tuned only ({tunedCount})
        </label>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted">
          {filtered.length} of {rows.length} players · wk {board.data.week ?? "—"} · baselines {board.data.baseline_season}
          <button
            onClick={exportCsv}
            className="px-2 py-1 rounded border divider hover:text-fg"
          >
            Export CSV
          </button>
        </span>
      </div>

      {/* Table */}
      <Card className="p-0 overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-bg border-b border-white/10">
            <tr>
              <Th k="season_rank" title="Season overall rank (market-aware composite)">Rk</Th>
              <Th k="name">Player</Th>
              <Th k="team">Team</Th>
              <Th k="season_fantasy" title="Season projected fantasy points">Szn Fpts</Th>
              <Th k="season_ppg" title="Season fantasy per game">PPG</Th>
              <Th k="week_fantasy" title="This week's projected fantasy points">Wk Fpts</Th>
              <Th k="week_pos_rank" title="This week's positional rank / tier">Wk Rk</Th>
              <Th k="stat" title="Selected stat column">
                {STAT_OPTIONS.find((s) => s.key === statKey)?.label} ({statScope})
              </Th>
              <Th k="defense_factor" title="Matchup: opponent defense factor (>1 = soft)">Matchup</Th>
              <Th k="adp" title="Market ADP">ADP</Th>
              <th className="py-1.5 px-2 text-left text-muted whitespace-nowrap" title="Usage inputs: baseline → lever">
                Inputs
              </th>
              <th className="py-1.5 px-2 text-left text-muted">Tuned</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {filtered.slice(0, limit).map((r) => {
              const tuned = r.override_count > 0;
              const isOpen = expanded === r.player_id;
              const stat = statOf(r);
              return (
                <FragmentRow
                  key={r.player_id}
                  r={r}
                  tuned={tuned}
                  isOpen={isOpen}
                  stat={stat}
                  onToggle={() => setExpanded(isOpen ? null : r.player_id)}
                />
              );
            })}
          </tbody>
        </table>
        {filtered.length > limit && (
          <div className="p-2 text-center">
            <button
              onClick={() => setLimit(limit + 150)}
              className="text-xs px-3 py-1.5 rounded border divider text-muted hover:text-fg"
            >
              Show more ({filtered.length - limit} remaining)
            </button>
          </div>
        )}
      </Card>
      <p className="text-[10px] text-muted/70">
        Rows highlighted amber have hand-tuned values (stat overrides, rank pins, or input
        levers). Numbers shown are exactly what the site serves — overrides and levers already
        applied. Edit from the Player Projections / Model Inputs tabs; tune globals in Parameters.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

function FragmentRow({
  r,
  tuned,
  isOpen,
  stat,
  onToggle,
}: {
  r: ProjectionsBoardRow;
  tuned: boolean;
  isOpen: boolean;
  stat: number | null;
  onToggle: () => void;
}) {
  const fmt = (v: number | null | undefined, nd = 1) => (v == null ? "—" : v.toFixed(nd));
  const levers = Object.entries(r.inputs.levers);
  const bl = r.inputs.baselines || {};

  return (
    <>
      <tr
        onClick={onToggle}
        className={`border-t border-white/5 cursor-pointer hover:bg-white/[0.04] ${
          tuned ? "bg-amber-500/[0.06]" : ""
        }`}
      >
        <td className="py-1.5 px-2 text-muted">{r.season.rank ?? "—"}</td>
        <td className="py-1.5 px-2 whitespace-nowrap">
          <span className={tuned ? "text-amber-200" : ""}>{r.name}</span>{" "}
          <span className="text-muted">({r.position})</span>
          {r.injury_status && (
            <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-red-500/15 text-red-300 border border-red-500/30">
              {r.injury_status}
            </span>
          )}
          {r.rookie && (
            <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30">
              R
            </span>
          )}
        </td>
        <td className="py-1.5 px-2">{r.team ?? "—"}</td>
        <td className="py-1.5 px-2">
          {fmt(r.season.fantasy)}{" "}
          {r.season.pos_rank != null && (
            <span className="text-muted text-[10px]">{r.position}{r.season.pos_rank}</span>
          )}
        </td>
        <td className="py-1.5 px-2">{fmt(r.season.fantasy_per_game, 2)}</td>
        <td className="py-1.5 px-2">
          {r.week.bye ? <span className="text-muted">BYE</span> : fmt(r.week.fantasy)}
          {r.week.fantasy_p10 != null && !r.week.bye && (
            <span className="text-muted text-[10px]"> [{fmt(r.week.fantasy_p10, 0)}–{fmt(r.week.fantasy_p90, 0)}]</span>
          )}
        </td>
        <td className="py-1.5 px-2">
          {r.week.pos_rank ?? "—"}
          {r.week.tier && <span className="text-muted text-[10px]"> {r.week.tier}</span>}
        </td>
        <td className="py-1.5 px-2">{fmt(stat)}</td>
        <td className="py-1.5 px-2 whitespace-nowrap">
          {r.week.bye ? "—" : (
            <>
              {r.week.is_home ? "vs" : "@"} {r.week.opponent ?? "—"}{" "}
              <span className={
                (r.week.defense_factor ?? 1) > 1.02 ? "text-emerald-300"
                : (r.week.defense_factor ?? 1) < 0.98 ? "text-red-300" : "text-muted"
              }>
                {r.week.matchup_grade ?? ""} {fmt(r.week.defense_factor, 2)}
              </span>
            </>
          )}
        </td>
        <td className="py-1.5 px-2">{fmt(r.week.market?.adp, 1)}</td>
        <td className="py-1.5 px-2 whitespace-nowrap">
          {levers.length === 0 ? (
            <span className="text-muted">—</span>
          ) : (
            levers.map(([k, v]) => (
              <span
                key={k}
                title={`${k}: baseline ${bl[k] ?? "?"} → lever ${v}`}
                className="mr-1 text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30"
              >
                {LEVER_LABELS[k] || k} {bl[k] != null ? `${bl[k]}→` : ""}{v}
              </span>
            ))
          )}
        </td>
        <td className="py-1.5 px-2">
          {tuned ? (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
              {r.override_count}
            </span>
          ) : (
            <span className="text-muted">—</span>
          )}
        </td>
      </tr>
      {isOpen && (
        <tr className="border-t border-white/5 bg-white/[0.02]">
          <td colSpan={12} className="px-4 py-3">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[11px]">
              <div>
                <div className="text-muted font-semibold uppercase tracking-wide mb-1">
                  Week stats (means)
                </div>
                {Object.entries(r.week.stats || {}).length === 0 ? (
                  <span className="text-muted">no weekly projection</span>
                ) : (
                  Object.entries(r.week.stats || {}).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-4 tabular-nums">
                      <span className="text-muted">{k.replace(/_/g, " ")}</span>
                      <span>{v == null ? "—" : Number(v).toFixed(1)}</span>
                    </div>
                  ))
                )}
              </div>
              <div>
                <div className="text-muted font-semibold uppercase tracking-wide mb-1">
                  Season totals
                </div>
                {Object.entries(r.season.stats || {}).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-4 tabular-nums">
                    <span className="text-muted">{k.replace(/_/g, " ")}</span>
                    <span>{v == null ? "—" : Number(v).toFixed(0)}</span>
                  </div>
                ))}
                <div className="flex justify-between gap-4 mt-1 pt-1 border-t border-white/5 tabular-nums">
                  <span className="text-muted">availability</span>
                  <span>{r.season.availability ?? "—"}</span>
                </div>
                <div className="flex justify-between gap-4 tabular-nums">
                  <span className="text-muted">role multiplier</span>
                  <span>{r.season.role_multiplier ?? "—"}</span>
                </div>
              </div>
              <div>
                <div className="text-muted font-semibold uppercase tracking-wide mb-1">
                  Usage inputs (baseline {`→`} lever)
                </div>
                {Object.entries(LEVER_LABELS).map(([k, label]) => (
                  <div key={k} className="flex justify-between gap-4 tabular-nums">
                    <span className="text-muted">{label}</span>
                    <span>
                      {bl[k] ?? "—"}
                      {r.inputs.levers[k] != null && (
                        <span className="text-amber-300"> → {r.inputs.levers[k]}</span>
                      )}
                    </span>
                  </div>
                ))}
                {r.overrides.length > 0 && (
                  <>
                    <div className="text-muted font-semibold uppercase tracking-wide mt-2 mb-1">
                      Active overrides
                    </div>
                    {r.overrides.map((o) => (
                      <div key={o.id} className="flex justify-between gap-4 tabular-nums">
                        <span className="text-muted">
                          {o.field}{o.week != null ? ` (wk ${o.week})` : ""}
                        </span>
                        <span className="text-amber-300">
                          {o.original_value != null ? `${o.original_value} → ` : ""}{o.value}
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
