"use client";
import { Fragment, useMemo, useState } from "react";
import useSWR from "swr";
import { api, AdminOverride, ProjectionsBoardRow } from "@/lib/api";
import { Card } from "@/components/Card";
import { TeamLogo } from "@/components/TeamLogo";
import { InlineEditCell } from "./InlineEditCell";

/**
 * Model-input levers — adjust what the model BELIEVES, not what it outputs.
 *
 * Team levers (pace, yards/play, pass rate, PPG, defense) feed the scoring
 * model. Player levers (usage shares, efficiency, snap rate, availability)
 * are a full-board table so you can tune a role while seeing surrounding
 * teammates and position mates.
 */

const TEAM_OFFENSE_FIELDS: { key: string; label: string; step: number; pct?: boolean }[] = [
  { key: "pace", label: "Plays/gm", step: 0.5 },
  { key: "yards_per_play", label: "Yds/play", step: 0.1 },
  { key: "pass_rate", label: "Pass rate", step: 0.01, pct: true },
  { key: "points_per_game", label: "PPG", step: 0.5 },
];

const TEAM_DEFENSE_FIELDS: { key: string; label: string; step: number; pct?: boolean }[] = [
  { key: "points_allowed_per_game", label: "Pts allowed", step: 0.5 },
  { key: "def_yards_per_play", label: "Def Y/P", step: 0.1 },
];

const TEAM_FIELDS = [...TEAM_OFFENSE_FIELDS, ...TEAM_DEFENSE_FIELDS];

const PLAYER_FIELDS: {
  key: string;
  label: string;
  step: number;
  pct?: boolean;
  positions?: string[]; // if set, only meaningful for these positions
}[] = [
  { key: "snap_rate", label: "Snap%", step: 0.01, pct: true },
  { key: "target_share", label: "Tgt%", step: 0.01, pct: true, positions: ["WR", "TE", "RB"] },
  { key: "rush_share", label: "Rush%", step: 0.01, pct: true, positions: ["RB", "QB"] },
  { key: "yards_per_target", label: "Y/T", step: 0.1, positions: ["WR", "TE", "RB"] },
  { key: "yards_per_carry", label: "Y/C", step: 0.1, positions: ["RB", "QB"] },
  { key: "availability", label: "Avail%", step: 0.01, pct: true },
];

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;

type PlayerSort =
  | "name"
  | "team"
  | "pos"
  | "season_rank"
  | "week_fantasy"
  | "snap"
  | "tgt"
  | "rush";

