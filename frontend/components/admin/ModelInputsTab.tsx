"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { api, AdminOverride } from "@/lib/api";
import { Card } from "@/components/Card";
import { TeamLogo } from "@/components/TeamLogo";

/**
 * Model-input levers — adjust what the model BELIEVES, not what it outputs.
 *
 * Team levers (pace, yards/play, neutral pass rate, PPG) feed the scoring
 * model: totals, spreads, game scripts and every roster player's environment
 * recompute from the adjusted inputs. Player levers (target/rush share,
 * efficiency, snap rate) scale the projection posteriors, so stats, props,
 * and fantasy move together. Set them for coaching changes, scheme changes,
 * or role changes the data can't see yet.
 */

const TEAM_FIELDS: { key: string; label: string; step: number; pct?: boolean }[] = [
  { key: "pace", label: "Plays/gm", step: 0.5 },
  { key: "yards_per_play", label: "Yds/play", step: 0.1 },
  { key: "pass_rate", label: "Pass rate", step: 0.01, pct: true },
  { key: "points_per_game", label: "PPG", step: 0.5 },
];

const PLAYER_FIELDS: { key: string; label: string; step: number; pct?: boolean }[] = [
  { key: "snap_rate", label: "Snap rate", step: 0.01, pct: true },
  { key: "target_share", label: "Target share", step: 0.01, pct: true },
  { key: "rush_share", label: "Rush share", step: 0.01, pct: true },
  { key: "yards_per_target", label: "Yds/target", step: 0.1 },
  { key: "yards_per_carry", label: "Yds/carry", step: 0.1 },
];

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

  const save = async (teamId: string, field: string, value: number, baseline: number | null) => {
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
      title={`Team offense levers${season ? ` · ${season}` : ""}${
        inputs.data && inputs.data.baseline_season !== inputs.data.season
          ? ` (baselines from ${inputs.data.baseline_season})`
          : ""
      }`}
    >
      <p className="text-[11px] text-muted mb-3">
        Pace and yards/play multiply expected scoring (elasticity 1.0 / 0.9,
        clamped ±25%). PPG is a direct level-set that supersedes both. Pass rate
        re-tilts pass vs rush volume for every player on the roster without
        changing the total. Blank = model baseline.
      </p>
      {inputs.isLoading && <p className="text-sm text-muted">Loading baselines…</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Team</th>
              {TEAM_FIELDS.map((f) => (
                <th key={f.key} className="pr-4">{f.label}</th>
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
                {TEAM_FIELDS.map((f) => (
                  <td key={f.key} className="pr-4">
                    <LeverCell
                      baseline={t.baselines[f.key] ?? null}
                      overrideValue={t.overrides[f.key] ?? null}
                      overrideRow={rowFor(t.team_id, f.key)}
                      step={f.step}
                      pct={f.pct}
                      onSave={(v) => save(t.team_id, f.key, v, t.baselines[f.key] ?? null)}
                      onRevert={revert}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Player levers
// ---------------------------------------------------------------------------

function PlayerInputsSection() {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Same searchable pool the player-overrides tab uses.
  const board = useSWR(["admin-inputs-board"], () => api.weeklyBoard({ limit: 600 }), {
    revalidateOnFocus: false,
  });
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return (board.data?.players || [])
      .filter((p) => p.name.toLowerCase().includes(needle))
      .slice(0, 8);
  }, [board.data, query]);

  const detail = useSWR(
    selectedId ? ["admin-player-inputs", selectedId] : null,
    () => api.adminPlayerModelInputs(selectedId!),
    { revalidateOnFocus: false },
  );
  const season = detail.data?.season;
  const ovRows = useSWR(
    selectedId && season ? ["admin-player-input-rows", selectedId, season] : null,
    () => api.adminListOverrides({ entity_type: "player", entity_id: selectedId!, season }),
    { revalidateOnFocus: false },
  );

  const rowFor = (field: string): AdminOverride | undefined =>
    (ovRows.data?.overrides || []).find((o) => o.field === field && o.week == null);

  const refresh = () => {
    void detail.mutate();
    void ovRows.mutate();
  };

  const save = async (field: string, value: number, baseline: number | null) => {
    if (!selectedId) return;
    await api.adminUpsertOverride({
      entity_type: "player",
      entity_id: selectedId,
      field,
      value,
      season: season ?? null,
      original_value: rowFor(field)?.original_value ?? baseline,
      note: "usage lever",
    });
    refresh();
  };

  const revert = async (id: number) => {
    await api.adminDeleteOverride(id);
    refresh();
  };

  return (
    <Card title="Player usage levers">
      <p className="text-[11px] text-muted mb-3">
        Shares and efficiency scale the player&apos;s projection inputs relative
        to their baseline (last completed season). Target/rush share moves the
        whole receiving/rushing family; yds/target and yds/carry move yardage
        1:1 and TDs at half strength; snap rate scales everything. No baseline =
        lever inactive for that field.
      </p>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search player…"
        className="bg-bg border divider rounded px-3 py-1.5 text-sm w-64"
      />
      {matches.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {matches.map((p) => (
            <button
              key={p.player_id}
              onClick={() => {
                setSelectedId(p.player_id);
                setQuery("");
              }}
              className="text-xs border divider rounded px-2.5 py-1 hover:border-team-primary"
            >
              {p.name} <span className="text-muted">{p.position} · {p.team ?? "FA"}</span>
            </button>
          ))}
        </div>
      )}

      {detail.data && (
        <div className="mt-4 border divider rounded p-3">
          <div className="text-sm font-medium mb-2">
            {detail.data.name}{" "}
            <span className="text-muted text-xs">
              {detail.data.position} · {detail.data.team ?? "FA"} · baselines from{" "}
              {detail.data.baseline_season}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {PLAYER_FIELDS.map((f) => (
              <div key={f.key}>
                <div className="text-[10px] uppercase tracking-wide text-muted mb-1">
                  {f.label}
                </div>
                <LeverCell
                  baseline={detail.data!.baselines[f.key] ?? null}
                  overrideValue={detail.data!.overrides[f.key] ?? null}
                  overrideRow={rowFor(f.key)}
                  step={f.step}
                  pct={f.pct}
                  onSave={(v) => save(f.key, v, detail.data!.baselines[f.key] ?? null)}
                  onRevert={revert}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// One lever cell: baseline label + override input + revert
// ---------------------------------------------------------------------------

function LeverCell({
  baseline,
  overrideValue,
  overrideRow,
  step,
  pct = false,
  onSave,
  onRevert,
}: {
  baseline: number | null;
  overrideValue: number | null;
  overrideRow: AdminOverride | undefined;
  step: number;
  pct?: boolean;
  onSave: (value: number) => Promise<void>;
  onRevert: (id: number) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  const fmt = (v: number | null) =>
    v == null ? "—" : pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(step < 0.5 ? 2 : 1);

  const commit = async () => {
    const v = Number(draft);
    if (draft === "" || !Number.isFinite(v)) return;
    setBusy(true);
    setErr(false);
    try {
      await onSave(v);
      setDraft("");
    } catch {
      setErr(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <span
        className={`tabular-nums ${overrideValue != null ? "text-amber-300 font-medium" : "text-muted"}`}
        title={
          overrideValue != null
            ? `Override active (baseline ${fmt(baseline)})`
            : "Model baseline"
        }
      >
        {overrideValue != null ? fmt(overrideValue) : fmt(baseline)}
      </span>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void commit()}
        placeholder={pct ? "0.55" : "…"}
        disabled={busy || baseline == null}
        title={baseline == null ? "No baseline — lever inactive" : "Type a value, Enter to save"}
        className={`w-14 bg-bg border rounded px-1.5 py-0.5 text-xs tabular-nums ${
          err ? "border-red-500" : "divider"
        } disabled:opacity-40`}
      />
      {overrideRow && (
        <button
          onClick={() => void onRevert(overrideRow.id)}
          disabled={busy}
          title="Revert to model baseline"
          className="text-muted hover:text-red-400 text-xs"
        >
          ×
        </button>
      )}
    </div>
  );
}
