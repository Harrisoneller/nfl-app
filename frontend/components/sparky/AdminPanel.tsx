"use client";
import { useState } from "react";
import { api, SparkyAdminStatus } from "@/lib/api";

/**
 * Admin / debug view (SOW 2): pipeline health + the controls to rebuild the
 * slate or seed synthetic demo data (useful in the offseason).
 */
export function AdminPanel({
  status,
  onChanged,
}: {
  status: SparkyAdminStatus | undefined;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (
    action: "refresh" | "backfill" | "settle" | "backtest" | "build_real",
    fn: () => Promise<unknown>,
  ) => {
    setBusy(action);
    setMsg(null);
    setErr(null);
    try {
      const res: any = await fn();
      if (action === "backfill") {
        setMsg("Demo data seeded and slate built.");
      } else if (action === "settle") {
        const p = res?.settled_picks ?? 0;
        const pr = res?.settled_parlays ?? 0;
        const sk = res?.skipped ?? 0;
        setMsg(`Settlement complete: ${p} picks + ${pr} parlays settled (${sk} skipped).`);
      } else if (action === "backtest") {
        const games = res?.n_games ?? 0;
        const brier = res?.metrics?.brier_score;
        const acc = res?.metrics?.pick_accuracy?.accuracy_pct;
        const brierStr = (typeof brier === "number" && !Number.isNaN(brier)) ? brier.toFixed(3) : "—";
        setMsg(`Backtest done: ${games} games. Accuracy ${acc ?? "—"}%. Brier ${brierStr}. Check console for full report.`);
        console.log("Sparky Backtest Result:", res);
      } else if (action === "build_real") {
        const n = res?.count ?? 0;
        const refresh = res?.odds_refresh;
        const status = refresh?.status ?? "unknown";
        const events = refresh?.upstream_events;
        if (n > 0) {
          setMsg(
            `Real slate built — ${n} game${n === 1 ? "" : "s"}` +
              (refresh ? ` (odds API: ${status}${events != null ? `, ${events} events` : ""})` : "") +
              ". Switch to Dashboard.",
          );
        } else if (status === "ok" && (events ?? 0) === 0) {
          setMsg(
            "Odds API returned 0 upcoming NFL events — the Week 1 schedule may not be posted yet. Try again closer to kickoff.",
          );
        } else if (status === "error" || status === "unauthorized" || status === "rate_limited") {
          setMsg(
            `Odds API ${status}${refresh?.message ? `: ${refresh.message}` : ""}. Check ODDS_API_KEY and try again.`,
          );
        } else {
          setMsg(
            `Built 0 games (odds API: ${status}${events != null ? `, ${events} events` : ""}). No real upcoming snapshots to build from.`,
          );
        }
      } else {
        setMsg("Slate rebuilt from current snapshots.");
      }
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : `Failed to ${action}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Snapshots" value={fmt(status?.snapshots)} sub={`${fmt(status?.snapshot_events)} games`} />
        <Stat label="Predictions" value={fmt(status?.predictions)} sub={status?.last_slate_date ?? "—"} />
        <Stat label="Settled results" value={fmt(status?.settled_results)} />
        <Stat label="Parlay rows" value={fmt(status?.parlay_rankings)} />
      </div>

      <div className="sparky-card p-4">
        <h3 className="text-sm font-semibold text-white mb-3">Pipeline health</h3>
        <div className="space-y-2 text-sm">
          <Health ok={!!status?.pipeline_ready} label="Pipeline ready (snapshots + predictions present)" />
          <Health ok={!!status?.has_history_for_movement} label="Line-movement history available" />
          <Row label="Last snapshot captured" value={fmtTime(status?.last_snapshot_at)} />
          <Row label="Last slate built" value={status?.last_slate_date ?? "—"} />
        </div>
      </div>

      <div className="sparky-card p-4">
        <h3 className="text-sm font-semibold text-white mb-1">Controls</h3>
        <p className="text-xs text-muted mb-3">
          In-season, snapshots and the slate refresh automatically with the twice-daily odds pull.
          Use these to rebuild on demand or to populate a demo in the offseason.
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => run("refresh", () => api.sparkyAdminRefresh())}
            disabled={!!busy}
            className="sparky-btn"
          >
            {busy === "refresh" ? "Rebuilding…" : "Rebuild slate"}
          </button>
          <button
            onClick={() => run("build_real", () => api.sparkyAdminBuildReal())}
            disabled={!!busy}
            className="sparky-btn sparky-btn--solid"
            title="Clear demo data and build predictions from the actual current Week 1 schedule + real odds + your model"
          >
            {busy === "build_real" ? "Building real…" : "Build real Week 1 slate"}
          </button>
          <button
            onClick={() => run("backfill", () => api.sparkyAdminBackfill(30))}
            disabled={!!busy}
            className="sparky-btn sparky-btn--solid"
          >
            {busy === "backfill" ? "Seeding…" : "Seed 30-day demo"}
          </button>
          <button
            onClick={() => run("settle", () => api.sparkyAdminSettle(14))}
            disabled={!!busy}
            className="sparky-btn"
            title="Record real outcomes for any completed games that have Sparky predictions. This powers the live accuracy dashboard."
          >
            {busy === "settle" ? "Settling…" : "Settle completed games"}
          </button>
          <button
            onClick={() => {
              // Use a recent 30-day window (works well after seeding demo data)
              const end = new Date();
              const start = new Date(end.getTime() - 1000 * 3600 * 24 * 45);
              const startStr = start.toISOString().slice(0, 10);
              const endStr = end.toISOString().slice(0, 10);
              run("backtest", () => api.sparkyAdminBacktest(startStr, endStr, "replay"));
            }}
            disabled={!!busy}
            className="sparky-btn"
            title="Run a historical backtest of the current Sparky engine against past odds snapshots. This is the best way to validate whether the model is improving. Best used after seeding demo data or with real historical snapshots."
          >
            {busy === "backtest" ? "Backtesting…" : "Run backtest (replay)"}
          </button>
        </div>
        <p className="mt-2 text-[10px] text-muted">
          Settlement is the live path for accuracy. It matches final scores from the games table to recent Sparky predictions
          (idempotent — safe to run anytime).
        </p>
        {msg && <p className="mt-3 text-xs text-emerald-300">{msg}</p>}
        {err && <p className="mt-3 text-xs text-red-400">{err}</p>}
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="sparky-stat">
      <div className="sparky-stat__value">{value}</div>
      <div className="sparky-stat__label">{label}</div>
      {sub && <div className="text-[10px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

function Health({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-2.5 h-2.5 rounded-full ${ok ? "bg-emerald-400" : "bg-slate-500"}`} />
      <span className={ok ? "text-slate-200" : "text-muted"}>{label}</span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className="text-slate-200 tabular-nums">{value}</span>
    </div>
  );
}

function fmt(n: number | undefined): string {
  return n == null ? "—" : n.toLocaleString();
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
