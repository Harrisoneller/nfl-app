"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { api, WeeklyBoardPlayer } from "@/lib/api";
import { SeasonSelect } from "@/components/SeasonSelect";
import { OverrideField, findOverride } from "./OverrideField";

const STAT_LABELS: Record<string, string> = {
  attempts: "Pass att",
  completions: "Completions",
  passing_yards: "Pass yds",
  passing_tds: "Pass TDs",
  interceptions: "INTs",
  carries: "Carries",
  rushing_yards: "Rush yds",
  rushing_tds: "Rush TDs",
  targets: "Targets",
  receptions: "Receptions",
  receiving_yards: "Rec yds",
  receiving_tds: "Rec TDs",
};

const SCORING = ["ppr", "half_ppr", "standard"] as const;

/** Player weekly stat + fantasy-point overrides, plus a start/sit rank pin.
 *
 * Stat overrides recenter the projection distribution server-side and ripple
 * into fantasy means, Prop Finder over-probs, and compare views. Fantasy-point
 * overrides win last if you set both.
 */
export function PlayerOverridesTab() {
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [week, setWeek] = useState<number | undefined>(undefined);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

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
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return players
      .filter((p) => !p.bye && p.name.toLowerCase().includes(q))
      .slice(0, 12);
  }, [players, query]);

  const selected: WeeklyBoardPlayer | undefined = useMemo(
    () => players.find((p) => p.player_id === selectedId) ?? undefined,
    [players, selectedId],
  );

  const overriddenIds = useMemo(() => {
    const ids = new Set<string>();
    for (const o of ovs.data?.overrides ?? []) ids.add(o.entity_id);
    return ids;
  }, [ovs.data]);

  const refresh = () => {
    ovs.mutate();
    board.mutate();
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

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
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
      </div>

      <div className="panel p-4">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            board.isLoading
              ? "Loading projection board…"
              : "Search a projectable player (e.g. Saquon Barkley)…"
          }
          disabled={board.isLoading}
          className="w-full bg-bg border divider rounded px-3 py-2 text-sm"
        />
        {results.length > 0 && (
          <ul className="mt-2 divide-y divide-white/5">
            {results.map((p) => (
              <li key={p.player_id}>
                <button
                  onClick={() => {
                    setSelectedId(p.player_id);
                    setQuery("");
                  }}
                  className="w-full text-left px-2 py-1.5 text-sm hover:bg-white/5 rounded flex items-center justify-between"
                >
                  <span>
                    {p.name}{" "}
                    <span className="text-muted text-xs">
                      {p.position} · {p.team ?? "FA"} · vs {p.opponent}
                    </span>
                  </span>
                  <span className="text-xs text-muted tabular-nums">
                    {p.fantasy?.ppr?.mean?.toFixed(1)} PPR
                    {overriddenIds.has(p.player_id) && (
                      <span className="ml-2 text-amber-300">adjusted</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selected && (
        <section className="panel p-4">
          <header className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div className="text-sm font-semibold">
              {selected.name}{" "}
              <span className="text-muted font-normal">
                {selected.position} · {selected.team ?? "FA"} · Week {effWeek} vs{" "}
                {selected.opponent} · {selected.tier} (#{selected.pos_rank}{" "}
                {selected.position})
              </span>
            </div>
            <button
              onClick={() => setSelectedId(null)}
              className="text-xs text-muted hover:text-white"
            >
              close
            </button>
          </header>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-8 gap-y-2">
            {Object.entries(selected.predicted ?? {}).map(([stat, s]) => (
              <OverrideField
                key={stat}
                label={STAT_LABELS[stat] ?? stat}
                served={s.mean}
                override={findOverride(
                  ovs.data?.overrides, "player", selected.player_id, stat,
                  effSeason, effWeek,
                )}
                step={stat.endsWith("_tds") || stat === "interceptions" ? 0.1 : 1}
                onSave={save(selected.player_id, stat)}
                onRevert={revert}
              />
            ))}
          </div>

          <div className="mt-4 pt-3 border-t divider">
            <div className="text-xs text-muted mb-2">
              Fantasy points (direct override — wins over stat-derived values)
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-2">
              {SCORING.map((fmt) => (
                <OverrideField
                  key={fmt}
                  label={fmt.replace("_", " ").toUpperCase()}
                  served={selected.fantasy?.[fmt]?.mean}
                  override={findOverride(
                    ovs.data?.overrides, "player", selected.player_id,
                    `fantasy_points_${fmt}`, effSeason, effWeek,
                  )}
                  step={0.5}
                  onSave={save(selected.player_id, `fantasy_points_${fmt}`)}
                  onRevert={revert}
                />
              ))}
            </div>
          </div>

          <div className="mt-4 pt-3 border-t divider">
            <div className="text-xs text-muted mb-2">
              Start/sit rank pin — holds this player at a positional rank on the
              weekly board regardless of projected points
            </div>
            <OverrideField
              label={`${selected.position} rank`}
              served={selected.pos_rank}
              override={findOverride(
                ovs.data?.overrides, "player", selected.player_id, "pos_rank",
                effSeason, effWeek,
              )}
              step={1}
              onSave={save(selected.player_id, "pos_rank")}
              onRevert={revert}
            />
          </div>
        </section>
      )}
    </div>
  );
}
