"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { SeasonSelect } from "@/components/SeasonSelect";
import { OverrideField, findOverride } from "./OverrideField";

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE"] as const;
const SCORING = ["ppr", "half_ppr", "standard"] as const;

/** Season fantasy leaderboard: pin ranks and hand-set season point totals.
 *
 * These are season-scoped overrides (no week) — the weekly start/sit board has
 * its own per-week pins on the Players tab.
 */
export function FantasyRanksTab() {
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [position, setPosition] = useState<string>("ALL");
  const [scoring, setScoring] = useState<(typeof SCORING)[number]>("ppr");

  const lb = useSWR(["admin-leaderboard", season, position, scoring], () =>
    api.projectionLeaderboard({
      season,
      position: position === "ALL" ? undefined : position,
      scoring,
      limit: 100,
    }),
  );
  const effSeason = lb.data?.season ?? null;

  // Season-scoped player overrides: week IS NULL, so don't pass week.
  const ovs = useSWR(
    effSeason != null ? ["admin-ovs-season", effSeason] : null,
    () => api.adminListOverrides({ entity_type: "player", season: effSeason! }),
  );
  const seasonOvs = (ovs.data?.overrides ?? []).filter((o) => o.week === null);

  const refresh = () => {
    ovs.mutate();
    lb.mutate();
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
        week: null,
        original_value: originalValue,
      });
      refresh();
    };

  const revert = async (id: number) => {
    await api.adminDeleteOverride(id);
    refresh();
  };

  const fantasyKey = `fantasy_${scoring}` as const;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <SeasonSelect value={season} onChange={setSeason} />
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Position</span>
          <select
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            className="bg-bg border divider rounded px-2 py-1.5 text-sm"
          >
            {POSITIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">Scoring</span>
          <select
            value={scoring}
            onChange={(e) => setScoring(e.target.value as (typeof SCORING)[number])}
            className="bg-bg border divider rounded px-2 py-1.5 text-sm"
          >
            {SCORING.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ").toUpperCase()}
              </option>
            ))}
          </select>
        </label>
      </div>

      {lb.isLoading && (
        <div className="panel p-6 text-sm text-muted">Loading leaderboard…</div>
      )}

      <div className="space-y-2">
        {lb.data?.players?.map((p) => {
          if (!p.player_id) return null;
          const fp = (p as Record<string, any>)[fantasyKey];
          const ovRank = findOverride(
            seasonOvs, "player", p.player_id, "rank", effSeason, null,
          );
          const ovPts = findOverride(
            seasonOvs, "player", p.player_id, `fantasy_points_${scoring}`,
            effSeason, null,
          );
          const touched = !!(ovRank || ovPts);
          return (
            <section
              key={p.player_id}
              className={`panel px-4 py-3 flex items-center gap-6 flex-wrap ${
                touched ? "border border-amber-500/40" : ""
              }`}
            >
              <div className="w-56 shrink-0">
                <span className="text-sm font-semibold tabular-nums mr-2">
                  #{p.rank}
                </span>
                <span className="text-sm">{p.name}</span>
                <div className="text-[11px] text-muted">
                  {p.position} · {p.team ?? "FA"}
                  {touched && <span className="ml-2 text-amber-300">adjusted</span>}
                </div>
              </div>
              <OverrideField
                label="Rank pin"
                served={p.rank}
                override={ovRank}
                step={1}
                onSave={save(p.player_id, "rank")}
                onRevert={revert}
              />
              <OverrideField
                label={`Season pts (${scoring.replace("_", " ")})`}
                served={fp?.mean}
                override={ovPts}
                step={1}
                onSave={save(p.player_id, `fantasy_points_${scoring}`)}
                onRevert={revert}
              />
            </section>
          );
        })}
      </div>
    </div>
  );
}
