"use client";
import { useEffect, useState } from "react";
import { AdminOverride } from "@/lib/api";

/**
 * Compact table-cell editor for admin overrides / input levers.
 *
 * Shows the active value (override or baseline), Enter-to-save draft input,
 * and one-click revert when an override row exists. Designed for dense
 * multi-player tables so surrounding players stay in view while you tune.
 */
export function InlineEditCell({
  value,
  baseline = null,
  override,
  step = 0.5,
  pct = false,
  disabled = false,
  disabledReason,
  widthClass = "w-16",
  onSave,
  onRevert,
}: {
  /** Currently served / displayed value (override wins over baseline). */
  value: number | null | undefined;
  /** Model baseline (shown as title / model: line when overridden). */
  baseline?: number | null;
  override?: AdminOverride;
  step?: number;
  pct?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  widthClass?: string;
  onSave: (value: number, originalValue: number | null) => Promise<void>;
  onRevert: (id: number) => Promise<void>;
}) {
  const shown = value ?? null;
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  useEffect(() => {
    setDraft("");
    setErr(false);
  }, [shown, override?.id, override?.value]);

  const fmt = (v: number | null | undefined) => {
    if (v == null || !Number.isFinite(v)) return "—";
    if (pct) return `${(v * 100).toFixed(1)}%`;
    if (step >= 1) return Number(v).toFixed(1);
    if (step >= 0.1) return Number(v).toFixed(1);
    return Number(v).toFixed(2);
  };

  const commit = async () => {
    const v = Number(draft);
    if (draft === "" || !Number.isFinite(v)) return;
    // Shares / rates stored as fractions — allow pasting 22 for 22%.
    let stored = v;
    if (pct && v > 1 && v <= 100) stored = v / 100;
    setBusy(true);
    setErr(false);
    try {
      await onSave(stored, override?.original_value ?? baseline ?? shown ?? null);
      setDraft("");
    } catch {
      setErr(true);
    } finally {
      setBusy(false);
    }
  };

  const isOverridden = override != null;

  return (
    <div className="flex items-center gap-1 min-w-0">
      <span
        className={`tabular-nums shrink-0 text-[11px] ${
          isOverridden ? "text-amber-300 font-medium" : "text-muted"
        }`}
        title={
          isOverridden
            ? `Override (baseline ${fmt(baseline ?? override?.original_value)})`
            : baseline != null
              ? `Baseline ${fmt(baseline)}`
              : disabled
                ? disabledReason || "Inactive"
                : "Model value"
        }
      >
        {fmt(shown)}
      </span>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void commit()}
        onBlur={() => {
          if (draft !== "") void commit();
        }}
        placeholder={pct ? "%" : "…"}
        disabled={busy || disabled}
        title={
          disabled
            ? disabledReason || "No baseline — lever inactive"
            : "Type value, Enter/blur to save"
        }
        className={`${widthClass} bg-bg border rounded px-1 py-0.5 text-[11px] tabular-nums ${
          err ? "border-red-500" : isOverridden ? "border-amber-500/50" : "divider"
        } disabled:opacity-40`}
      />
      {override && (
        <button
          onClick={() => void onRevert(override.id)}
          disabled={busy}
          title="Revert to model"
          className="text-muted hover:text-red-400 text-[11px] leading-none shrink-0"
        >
          ×
        </button>
      )}
    </div>
  );
}
