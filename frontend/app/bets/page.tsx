"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { api, type Bet, type BetInput, type BetProfile } from "@/lib/api";
import { useAuth } from "@/context/AuthProvider";
import { Card } from "@/components/Card";
import { ProfileSummary } from "@/components/betting/ProfileSummary";
import { BetEntryForm } from "@/components/betting/BetEntryForm";
import { BetList } from "@/components/betting/BetList";

type TabId = "open" | "settled" | "all";

export default function BetsPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState<TabId>("open");

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  const profile = useSWR<BetProfile>(user ? ["bet-profile"] : null, () => api.betProfile());
  const bets = useSWR<Bet[]>(user ? ["bets-all"] : null, () => api.listBets());

  const refresh = useCallback(() => {
    profile.mutate();
    bets.mutate();
  }, [profile, bets]);

  const createBet = useCallback(
    async (b: BetInput) => {
      await api.createBet(b);
      refresh();
    },
    [refresh],
  );

  const deleteBet = useCallback(
    async (id: string) => {
      await api.deleteBet(id);
      refresh();
    },
    [refresh],
  );

  if (loading) return <p className="text-sm text-muted">Loading…</p>;
  if (!user) return null;

  const all = bets.data ?? [];
  const filtered =
    tab === "open"
      ? all.filter((b) => b.status === "pending")
      : tab === "settled"
        ? all.filter((b) => b.status !== "pending")
        : all;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">My bets</h1>
          <p className="text-sm text-muted mt-1">
            Track every wager, auto-graded against results, with closing line value.
          </p>
        </div>
        <Link href="/odds" className="text-xs text-team-primary hover:underline">
          Find a bet on the odds board →
        </Link>
      </div>

      {profile.data && <ProfileSummary p={profile.data} />}

      <BetEntryForm onSubmit={createBet} />

      <div>
        <div className="flex items-center gap-1 mb-3">
          {(["open", "settled", "all"] as TabId[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`text-xs px-3 py-1.5 rounded-full border capitalize ${
                tab === t
                  ? "border-team-primary text-team-primary"
                  : "divider text-muted hover:text-text"
              }`}
            >
              {t}
              {t === "open" && profile.data ? ` (${profile.data.pending})` : ""}
            </button>
          ))}
        </div>

        {bets.error ? (
          <Card>
            <p className="text-sm text-red-400">Couldn’t load your bets.</p>
          </Card>
        ) : (
          <BetList bets={filtered} onDelete={deleteBet} />
        )}
      </div>

      <p className="text-[11px] text-muted/80 leading-relaxed">
        Tracking is for your own record-keeping. If betting stops being fun, take a break —
        call 1-800-GAMBLER for confidential help. 21+ where legal.
      </p>
    </div>
  );
}
