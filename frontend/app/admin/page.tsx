"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthProvider";
import { GameOverridesTab } from "@/components/admin/GameOverridesTab";
import { PlayerOverridesTab } from "@/components/admin/PlayerOverridesTab";
import { RankingBoardsTab } from "@/components/admin/RankingBoardsTab";
import { ModelInputsTab } from "@/components/admin/ModelInputsTab";
import { ParametersTab } from "@/components/admin/ParametersTab";
import { ChangeLogTab } from "@/components/admin/ChangeLogTab";
import { ProjectionsBoardTab } from "@/components/admin/ProjectionsBoardTab";
import { ConfigStatusTab } from "@/components/admin/ConfigStatusTab";
import { RerunsTab } from "@/components/admin/RerunsTab";

type TabId =
  | "board"
  | "status"
  | "games"
  | "players"
  | "fantasy"
  | "inputs"
  | "params"
  | "reruns"
  | "changelog"
  | "audit";

const TABS: { id: TabId; label: string }[] = [
  { id: "board", label: "Projections Board" },
  { id: "status", label: "Config Status" },
  { id: "games", label: "Game Projections" },
  { id: "players", label: "Player Projections" },
  { id: "fantasy", label: "Fantasy Rankings" },
  { id: "inputs", label: "Model Inputs" },
  { id: "params", label: "Parameters" },
  { id: "reruns", label: "Reruns" },
  { id: "changelog", label: "Change Log" },
  { id: "audit", label: "All Overrides" },
];

/** Admin-only projection control room.
 *
 * Gating mirrors Sparky's admin tab: the server-computed `is_admin` flag from
 * /auth/me (ADMIN_EMAILS allowlist when set, else the DB column) decides both
 * this page AND every /admin/overrides API route — a non-admin who guesses the
 * URL gets bounced here and 403s from the API anyway.
 */
export default function AdminPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const isAdmin = !!user?.is_admin;

  useEffect(() => {
    if (!loading && !isAdmin) router.replace("/");
  }, [loading, isAdmin, router]);

  const [tab, setTab] = useState<TabId>("board");

  if (loading) {
    return <div className="panel p-6 text-sm text-muted">Checking access…</div>;
  }
  if (!isAdmin) return null; // redirecting

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Projection Control Room</h1>
        <p className="text-sm text-muted mt-1 max-w-3xl">
          Three tuning layers: <strong>global parameters</strong> (Elo, market
          blend, weather, injury, priors…), <strong>model-input levers</strong>{" "}
          (pace, usage, defense, availability — recompute downstream), and{" "}
          <strong>output pins</strong> (spread/total/stat lines). Everything is
          audited, versioned into cache keys, and exportable. Reverting restores
          pure model output instantly. Adjusted values are <em>not</em> flagged
          on public pages.
        </p>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 rounded text-sm border ${
              tab === t.id
                ? "bg-white/10 border-white/20 font-semibold"
                : "divider text-muted hover:text-white"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "board" && <ProjectionsBoardTab />}
      {tab === "status" && <ConfigStatusTab />}
      {tab === "games" && <GameOverridesTab />}
      {tab === "players" && <PlayerOverridesTab />}
      {tab === "fantasy" && <RankingBoardsTab />}
      {tab === "inputs" && <ModelInputsTab />}
      {tab === "params" && <ParametersTab />}
      {tab === "reruns" && <RerunsTab />}
      {tab === "changelog" && <ChangeLogTab />}
      {tab === "audit" && <AuditTab />}
    </div>
  );
}

/** Every active override across all scopes, with one-click revert. */
function AuditTab() {
  const ovs = useSWR(["admin-ovs-all"], () => api.adminListOverrides());
  const [busyId, setBusyId] = useState<number | null>(null);

  const revert = async (id: number) => {
    setBusyId(id);
    try {
      await api.adminDeleteOverride(id);
      ovs.mutate();
    } finally {
      setBusyId(null);
    }
  };

  const rows = ovs.data?.overrides ?? [];
  if (ovs.isLoading)
    return <div className="panel p-6 text-sm text-muted">Loading…</div>;
  if (!rows.length)
    return (
      <div className="panel p-6 text-sm text-muted">
        No active overrides — everything on the site is pure model output.
      </div>
    );

  return (
    <div className="panel p-4 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] text-muted border-b divider">
            <th className="py-2 pr-4">Type</th>
            <th className="py-2 pr-4">Entity</th>
            <th className="py-2 pr-4">Scope</th>
            <th className="py-2 pr-4">Field</th>
            <th className="py-2 pr-4 text-right">Model</th>
            <th className="py-2 pr-4 text-right">Override</th>
            <th className="py-2 pr-4">Updated</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id} className="border-b divider last:border-0">
              <td className="py-2 pr-4">{o.entity_type}</td>
              <td className="py-2 pr-4 font-mono text-xs">{o.entity_id}</td>
              <td className="py-2 pr-4 text-muted">
                {o.season ?? "—"}
                {o.week != null ? ` · wk ${o.week}` : " · season"}
              </td>
              <td className="py-2 pr-4">{o.field}</td>
              <td className="py-2 pr-4 text-right tabular-nums text-muted">
                {o.original_value ?? "—"}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums text-amber-300">
                {o.value}
              </td>
              <td className="py-2 pr-4 text-xs text-muted">
                {o.updated_at ? new Date(o.updated_at).toLocaleString() : "—"}
              </td>
              <td className="py-2 text-right">
                <button
                  onClick={() => revert(o.id)}
                  disabled={busyId === o.id}
                  className="text-[11px] px-2 py-1 rounded bg-red-600/10 text-red-300 border border-red-500/30 hover:bg-red-600/20 disabled:opacity-50"
                >
                  {busyId === o.id ? "…" : "Revert"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
