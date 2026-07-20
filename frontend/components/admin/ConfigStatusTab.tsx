"use client";
import { useRef, useState } from "react";
import useSWR from "swr";
import { api, ConfigSnapshot, TuningStatus } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Tuning command center — every active param override, input lever, and
 * output pin in one place, plus export/import of the full configuration.
 *
 * This is the "what's currently tuned?" view. Editing still lives in the
 * Parameters / Model Inputs / Game-Player tabs; this tab is for tracking,
 * audit-at-a-glance, and backup/restore.
 */
export function ConfigStatusTab() {
  const status = useSWR(["admin-tuning-status"], () => api.adminTuningStatus(), {
    revalidateOnFocus: false,
  });
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => void status.mutate();

  const exportConfig = async () => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const snap = await api.adminExportSnapshot();
      const blob = new Blob([JSON.stringify(snap, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `nfl-tuning-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setMsg("Exported configuration snapshot.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (file: File, replaceParams: boolean) => {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const text = await file.text();
      const snapshot = JSON.parse(text) as ConfigSnapshot;
      const result = await api.adminImportSnapshot(snapshot, {
        note: `imported from ${file.name}`,
        replace_params: replaceParams,
      });
      refresh();
      const nParams = Object.keys(result.params_applied || {}).length;
      setMsg(
        `Import complete: ${nParams} params, ${result.overrides_upserted} overrides` +
          (result.params_reverted?.length
            ? `, ${result.params_reverted.length} params reverted`
            : "") +
          (result.errors?.length ? ` · ${result.errors.length} skipped` : ""),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  if (status.error) {
    return <Card className="p-4 text-sm text-red-400">Failed to load tuning status.</Card>;
  }
  if (!status.data) {
    return <Card className="p-4 text-sm text-muted">Loading tuning status…</Card>;
  }

  const s = status.data;
  const c = s.counts;
  const totalTuned =
    c.params + c.team_input_levers + c.player_input_levers +
    c.game_output_overrides + c.player_output_overrides;

  return (
    <div className="space-y-4">
      <Card className="p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Configuration status</div>
            <div className="text-xs text-muted mt-0.5">
              {s.registry_total} registry tunables · version{" "}
              <span className="font-mono text-[10px]">{s.version_token}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={exportConfig}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded bg-sky-600/20 text-sky-300 border border-sky-500/40 hover:bg-sky-600/30 disabled:opacity-50"
            >
              Export JSON
            </button>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-600/30 disabled:opacity-50"
            >
              Import merge…
            </button>
            <button
              onClick={() => {
                if (
                  confirm(
                    "Import and REPLACE all param overrides with the file? Keys not in the file revert to defaults. Entity overrides still merge.",
                  )
                ) {
                  const input = document.createElement("input");
                  input.type = "file";
                  input.accept = "application/json,.json";
                  input.onchange = () => {
                    const f = input.files?.[0];
                    if (f) void importFile(f, true);
                  };
                  input.click();
                }
              }}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded bg-amber-600/15 text-amber-300 border border-amber-500/40 hover:bg-amber-600/25 disabled:opacity-50"
            >
              Import replace…
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void importFile(f, false);
              }}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <Stat label="Params tuned" value={c.params} total={s.registry_total} />
          <Stat label="Team levers" value={c.team_input_levers} />
          <Stat label="Player levers" value={c.player_input_levers} />
          <Stat label="Game pins" value={c.game_output_overrides} />
          <Stat label="Player pins" value={c.player_output_overrides} />
        </div>

        {totalTuned === 0 && (
          <p className="text-xs text-muted">
            Everything is pure model output — no param overrides, input levers,
            or output pins are active.
          </p>
        )}
        {err && <div className="text-xs text-red-400">{err}</div>}
        {msg && <div className="text-xs text-emerald-300">{msg}</div>}
      </Card>

      {s.params_by_category.length > 0 && (
        <Card className="p-4 space-y-3">
          <div className="text-sm font-semibold">Active parameter overrides</div>
          {s.params_by_category.map((cat) => (
            <div key={cat.id}>
              <div className="text-[11px] uppercase tracking-wide text-muted mb-1">
                {cat.label}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {cat.params.map((p) => (
                  <span
                    key={p.key}
                    title={`${p.key}: default ${p.default}`}
                    className="text-[11px] px-2 py-1 rounded border border-amber-500/30 bg-amber-500/10 text-amber-200 tabular-nums"
                  >
                    {p.label}: {fmt(p.value)}{" "}
                    <span className="text-amber-400/70">
                      ({p.delta > 0 ? "+" : ""}
                      {fmt(p.delta)} vs default)
                    </span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </Card>
      )}

      {(s.team_input_levers.length > 0 || s.player_input_levers.length > 0) && (
        <Card className="p-4 space-y-3">
          <div className="text-sm font-semibold">Active input levers</div>
          <LeverTable
            title="Teams"
            rows={s.team_input_levers}
            onRevert={async (id) => {
              await api.adminDeleteOverride(id);
              refresh();
            }}
          />
          <LeverTable
            title="Players"
            rows={s.player_input_levers}
            onRevert={async (id) => {
              await api.adminDeleteOverride(id);
              refresh();
            }}
          />
        </Card>
      )}

      {s.recent_overrides.length > 0 && (
        <Card className="p-4">
          <div className="text-sm font-semibold mb-2">Recently updated overrides</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted">
                <tr>
                  <th className="py-1 pr-3">Type</th>
                  <th className="py-1 pr-3">Entity</th>
                  <th className="py-1 pr-3">Field</th>
                  <th className="py-1 pr-3 text-right">Value</th>
                  <th className="py-1 pr-3">Note</th>
                  <th className="py-1">Updated</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {s.recent_overrides.slice(0, 15).map((o, i) => (
                  <tr key={`${o.entity_type}-${o.entity_id}-${o.field}-${i}`} className="border-t border-white/5">
                    <td className="py-1 pr-3">{o.entity_type}</td>
                    <td className="py-1 pr-3 font-mono text-[10px]">{o.entity_id}</td>
                    <td className="py-1 pr-3">{o.field}</td>
                    <td className="py-1 pr-3 text-right text-amber-300">{o.value}</td>
                    <td className="py-1 pr-3 text-muted max-w-[12rem] truncate">{o.note || "—"}</td>
                    <td className="py-1 text-muted">
                      {o.updated_at ? new Date(o.updated_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card className="p-4">
        <div className="text-sm font-semibold mb-2">Registry categories</div>
        <div className="grid md:grid-cols-2 gap-2">
          {s.categories.map((cat) => (
            <div key={cat.id} className="text-xs border divider rounded p-2">
              <div className="font-medium">{cat.label}</div>
              <div className="text-muted mt-0.5">{cat.description}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  total,
}: {
  label: string;
  value: number;
  total?: number;
}) {
  return (
    <div className="rounded border divider bg-white/[0.02] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${value ? "text-amber-300" : "text-muted"}`}>
        {value}
        {total != null && (
          <span className="text-xs text-muted font-normal"> / {total}</span>
        )}
      </div>
    </div>
  );
}

function LeverTable({
  title,
  rows,
  onRevert,
}: {
  title: string;
  rows: TuningStatus["team_input_levers"];
  onRevert: (id: number) => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  if (!rows.length) return null;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted mb-1">
        {title} ({rows.length})
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Entity</th>
              <th className="py-1 pr-3">Field</th>
              <th className="py-1 pr-3 text-right">Baseline</th>
              <th className="py-1 pr-3 text-right">Override</th>
              <th className="py-1 pr-3">Season</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((o) => (
              <tr key={o.id} className="border-t border-white/5">
                <td className="py-1 pr-3 font-mono text-[10px]">{o.entity_id}</td>
                <td className="py-1 pr-3">{o.field}</td>
                <td className="py-1 pr-3 text-right text-muted">
                  {o.original_value ?? "—"}
                </td>
                <td className="py-1 pr-3 text-right text-amber-300">{o.value}</td>
                <td className="py-1 pr-3 text-muted">{o.season ?? "—"}</td>
                <td className="py-1 text-right">
                  <button
                    onClick={async () => {
                      setBusyId(o.id);
                      try {
                        await onRevert(o.id);
                      } finally {
                        setBusyId(null);
                      }
                    }}
                    disabled={busyId === o.id}
                    className="text-[10px] px-1.5 py-0.5 rounded text-red-300 border border-red-500/30 hover:bg-red-600/15 disabled:opacity-50"
                  >
                    {busyId === o.id ? "…" : "Revert"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmt(v: number) {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(1);
  if (a >= 10) return v.toFixed(2);
  return v.toFixed(3).replace(/\.?0+$/, "") || "0";
}
