"use client";
import { useState } from "react";
import useSWR from "swr";
import { api, ParamAuditEntry } from "@/lib/api";
import { Card } from "@/components/Card";

/**
 * Unified tuning change log — every parameter set/revert, entity override,
 * and preset action ever taken, newest first. Append-only on the backend, so
 * this is the complete audit history of "who changed what, from what, to
 * what, and why". Param entries offer one-click revert-to-old-value.
 */

const ACTION_META: Record<string, { label: string; cls: string }> = {
  param_set: { label: "param set", cls: "bg-sky-500/15 text-sky-300 border-sky-500/30" },
  param_revert: { label: "param revert", cls: "bg-slate-500/15 text-slate-300 border-slate-500/30" },
  params_revert_all: { label: "revert all", cls: "bg-red-500/15 text-red-300 border-red-500/30" },
  override_set: { label: "override set", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  override_delete: { label: "override removed", cls: "bg-slate-500/15 text-slate-300 border-slate-500/30" },
  preset_save: { label: "preset saved", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  preset_apply: { label: "preset applied", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  preset_delete: { label: "preset deleted", cls: "bg-red-500/15 text-red-300 border-red-500/30" },
};

export function ChangeLogTab() {
  const [targetType, setTargetType] = useState<string>("");
  const [search, setSearch] = useState("");
  const [pages, setPages] = useState<number>(1);
  const [beforeIds, setBeforeIds] = useState<(number | undefined)[]>([undefined]);
  const [reverting, setReverting] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const key = ["admin-param-audit", targetType, search, ...beforeIds];
  const { data, mutate, isLoading } = useSWR(
    key,
    async () => {
      // Fetch all loaded pages (cursor chain) so "load more" appends stably.
      const all: ParamAuditEntry[] = [];
      let hasMore = false;
      let nextId: number | null = null;
      for (const before of beforeIds) {
        const page = await api.adminParamAudit({
          target_type: targetType || undefined,
          search: search || undefined,
          limit: 50,
          before_id: before,
        });
        all.push(...page.entries);
        hasMore = page.has_more;
        nextId = page.next_before_id;
      }
      return { entries: all, has_more: hasMore, next_before_id: nextId };
    },
    { revalidateOnFocus: false },
  );

  const resetPaging = () => {
    setPages(1);
    setBeforeIds([undefined]);
  };

  const revertParamTo = async (e: ParamAuditEntry) => {
    if (e.old_value == null) return;
    if (!confirm(`Set ${e.target_key} back to ${e.old_value}?`)) return;
    setReverting(e.id);
    setErr(null);
    try {
      await api.adminSetParam(
        e.target_key,
        e.old_value,
        `revert to pre-change value (audit #${e.id})`,
      );
      void mutate();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Revert failed");
    } finally {
      setReverting(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {["", "param", "override", "preset"].map((t) => (
          <button
            key={t || "all"}
            onClick={() => {
              setTargetType(t);
              resetPaging();
            }}
            className={`text-xs px-2.5 py-1 rounded border ${
              targetType === t
                ? "bg-white/10 border-white/30 text-fg"
                : "divider text-muted hover:text-fg"
            }`}
          >
            {t || "All"}
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            resetPaging();
          }}
          placeholder="Search target (e.g. elo., KC, k_factor)…"
          className="bg-bg border divider rounded px-2 py-1 text-sm w-64 ml-auto"
        />
      </div>

      {err && <div className="text-xs text-red-400">{err}</div>}

      <Card className="p-0 overflow-hidden">
        {isLoading && !data ? (
          <div className="p-4 text-sm text-muted">Loading change log…</div>
        ) : !data?.entries.length ? (
          <div className="p-4 text-sm text-muted">
            No tuning changes recorded yet. Every parameter edit, override, and
            preset action will appear here.
          </div>
        ) : (
          <div className="divide-y divide-white/5">
            {data.entries.map((e) => {
              const meta = ACTION_META[e.action] || {
                label: e.action,
                cls: "bg-white/5 text-muted border-white/10",
              };
              const ctx = e.context || {};
              const scope = [
                ctx["season"] != null ? `S${ctx["season"]}` : null,
                ctx["week"] != null ? `W${ctx["week"]}` : null,
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <div key={e.id} className="px-4 py-2.5 flex flex-wrap items-center gap-2 text-sm">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${meta.cls}`}>
                    {meta.label}
                  </span>
                  <span className="font-mono text-xs">{e.target_key}</span>
                  {scope && <span className="text-[10px] text-muted">{scope}</span>}
                  {(e.old_value != null || e.new_value != null) && (
                    <span className="tabular-nums text-xs">
                      <span className="text-muted">{e.old_value ?? "—"}</span>
                      {" → "}
                      <span className="text-fg">{e.new_value ?? "—"}</span>
                    </span>
                  )}
                  {e.note && (
                    <span className="text-xs text-muted italic truncate max-w-xs" title={e.note}>
                      &ldquo;{e.note}&rdquo;
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-2 shrink-0">
                    {e.target_type === "param" &&
                      e.action === "param_set" &&
                      e.old_value != null && (
                        <button
                          onClick={() => revertParamTo(e)}
                          disabled={reverting === e.id}
                          title={`Set back to ${e.old_value}`}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-red-600/10 text-red-300 border border-red-500/30 hover:bg-red-600/20 disabled:opacity-50"
                        >
                          {reverting === e.id ? "…" : "↩ undo"}
                        </button>
                      )}
                    <span className="text-[10px] text-muted">{e.actor || "system"}</span>
                    <span className="text-[10px] text-muted/70 tabular-nums">
                      {e.created_at ? new Date(e.created_at).toLocaleString() : ""}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {data?.has_more && data.next_before_id != null && (
        <button
          onClick={() => {
            setPages(pages + 1);
            setBeforeIds([...beforeIds, data.next_before_id!]);
          }}
          className="text-xs px-3 py-1.5 rounded border divider text-muted hover:text-fg"
        >
          Load older entries
        </button>
      )}
    </div>
  );
}
