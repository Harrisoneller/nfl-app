"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  api,
  ModelParamEntry,
  ParamPreset,
  ParamPreview,
} from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Global model-parameter console.
 *
 * Every tunable in the projection stack — Elo, scoring model, market blend,
 * player engine, prop anchors, defense adjustment, ADP, lever mechanics,
 * distributions — declared in the backend registry and editable here without
 * a deploy. Changes are bounds-validated, audited, and live within seconds.
 *
 * Workflow: edits are STAGED locally first. From the staging bar you can
 * Preview the impact (recomputes this week's slate + player board under the
 * staged values, nothing persisted), then Apply. Presets snapshot/restore a
 * whole configuration.
 */

export function ParametersTab() {
  const registry = useSWR(["admin-params"], () => api.adminListParams(), {
    revalidateOnFocus: false,
  });
  const presets = useSWR(["admin-param-presets"], () => api.adminListPresets(), {
    revalidateOnFocus: false,
  });

  const [staged, setStaged] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [openCat, setOpenCat] = useState<string | null>(null);
  const [onlyOverridden, setOnlyOverridden] = useState(false);
  const [filter, setFilter] = useState("");
  const [preview, setPreview] = useState<ParamPreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => {
    void registry.mutate();
    void presets.mutate();
  };

  const allParams = useMemo(
    () => (registry.data?.categories || []).flatMap((c) => c.params),
    [registry.data],
  );

  const stage = (key: string, value: number) =>
    setStaged((s) => ({ ...s, [key]: value }));
  const unstage = (key: string) =>
    setStaged((s) => {
      const { [key]: _drop, ...rest } = s;
      return rest;
    });

  const stagedCount = Object.keys(staged).length;

  const runPreview = async () => {
    if (!stagedCount) return;
    setPreviewBusy(true);
    setErr(null);
    try {
      setPreview(await api.adminPreviewParams(staged));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setPreviewBusy(false);
    }
  };

  const applyStaged = async () => {
    if (!stagedCount) return;
    setApplyBusy(true);
    setErr(null);
    try {
      // Atomic bulk write: all-or-nothing bounds + cross-param validation.
      const noteParts = Object.keys(staged)
        .map((k) => notes[k])
        .filter(Boolean);
      await api.adminBulkSetParams(
        staged,
        noteParts.length ? noteParts.join("; ") : "bulk apply from admin UI",
      );
      setStaged({});
      setNotes({});
      setPreview(null);
      refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplyBusy(false);
    }
  };

  const revertOne = async (key: string) => {
    setErr(null);
    try {
      await api.adminRevertParam(key);
      unstage(key);
      refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Revert failed");
    }
  };

  if (registry.error) {
    return <Card className="p-4 text-sm text-red-400">Failed to load registry.</Card>;
  }
  if (!registry.data) {
    return <Card className="p-4 text-sm text-muted">Loading parameter registry…</Card>;
  }

  const { categories, overridden_count, total_count } = registry.data;
  const q = filter.trim().toLowerCase();

  return (
    <div className="space-y-4">
      {/* Header / controls */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-sm text-muted">
          <span className="text-fg font-semibold tabular-nums">{total_count}</span> tunables ·{" "}
          <span className={overridden_count ? "text-amber-300" : ""}>
            {overridden_count} overridden
          </span>
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter parameters…"
          className="bg-bg border divider rounded px-2 py-1 text-sm w-56"
        />
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={onlyOverridden}
            onChange={(e) => setOnlyOverridden(e.target.checked)}
          />
          Overridden only
        </label>
        {overridden_count > 0 && (
          <button
            onClick={async () => {
              if (!confirm("Revert EVERY parameter to its code default?")) return;
              await api.adminRevertAllParams("bulk revert from admin UI");
              setStaged({});
              refresh();
            }}
            className="text-[11px] px-2 py-1 rounded bg-red-600/10 text-red-300 border border-red-500/30 hover:bg-red-600/20"
          >
            Revert all
          </button>
        )}
      </div>

      <PresetsBar presets={presets.data?.presets || []} onChanged={refresh} setErr={setErr} />

      {err && <div className="text-xs text-red-400">{err}</div>}

      {/* Categories */}
      {categories.map((cat) => {
        const params = cat.params.filter((p) => {
          if (onlyOverridden && !(p.is_overridden || p.key in staged)) return false;
          if (!q) return true;
          return (
            p.key.toLowerCase().includes(q) ||
            p.label.toLowerCase().includes(q) ||
            p.description.toLowerCase().includes(q)
          );
        });
        if (!params.length) return null;
        const open = openCat === cat.id || !!q || onlyOverridden;
        const catOverridden = cat.params.filter((p) => p.is_overridden).length;
        return (
          <Card key={cat.id} className="p-0 overflow-hidden">
            <button
              onClick={() => setOpenCat(open && openCat === cat.id ? null : cat.id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/[0.03]"
            >
              <div>
                <div className="text-sm font-semibold flex items-center gap-2">
                  {cat.label}
                  {catOverridden > 0 && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
                      {catOverridden} tuned
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted mt-0.5">{cat.description}</div>
              </div>
              <span className="text-muted text-xs">{open ? "−" : "+"}</span>
            </button>
            {open && (
              <div className="divide-y divide-white/5">
                {params.map((p) => (
                  <ParamRow
                    key={p.key}
                    p={p}
                    stagedValue={staged[p.key]}
                    note={notes[p.key] || ""}
                    onStage={stage}
                    onUnstage={unstage}
                    onNote={(v) => setNotes((n) => ({ ...n, [p.key]: v }))}
                    onRevert={revertOne}
                  />
                ))}
              </div>
            )}
          </Card>
        );
      })}

      {/* Staging bar */}
      {stagedCount > 0 && (
        <div className="sticky bottom-3 z-10">
          <Card className="p-3 border-amber-500/40 bg-bg/95 backdrop-blur flex flex-wrap items-center gap-3">
            <span className="text-sm">
              <span className="font-semibold text-amber-300 tabular-nums">{stagedCount}</span>{" "}
              staged change{stagedCount > 1 ? "s" : ""}
            </span>
            <div className="flex flex-wrap gap-1.5 max-w-xl">
              {Object.entries(staged).map(([k, v]) => {
                const spec = allParams.find((p) => p.key === k);
                return (
                  <span
                    key={k}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-200 tabular-nums"
                  >
                    {spec?.label || k}: {spec?.value} → {v}
                    <button onClick={() => unstage(k)} className="ml-1 text-amber-400/70 hover:text-amber-200">
                      ×
                    </button>
                  </span>
                );
              })}
            </div>
            <div className="ml-auto flex gap-2">
              <button
                onClick={runPreview}
                disabled={previewBusy}
                className="text-xs px-3 py-1.5 rounded bg-sky-600/20 text-sky-300 border border-sky-500/40 hover:bg-sky-600/30 disabled:opacity-50"
              >
                {previewBusy ? "Computing…" : "Preview impact"}
              </button>
              <button
                onClick={applyStaged}
                disabled={applyBusy}
                className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-600/30 disabled:opacity-50"
              >
                {applyBusy ? "Applying…" : "Apply"}
              </button>
              <button
                onClick={() => {
                  setStaged({});
                  setPreview(null);
                }}
                className="text-xs px-3 py-1.5 rounded border divider text-muted hover:text-fg"
              >
                Discard
              </button>
            </div>
          </Card>
        </div>
      )}

      {preview && <PreviewPanel preview={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------

function ParamRow({
  p,
  stagedValue,
  note,
  onStage,
  onUnstage,
  onNote,
  onRevert,
}: {
  p: ModelParamEntry;
  stagedValue: number | undefined;
  note: string;
  onStage: (key: string, value: number) => void;
  onUnstage: (key: string) => void;
  onNote: (v: string) => void;
  onRevert: (key: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? String(stagedValue ?? p.value);
  const parsed = Number(shown);
  const inBounds = Number.isFinite(parsed) && parsed >= p.min && parsed <= p.max;
  const dirty = Number.isFinite(parsed) && parsed !== p.value;

  return (
    <div className="px-4 py-3 flex flex-wrap items-start gap-3">
      <div className="min-w-[16rem] flex-1">
        <div className="text-sm flex items-center gap-2">
          {p.label}
          {p.is_overridden && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
              tuned
            </span>
          )}
          {stagedValue != null && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30">
              staged
            </span>
          )}
        </div>
        <div className="text-xs text-muted mt-0.5 max-w-xl">{p.description}</div>
        <div className="text-[10px] text-muted/70 mt-1 font-mono">
          {p.key} · range [{p.min}, {p.max}]{p.unit ? ` ${p.unit}` : ""}
          {p.affects.length > 0 && <> · affects: {p.affects.join(", ")}</>}
        </div>
        {p.is_overridden && (
          <div className="text-[10px] text-amber-300/80 mt-0.5">
            default {p.default}
            {p.note ? ` — "${p.note}"` : ""}
            {p.updated_by ? ` (${p.updated_by})` : ""}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          step={p.step}
          min={p.min}
          max={p.max}
          value={shown}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (draft == null) return;
            const v = Number(draft);
            if (Number.isFinite(v) && v !== p.value && v >= p.min && v <= p.max) {
              onStage(p.key, p.kind === "int" ? Math.round(v) : v);
            } else if (v === p.value) {
              onUnstage(p.key);
            }
            setDraft(null);
          }}
          className={`w-28 bg-bg border rounded px-2 py-1 text-sm tabular-nums ${
            !inBounds
              ? "border-red-500/70 text-red-300"
              : stagedValue != null
                ? "border-sky-500/60 text-sky-200"
                : p.is_overridden
                  ? "border-amber-500/60 text-amber-300"
                  : "divider"
          }`}
        />
        {dirty && (
          <input
            value={note}
            onChange={(e) => onNote(e.target.value)}
            placeholder="why? (audit note)"
            className="w-40 bg-bg border divider rounded px-2 py-1 text-xs"
          />
        )}
        {p.is_overridden && (
          <button
            onClick={() => onRevert(p.key)}
            title={`Revert to default (${p.default})`}
            className="text-[11px] px-2 py-1 rounded bg-red-600/10 text-red-300 border border-red-500/30 hover:bg-red-600/20"
          >
            Revert
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function PresetsBar({
  presets,
  onChanged,
  setErr,
}: {
  presets: ParamPreset[];
  onChanged: () => void;
  setErr: (e: string | null) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : `${label} failed`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="p-3 flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted font-semibold uppercase tracking-wide">Presets</span>
      {presets.map((pr) => (
        <span
          key={pr.id}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border divider bg-white/[0.03]"
          title={`${pr.description || "no description"} · ${Object.keys(pr.params).length} params · by ${pr.created_by}`}
        >
          {pr.name}
          <button
            onClick={() =>
              confirm(
                `Apply "${pr.name}"? Sets ${Object.keys(pr.params).length} params and reverts everything else.`,
              ) && run("apply", () => api.adminApplyPreset(pr.name, "applied from admin UI"))
            }
            disabled={busy != null}
            className="text-emerald-300 hover:text-emerald-200 disabled:opacity-50"
            title="Apply"
          >
            ▶
          </button>
          <button
            onClick={() =>
              confirm(`Delete preset "${pr.name}"?`) &&
              run("delete", () => api.adminDeletePreset(pr.name))
            }
            disabled={busy != null}
            className="text-red-400/70 hover:text-red-300 disabled:opacity-50"
            title="Delete"
          >
            ×
          </button>
        </span>
      ))}
      <span className="flex items-center gap-1 ml-auto">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Save current as…"
          className="bg-bg border divider rounded px-2 py-1 text-xs w-36"
        />
        <button
          onClick={() =>
            name.trim() &&
            run("save", () => api.adminSavePreset(name.trim(), "saved from admin UI")).then(() =>
              setName(""),
            )
          }
          disabled={!name.trim() || busy != null}
          className="text-[11px] px-2 py-1 rounded bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-600/30 disabled:opacity-50"
        >
          Save
        </button>
      </span>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function PreviewPanel({ preview, onClose }: { preview: ParamPreview; onClose: () => void }) {
  const fmt = (v: number | null | undefined, nd = 2) => (v == null ? "—" : v.toFixed(nd));
  const deltaCls = (v: number | null | undefined) =>
    !v ? "text-muted" : v > 0 ? "text-emerald-300" : "text-red-300";
  const movedGames = preview.games.filter(
    (g) => g.delta.spread || g.delta.total || g.delta.home_win_prob,
  );

  return (
    <Card className="p-4 space-y-4 border-sky-500/40">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">
            Impact preview — {preview.season} week {preview.week ?? "next"}
          </div>
          <div className="text-xs text-muted mt-0.5">
            {preview.summary.games_moved}/{preview.summary.games_evaluated} games moved · max
            spread Δ {fmt(preview.summary.max_spread_delta)} · max total Δ{" "}
            {fmt(preview.summary.max_total_delta)} · {preview.summary.players_moved} players moved
            (max Δ {fmt(preview.summary.max_player_delta)} pts)
          </div>
        </div>
        <button onClick={onClose} className="text-xs text-muted hover:text-fg">
          Close ×
        </button>
      </div>

      {movedGames.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1.5">
            Games (before → after)
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted text-left">
                <tr>
                  <th className="py-1 pr-3">Matchup</th>
                  <th className="py-1 pr-3">Spread</th>
                  <th className="py-1 pr-3">Total</th>
                  <th className="py-1 pr-3">Home win%</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {movedGames.slice(0, 16).map((g) => (
                  <tr key={g.game_id} className="border-t border-white/5">
                    <td className="py-1 pr-3">
                      {g.away_team} @ {g.home_team}
                    </td>
                    <td className="py-1 pr-3">
                      {fmt(g.before.spread, 1)} → {fmt(g.after.spread, 1)}{" "}
                      <span className={deltaCls(g.delta.spread)}>
                        ({g.delta.spread! > 0 ? "+" : ""}
                        {fmt(g.delta.spread)})
                      </span>
                    </td>
                    <td className="py-1 pr-3">
                      {fmt(g.before.total, 1)} → {fmt(g.after.total, 1)}{" "}
                      <span className={deltaCls(g.delta.total)}>
                        ({g.delta.total! > 0 ? "+" : ""}
                        {fmt(g.delta.total)})
                      </span>
                    </td>
                    <td className="py-1 pr-3">
                      {fmt((g.before.home_win_prob ?? 0) * 100, 1)}% →{" "}
                      {fmt((g.after.home_win_prob ?? 0) * 100, 1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {preview.players.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1.5">
            Top player movers ({"Δ"} projected fantasy pts)
          </div>
          <div className="flex flex-wrap gap-1.5">
            {preview.players.slice(0, 24).map((p) => (
              <span
                key={p.player_id}
                className="text-[10px] px-1.5 py-0.5 rounded border divider bg-white/[0.03] tabular-nums"
              >
                {p.name} <span className="text-muted">({p.position})</span> {p.before} →{" "}
                {p.after}{" "}
                <span className={deltaCls(p.delta)}>
                  ({p.delta > 0 ? "+" : ""}
                  {p.delta})
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {movedGames.length === 0 && preview.players.length === 0 && (
        <div className="text-xs text-muted">
          No movement on this week&apos;s slate — these parameters may only affect batch
          rebuilds (e.g. Elo K-factor) or other seasons.
        </div>
      )}

      <div className="text-[10px] text-muted/70 space-y-0.5">
        {preview.notes.map((n, i) => (
          <div key={i}>• {n}</div>
        ))}
      </div>
    </Card>
  );
}
