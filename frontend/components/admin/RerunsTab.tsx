"use client";
import { useState } from "react";
import useSWR from "swr";
import { api, RerunScope, RerunRun } from "@/lib/api";

/**
 * Model reruns — the "make my tuning live everywhere" control.
 *
 * Player boards and game slates already recompute on the next read after a
 * param write (their cache keys embed the param+override version token). Two
 * things don't self-heal, and that's what a rerun fixes:
 *   • the season Monte-Carlo sim (playoff/division/SB odds) — cached 24h and
 *     not param-versioned, so it's evicted and recomputed here;
 *   • Elo ratings — only rebuilt by the batch job, so K-factor / spread-
 *     conversion changes need the "Full rebuild" scope to move spreads.
 *
 * Reruns run in the background (one at a time). We poll /status while one is
 * in flight so the button state and history stay live.
 */

const SCOPES: {
  id: RerunScope;
  label: string;
  blurb: string;
  eta: string;
  recommended?: boolean;
  cls: string;
}[] = [
  {
    id: "quick",
    label: "Quick recompute",
    blurb:
      "Evict the season sim and rewarm the game slate + player boards. Picks up market-blend, prior, weather, injury and output-pin changes.",
    eta: "~seconds",
    recommended: true,
    cls: "bg-sky-500/15 text-sky-200 border-sky-500/40 hover:bg-sky-500/25",
  },
  {
    id: "games",
    label: "Games only",
    blurb: "Season Monte-Carlo sim + game slate. Skips player boards.",
    eta: "~seconds",
    cls: "bg-violet-500/15 text-violet-200 border-violet-500/40 hover:bg-violet-500/25",
  },
  {
    id: "players",
    label: "Players only",
    blurb: "Weekly projection board + season leaderboard.",
    eta: "~seconds",
    cls: "bg-emerald-500/15 text-emerald-200 border-emerald-500/40 hover:bg-emerald-500/25",
  },
  {
    id: "full",
    label: "Full rebuild",
    blurb:
      "Rebuild Elo history + derived profiles, then games + players. Required for K-factor / home-field / Elo↔spread changes to move spreads.",
    eta: "~minutes",
    cls: "bg-amber-500/15 text-amber-200 border-amber-500/40 hover:bg-amber-500/25",
  },
];

const STATUS_META: Record<string, string> = {
  running: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  error: "bg-red-500/15 text-red-300 border-red-500/30",
};

function fmt(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

function duration(r: RerunRun): string {
  if (!r.started_at) return "—";
  const start = new Date(r.started_at).getTime();
  const end = r.finished_at ? new Date(r.finished_at).getTime() : Date.now();
  const s = Math.max(0, Math.round((end - start) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function RerunsTab() {
  const [busy, setBusy] = useState<RerunScope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const { data, mutate } = useSWR("admin-rerun-status", () => api.adminRerunStatus(), {
    // Poll fast while a rerun is running, slowly otherwise.
    refreshInterval: (d) => (d?.running ? 2000 : 15000),
    revalidateOnFocus: true,
  });

  const running = data?.running ?? false;

  const run = async (scope: RerunScope) => {
    setErr(null);
    setNote(null);
    setBusy(scope);
    try {
      const res = await api.adminTriggerRerun(scope);
      setNote(`Started ${res.label} (run #${res.run_id}, season ${res.season}).`);
      await mutate();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to start rerun");
    } finally {
      setBusy(null);
    }
  };

  const runs = data?.runs ?? [];

  return (
    <div className="space-y-5">
      <div className="panel p-4">
        <h2 className="text-sm font-semibold">Model reruns</h2>
        <p className="text-xs text-muted mt-1 max-w-3xl">
          After tuning parameters, most pages update on the next read — player
          boards and game predictions are version-keyed to your changes. A rerun
          covers what doesn't self-heal: it recomputes the season{" "}
          <strong>Monte-Carlo sim</strong> (playoff / division / Super-Bowl odds)
          and rewarms every cache so the first visitor gets fast, fresh numbers.
          Use <strong>Full rebuild</strong> after changing Elo params (K-factor,
          home-field, spread conversion) — those only land once Elo is rebuilt.
        </p>
      </div>

      {running && data?.active && (
        <div className="panel p-3 border border-sky-500/30 bg-sky-500/5 flex items-center gap-3">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-sky-400 animate-pulse" />
          <span className="text-sm">
            Running <strong>{data.active.scope}</strong> rerun (run #
            {data.active.run_id}) — started {fmt(data.active.started_at ?? null)}.
            New reruns are blocked until it finishes.
          </span>
        </div>
      )}

      {err && (
        <div className="panel p-3 border border-red-500/30 bg-red-500/5 text-sm text-red-300">
          {err}
        </div>
      )}
      {note && !err && (
        <div className="panel p-3 border border-emerald-500/30 bg-emerald-500/5 text-sm text-emerald-300">
          {note}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {SCOPES.map((s) => (
          <div key={s.id} className="panel p-4 flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">{s.label}</span>
              {s.recommended && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-200 border border-sky-500/30">
                  recommended
                </span>
              )}
              <span className="text-[11px] text-muted ml-auto">{s.eta}</span>
            </div>
            <p className="text-xs text-muted flex-1">{s.blurb}</p>
            <button
              onClick={() => run(s.id)}
              disabled={running || busy !== null}
              className={`mt-1 px-3 py-1.5 rounded text-sm border font-medium disabled:opacity-40 disabled:cursor-not-allowed ${s.cls}`}
            >
              {busy === s.id ? "Starting…" : running ? "Rerun in progress…" : `Run ${s.label}`}
            </button>
          </div>
        ))}
      </div>

      <div className="panel p-4 overflow-x-auto">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Recent reruns</h3>
          <button
            onClick={() => mutate()}
            className="text-[11px] px-2 py-1 rounded divider text-muted hover:text-white"
          >
            Refresh
          </button>
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-muted">No reruns yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] text-muted border-b divider">
                <th className="py-2 pr-4">Run</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Started</th>
                <th className="py-2 pr-4">Took</th>
                <th className="py-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b divider last:border-0 align-top">
                  <td className="py-2 pr-4 font-mono text-xs">#{r.id}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`text-[11px] px-1.5 py-0.5 rounded border ${
                        STATUS_META[r.status] ?? "divider text-muted"
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-xs text-muted whitespace-nowrap">
                    {fmt(r.started_at)}
                  </td>
                  <td className="py-2 pr-4 text-xs tabular-nums text-muted">
                    {duration(r)}
                  </td>
                  <td className="py-2 text-xs text-muted font-mono break-words">
                    {r.message ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
