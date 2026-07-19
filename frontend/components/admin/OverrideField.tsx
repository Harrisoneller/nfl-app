"use client";
import { useEffect, useState } from "react";
import { AdminOverride } from "@/lib/api";

/** One editable projection value.
 *
 * Shows the served number (which already includes any active override), an
 * input to set/replace the override, and — when overridden — the original
 * model value plus a revert control. Saving posts an upsert; reverting
 * deletes the override row so the model value flows back instantly.
 */
export function OverrideField({
  label,
  served,
  override,
  step = 0.5,
  onSave,
  onRevert,
}: {
  label: string;
  served: number | null | undefined;
  override: AdminOverride | undefined;
  step?: number;
  onSave: (value: number, originalValue: number | null) => Promise<void>;
  onRevert: (id: number) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-sync the input whenever the served value / override changes upstream.
  useEffect(() => {
    setDraft(served != null ? String(served) : "");
  }, [served, override?.id, override?.value]);

  const dirty = draft !== "" && served != null && Number(draft) !== Number(served);

  const save = async () => {
    const v = Number(draft);
    if (!Number.isFinite(v)) {
      setErr("Not a number");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      // Snapshot the model value the FIRST time we override; afterwards the
      // backend keeps the original snapshot.
      await onSave(v, override ? override.original_value : served ?? null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const revert = async () => {
    if (!override) return;
    setBusy(true);
    setErr(null);
    try {
      await onRevert(override.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Revert failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] text-muted w-24 shrink-0">{label}</span>
        <input
          type="number"
          step={step}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && dirty && !busy) save();
          }}
          className={`w-24 bg-bg border rounded px-2 py-1 text-sm tabular-nums ${
            override ? "border-amber-500/60 text-amber-300" : "divider"
          }`}
        />
        {dirty && (
          <button
            onClick={save}
            disabled={busy}
            className="text-[11px] px-2 py-1 rounded bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-600/30 disabled:opacity-50"
          >
            {busy ? "…" : "Set"}
          </button>
        )}
        {override && !dirty && (
          <button
            onClick={revert}
            disabled={busy}
            title={`Revert to model${override.original_value != null ? ` (${override.original_value})` : ""}`}
            className="text-[11px] px-2 py-1 rounded bg-red-600/10 text-red-300 border border-red-500/30 hover:bg-red-600/20 disabled:opacity-50"
          >
            {busy ? "…" : "Revert"}
          </button>
        )}
      </div>
      {override && override.original_value != null && (
        <span className="text-[10px] text-muted pl-[6.4rem]">
          model: <span className="tabular-nums">{override.original_value}</span>
        </span>
      )}
      {err && <span className="text-[10px] text-red-400 pl-[6.4rem]">{err}</span>}
    </div>
  );
}

/** Find the active override for one (entity, field, season, week) scope. */
export function findOverride(
  overrides: AdminOverride[] | undefined,
  entityType: "game" | "player",
  entityId: string,
  field: string,
  season: number | null,
  week: number | null,
): AdminOverride | undefined {
  return overrides?.find(
    (o) =>
      o.entity_type === entityType &&
      o.entity_id === entityId &&
      o.field === field &&
      o.season === season &&
      o.week === week,
  );
}
