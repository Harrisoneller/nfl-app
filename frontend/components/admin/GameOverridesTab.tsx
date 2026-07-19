"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { SeasonSelect } from "@/components/SeasonSelect";
import { OverrideField, findOverride } from "./OverrideField";

/** Game-level projection overrides: spread, total, home win prob. */
export function GameOverridesTab() {
  const [season, setSeason] = useState<number | undefined>(undefined);
  const [week, setWeek] = useState<number | undefined>(undefined);

  const games = useSWR(["admin-pred-games", season, week], () =>
    api.predictGames(season, week, false),
  );
  const effSeason = games.data?.season ?? null;
  const effWeek = games.data?.week ?? null;

  const ovs = useSWR(
    effSeason != null && effWeek != null
      ? ["admin-ovs-game", effSeason, effWeek]
      : null,
    () =>
      api.adminListOverrides({
        entity_type: "game",
        season: effSeason!,
        week: effWeek!,
      }),
  );

  const refresh = () => {
    ovs.mutate();
    games.mutate();
  };

  const save =
    (gameId: string, field: string) =>
    async (value: number, originalValue: number | null) => {
      await api.adminUpsertOverride({
        entity_type: "game",
        entity_id: gameId,
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

      {games.isLoading && (
        <div className="panel p-6 text-sm text-muted">Loading predictions…</div>
      )}
      {!games.isLoading && !games.data?.games?.length && (
        <div className="panel p-6 text-sm text-muted">
          No games for this week.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {games.data?.games?.map((g) => {
          const p = g.prediction;
          const ovSpread = findOverride(
            ovs.data?.overrides, "game", g.id, "predicted_spread", effSeason, effWeek,
          );
          const ovTotal = findOverride(
            ovs.data?.overrides, "game", g.id, "predicted_total", effSeason, effWeek,
          );
          const ovProb = findOverride(
            ovs.data?.overrides, "game", g.id, "home_win_prob", effSeason, effWeek,
          );
          const touched = !!(ovSpread || ovTotal || ovProb);
          return (
            <section
              key={g.id}
              className={`panel p-4 ${touched ? "border border-amber-500/40" : ""}`}
            >
              <header className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <TeamLogo teamId={g.away_team_id} size={22} />
                  {g.away_team_id}
                  <span className="text-muted font-normal">@</span>
                  <TeamLogo teamId={g.home_team_id} size={22} />
                  {g.home_team_id}
                </div>
                <div className="text-[11px] text-muted">
                  {g.gameday}
                  {touched && (
                    <span className="ml-2 text-amber-300">adjusted</span>
                  )}
                </div>
              </header>
              <div className="space-y-2">
                <OverrideField
                  label="Spread (home)"
                  served={p.predicted_spread}
                  override={ovSpread}
                  step={0.5}
                  onSave={save(g.id, "predicted_spread")}
                  onRevert={revert}
                />
                <OverrideField
                  label="Total"
                  served={p.predicted_total}
                  override={ovTotal}
                  step={0.5}
                  onSave={save(g.id, "predicted_total")}
                  onRevert={revert}
                />
                <OverrideField
                  label="Home win %"
                  served={p.home_win_prob}
                  override={ovProb}
                  step={0.01}
                  onSave={save(g.id, "home_win_prob")}
                  onRevert={revert}
                />
              </div>
              <p className="mt-2 text-[10px] text-muted">
                Implied score {p.predicted_home_score}–{p.predicted_away_score}.
                Spread is home-relative (negative = home favored). Win prob is
                0–1; overriding the spread alone re-derives it.
              </p>
            </section>
          );
        })}
      </div>
    </div>
  );
}