export function ModelInputsTab() {
  return (
    <div className="space-y-6">
      <TeamInputsSection />
      <PlayerInputsSection />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Team levers
// ---------------------------------------------------------------------------

function TeamInputsSection() {
  const inputs = useSWR(["admin-team-inputs"], () => api.adminTeamModelInputs(), {
    revalidateOnFocus: false,
  });
  const season = inputs.data?.season;
  const ovRows = useSWR(
    season ? ["admin-team-input-rows", season] : null,
    () => api.adminListOverrides({ entity_type: "team", season }),
    { revalidateOnFocus: false },
  );

  const rowFor = (teamId: string, field: string): AdminOverride | undefined =>
    (ovRows.data?.overrides || []).find(
      (o) => o.entity_id === teamId && o.field === field && o.week == null,
    );

  const refresh = () => {
    void inputs.mutate();
    void ovRows.mutate();
  };

  const save = async (
    teamId: string,
    field: string,
    value: number,
    baseline: number | null,
  ) => {
    await api.adminUpsertOverride({
      entity_type: "team",
      entity_id: teamId,
      field,
      value,
      season: season ?? null,
      original_value: rowFor(teamId, field)?.original_value ?? baseline,
      note: "model input lever",
    });
    refresh();
  };

  const revert = async (id: number) => {
    await api.adminDeleteOverride(id);
    refresh();
  };

  return (
    <Card
      title={`Team offense / defense levers${season ? ` · ${season}` : ""}${
        inputs.data && inputs.data.baseline_season !== inputs.data.season
          ? ` (baselines from ${inputs.data.baseline_season})`
          : ""
      }`}
    >
      <p className="text-[11px] text-muted mb-3">
        <strong>Offense:</strong> Pace and yards/play multiply expected scoring.
        PPG is a direct level-set that supersedes both. Pass rate re-tilts pass
        vs rush volume for every player on the roster.{" "}
        <strong>Defense:</strong> Pts allowed is a direct level-set; def Y/P
        scales points allowed. Blank input = leave at baseline. Amber = tuned.
      </p>
      {inputs.isLoading && <p className="text-sm text-muted">Loading baselines…</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Team</th>
              {TEAM_FIELDS.map((f) => (
                <th key={f.key} className="pr-4">
                  {f.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(inputs.data?.teams || []).map((t) => (
              <tr key={t.team_id} className="border-t divider">
                <td className="py-1 pr-3">
                  <span className="inline-flex items-center gap-1.5 font-medium">
                    <TeamLogo teamId={t.team_id} size={18} />
                    {t.team_id}
                  </span>
                </td>
                {TEAM_FIELDS.map((f) => {
                  const baseline = t.baselines[f.key] ?? null;
                  const ov = t.overrides[f.key] ?? null;
                  return (
                    <td key={f.key} className="pr-4">
                      <InlineEditCell
                        value={ov ?? baseline}
                        baseline={baseline}
                        override={rowFor(t.team_id, f.key)}
                        step={f.step}
                        pct={f.pct}
                        disabled={baseline == null}
                        onSave={(v) => save(t.team_id, f.key, v, baseline)}
                        onRevert={revert}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Player levers — full-board table
// ---------------------------------------------------------------------------

function PlayerInputsSection() {
  const [pos, setPos] = useState<(typeof POSITIONS)[number]>("ALL");
  const [team, setTeam] = useState("");
  const [search, setSearch] = useState("");
  const [onlyTuned, setOnlyTuned] = useState(false);
  const [groupByTeam, setGroupByTeam] = useState(true);
  const [sortKey, setSortKey] = useState<PlayerSort>("season_rank");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [limit, setLimit] = useState(150);

  // Holistic board already carries baselines + lever values for every player.
  const board = useSWR(
    ["admin-player-inputs-board"],
    () => api.adminProjectionsBoard({ scoring: "ppr" }),
    { revalidateOnFocus: false },
  );
  const season = board.data?.season;

  // Full override rows (need ids for revert).
  const ovRows = useSWR(
    season != null ? ["admin-player-input-rows-all", season] : null,
    () => api.adminListOverrides({ entity_type: "player", season }),
    { revalidateOnFocus: false },
  );

  const ovIndex = useMemo(() => {
    const m = new Map<string, AdminOverride>();
    for (const o of ovRows.data?.overrides || []) {
      if (o.week != null) continue; // only season-scoped input levers
      if (!PLAYER_FIELDS.some((f) => f.key === o.field)) continue;
      m.set(`${o.entity_id}::${o.field}`, o);
    }
    return m;
  }, [ovRows.data]);

  const rowFor = (playerId: string, field: string) =>
    ovIndex.get(`${playerId}::${field}`);

  const teams = useMemo(
    () =>
      Array.from(
        new Set((board.data?.players || []).map((p) => p.team).filter(Boolean) as string[]),
      ).sort(),
    [board.data],
  );

  const leverFields = useMemo(() => {
    if (pos === "ALL") return PLAYER_FIELDS;
    return PLAYER_FIELDS.filter(
      (f) => !f.positions || f.positions.includes(pos),
    );
  }, [pos]);

  const filtered = useMemo(() => {
    let rows = board.data?.players || [];
    if (pos !== "ALL") rows = rows.filter((p) => p.position === pos);
    if (team) rows = rows.filter((p) => p.team === team);
    const q = search.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (p) =>
          (p.name || "").toLowerCase().includes(q) ||
          (p.team || "").toLowerCase().includes(q),
      );
    }
    if (onlyTuned) {
      rows = rows.filter((p) => Object.keys(p.inputs?.levers || {}).length > 0);
    }

    const dir = sortDir;
    rows = [...rows].sort((a, b) => {
      // When grouping by team, keep team blocks contiguous first.
      if (groupByTeam) {
        const tc = (a.team || "zzz").localeCompare(b.team || "zzz");
        if (tc !== 0) return tc;
      }
      const av = playerSortVal(a, sortKey);
      const bv = playerSortVal(b, sortKey);
      if (typeof av === "string" && typeof bv === "string") {
        return dir * av.localeCompare(bv);
      }
      return dir * (Number(av) - Number(bv));
    });
    return rows;
  }, [board.data, pos, team, search, onlyTuned, groupByTeam, sortKey, sortDir]);

  const shown = filtered.slice(0, limit);
  const tunedCount = useMemo(
    () =>
      (board.data?.players || []).filter(
        (p) => Object.keys(p.inputs?.levers || {}).length > 0,
      ).length,
    [board.data],
  );

  const refresh = () => {
    void board.mutate();
    void ovRows.mutate();
  };

  const save = async (
    playerId: string,
    field: string,
    value: number,
    baseline: number | null,
  ) => {
    await api.adminUpsertOverride({
      entity_type: "player",
      entity_id: playerId,
      field,
      value,
      season: season ?? null,
      original_value: rowFor(playerId, field)?.original_value ?? baseline,
      note: "usage lever",
    });
    refresh();
  };

  const revert = async (id: number) => {
    await api.adminDeleteOverride(id);
    refresh();
  };

  const toggleSort = (k: PlayerSort) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(k);
      setSortDir(1);
    }
  };

  const sortMark = (k: PlayerSort) =>
    sortKey === k ? (sortDir === 1 ? " ↑" : " ↓") : "";

  // Group headers when groupByTeam is on.
  let lastTeam: string | null = null;

  return (
    <Card
      title={`Player usage levers${season ? ` · ${season}` : ""}${
        board.data
          ? ` (baselines from ${board.data.baseline_season})`
          : ""
      }`}
    >
      <p className="text-[11px] text-muted mb-3">
        Full-board view of every projectable player&apos;s usage inputs. Tune a
        role (target share, snap rate, …) while seeing surrounding teammates —
        especially useful with <em>Group by team</em> on. Shares move whole
        receiving/rushing families; Y/T and Y/C move yardage 1:1 and TDs at
        half strength; snap rate scales everything; availability overrides
        season durability. No baseline = lever inactive for that field.
      </p>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search player / team…"
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
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={groupByTeam}
            onChange={(e) => setGroupByTeam(e.target.checked)}
          />
          Group by team
        </label>
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
          {tunedCount > 0 && (
            <span className="text-amber-300"> · {tunedCount} tuned</span>
          )}
        </span>
      </div>

      {board.isLoading && <p className="text-sm text-muted">Loading player board…</p>}
      {board.error && (
        <p className="text-sm text-red-400">Failed to load player inputs board.</p>
      )}

      {!board.isLoading && !board.error && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-muted sticky top-0 bg-bg z-[1]">
              <tr className="border-b divider">
                <th
                  className="py-2 pr-3 font-medium cursor-pointer hover:text-fg"
                  onClick={() => toggleSort("name")}
                >
                  Player{sortMark("name")}
                </th>
                <th
                  className="py-2 pr-2 font-medium cursor-pointer hover:text-fg"
                  onClick={() => toggleSort("pos")}
                >
                  Pos{sortMark("pos")}
                </th>
                <th
                  className="py-2 pr-2 font-medium cursor-pointer hover:text-fg"
                  onClick={() => toggleSort("team")}
                >
                  Team{sortMark("team")}
                </th>
                <th
                  className="py-2 pr-2 font-medium cursor-pointer hover:text-fg tabular-nums"
                  onClick={() => toggleSort("season_rank")}
                  title="Season overall rank"
                >
                  #Szn{sortMark("season_rank")}
                </th>
                <th
                  className="py-2 pr-3 font-medium cursor-pointer hover:text-fg tabular-nums"
                  onClick={() => toggleSort("week_fantasy")}
                  title="This week projected PPR"
                >
                  Wk PPR{sortMark("week_fantasy")}
                </th>
                {leverFields.map((f) => (
                  <th key={f.key} className="py-2 pr-3 font-medium whitespace-nowrap">
                    {f.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => {
                const levers = p.inputs?.levers || {};
                const baselines = p.inputs?.baselines || {};
                const tuned = Object.keys(levers).length > 0;
                const teamChanged = groupByTeam && p.team !== lastTeam;
                if (groupByTeam) lastTeam = p.team;

                return (
                  <Fragment key={p.player_id}>
                    {teamChanged && p.team && (
                      <tr className="bg-white/[0.03]">
                        <td
                          colSpan={5 + leverFields.length}
                          className="py-1.5 px-1 text-[10px] uppercase tracking-wide text-muted font-semibold"
                        >
                          <span className="inline-flex items-center gap-1.5">
                            <TeamLogo teamId={p.team} size={14} />
                            {p.team}
                          </span>
                        </td>
                      </tr>
                    )}
                    <tr
                      className={`border-t divider hover:bg-white/[0.02] ${
                        tuned ? "bg-amber-500/[0.04]" : ""
                      }`}
                    >
                      <td className="py-1.5 pr-3 whitespace-nowrap">
                        <span className="font-medium">{p.name}</span>
                        {p.rookie && (
                          <span className="ml-1 text-[10px] text-sky-300">R</span>
                        )}
                        {p.injury_status && (
                          <span className="ml-1 text-[10px] text-red-300">
                            {p.injury_status}
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-muted">{p.position}</td>
                      <td className="py-1.5 pr-2">
                        <span className="inline-flex items-center gap-1">
                          {p.team && <TeamLogo teamId={p.team} size={14} />}
                          {p.team ?? "FA"}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2 tabular-nums text-muted">
                        {p.season.rank ?? "—"}
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums text-muted">
                        {p.week.fantasy != null ? p.week.fantasy.toFixed(1) : "—"}
                      </td>
                      {leverFields.map((f) => {
                        const baseline = baselines[f.key] ?? null;
                        const lever = levers[f.key];
                        const shownVal = lever ?? baseline;
                        // Soft-hide fields that don't apply to this player's pos.
                        const irrelevant =
                          f.positions &&
                          p.position &&
                          !f.positions.includes(p.position) &&
                          baseline == null;
                        if (irrelevant) {
                          return (
                            <td key={f.key} className="py-1.5 pr-3 text-muted/30">
                              —
                            </td>
                          );
                        }
                        // Availability can be set without a history baseline
                        // (rookies / thin samples); other levers need a baseline.
                        const needsBaseline = f.key !== "availability";
                        const inactive =
                          needsBaseline && baseline == null && lever == null;
                        return (
                          <td key={f.key} className="py-1.5 pr-3">
                            <InlineEditCell
                              value={shownVal}
                              baseline={baseline}
                              override={rowFor(p.player_id, f.key)}
                              step={f.step}
                              pct={f.pct}
                              disabled={inactive}
                              disabledReason="No baseline — lever inactive"
                              widthClass="w-14"
                              onSave={(v) => save(p.player_id, f.key, v, baseline)}
                              onRevert={revert}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  </Fragment>
                );
              })}
              {!shown.length && (
                <tr>
                  <td
                    colSpan={5 + leverFields.length}
                    className="py-8 text-center text-muted"
                  >
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
          className="mt-2 text-xs text-sky-300 hover:text-sky-200"
        >
          Show more ({filtered.length - limit} remaining)
        </button>
      )}
    </Card>
  );
}

function playerSortVal(p: ProjectionsBoardRow, key: PlayerSort): number | string {
  switch (key) {
    case "name":
      return (p.name || "").toLowerCase();
    case "team":
      return p.team || "zzz";
    case "pos":
      return p.position || "zzz";
    case "season_rank":
      return p.season.rank ?? 999999;
    case "week_fantasy":
      return -(p.week.fantasy ?? -1e9);
    case "snap":
      return -(p.inputs?.levers?.snap_rate ?? p.inputs?.baselines?.snap_rate ?? -1);
    case "tgt":
      return -(p.inputs?.levers?.target_share ?? p.inputs?.baselines?.target_share ?? -1);
    case "rush":
      return -(p.inputs?.levers?.rush_share ?? p.inputs?.baselines?.rush_share ?? -1);
    default:
      return 0;
  }
}
