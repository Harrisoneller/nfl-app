import { useCallback, useState } from "react";
import { Alert, Pressable, StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { api, type Bet, type BetProfile } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/context/AuthProvider";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { Loading, ErrorState, EmptyState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, spacing } from "@/theme/theme";
import { americanOdds, num, signed, shortDate } from "@/lib/format";

const FILTERS = [
  { id: "all" as const, label: "All" },
  { id: "pending" as const, label: "Pending" },
  { id: "won" as const, label: "Won" },
  { id: "lost" as const, label: "Lost" },
];

export default function BetsScreen() {
  const { isAuthenticated, loading } = useAuth();
  const [filter, setFilter] = useState<"all" | "pending" | "won" | "lost">("all");

  const profile = useApi(() => api.betProfile(), [], { enabled: isAuthenticated });
  const bets = useApi(
    () => api.listBets(filter === "all" ? undefined : filter),
    [filter],
    { enabled: isAuthenticated },
  );

  const onRefresh = useCallback(() => {
    profile.refetch();
    bets.refetch();
  }, [profile, bets]);

  async function settle() {
    try {
      const r = await api.settleBets();
      Alert.alert("Settled", `${r.settled_bets} bets graded.`);
      onRefresh();
    } catch (e) {
      Alert.alert("Couldn't settle", e instanceof Error ? e.message : "Try again.");
    }
  }

  async function remove(id: string) {
    Alert.alert("Delete bet?", "This can't be undone.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await api.deleteBet(id);
            onRefresh();
          } catch (e) {
            Alert.alert("Couldn't delete", e instanceof Error ? e.message : "Try again.");
          }
        },
      },
    ]);
  }

  if (loading) {
    return (
      <Screen>
        <Loading />
      </Screen>
    );
  }

  if (!isAuthenticated) {
    return (
      <Screen>
        <Txt variant="h1">My Bets</Txt>
        <EmptyState
          title="Sign in to track your bets"
          subtitle="Log straights and parlays in units or dollars, auto-grade results, and measure your closing-line value."
        />
        <Button label="Sign in" onPress={() => router.push("/login")} />
      </Screen>
    );
  }

  return (
    <Screen onRefresh={onRefresh} refreshing={bets.isRefetching || profile.isRefetching}>
      <View style={styles.headerRow}>
        <Txt variant="h1">My Bets</Txt>
        <Button label="Settle" variant="secondary" onPress={settle} style={{ height: 40, paddingHorizontal: 16 }} />
      </View>

      {profile.data ? <ProfileSummary p={profile.data} /> : null}

      <Segmented options={FILTERS} value={filter} onChange={setFilter} />

      {bets.isLoading ? (
        <Loading />
      ) : bets.error ? (
        <ErrorState message="Couldn't load bets." />
      ) : (bets.data ?? []).length === 0 ? (
        <EmptyState
          title="No bets here yet"
          subtitle="Tap a price on the Odds board to track your first bet."
        />
      ) : (
        (bets.data ?? []).map((b) => <BetCard key={b.id} bet={b} onDelete={() => remove(b.id)} />)
      )}
    </Screen>
  );
}

function ProfileSummary({ p }: { p: BetProfile }) {
  const profitPositive = p.profit_units >= 0;
  return (
    <Card>
      <View style={styles.statGrid}>
        <Stat
          label="Profit"
          value={`${signed(p.profit_units, 2)}u`}
          color={profitPositive ? colors.positive : colors.negative}
        />
        <Stat
          label="ROI"
          value={p.roi_pct != null ? `${p.roi_pct.toFixed(1)}%` : "—"}
          color={(p.roi_pct ?? 0) >= 0 ? colors.positive : colors.negative}
        />
        <Stat label="Record" value={`${p.won}-${p.lost}${p.push ? `-${p.push}` : ""}`} />
        <Stat
          label="Win rate"
          value={p.win_rate != null ? `${(p.win_rate * 100).toFixed(0)}%` : "—"}
        />
        <Stat
          label="Avg CLV"
          value={p.avg_clv_pct != null ? `${signed(p.avg_clv_pct, 1)}%` : "—"}
          color={(p.avg_clv_pct ?? 0) >= 0 ? colors.positive : colors.negative}
        />
        <Stat
          label="Beat close"
          value={p.beat_close_pct != null ? `${p.beat_close_pct.toFixed(0)}%` : "—"}
        />
      </View>
      <Txt variant="muted" style={{ marginTop: spacing.sm, fontSize: 12 }}>
        {p.settled} settled · {p.pending} pending · {num(p.staked_units, 1)}u staked · {num(p.open_risk_units, 1)}u at risk
      </Txt>
    </Card>
  );
}

function BetCard({ bet, onDelete }: { bet: Bet; onDelete: () => void }) {
  const tone =
    bet.status === "won"
      ? "positive"
      : bet.status === "lost"
        ? "negative"
        : bet.status === "push" || bet.status === "void"
          ? "neutral"
          : "info";

  return (
    <Card>
      <View style={styles.betHead}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
          <Pill label={bet.status.toUpperCase()} tone={tone as never} />
          <Txt variant="muted" style={{ fontSize: 12 }}>
            {bet.bet_type === "parlay" ? `${bet.legs.length}-leg parlay` : "Straight"} · {shortDate(bet.placed_at)}
          </Txt>
        </View>
        <Txt variant="mono" style={{ color: colors.accent }}>{americanOdds(bet.odds_american)}</Txt>
      </View>

      <View style={styles.legs}>
        {bet.legs.map((leg) => (
          <View key={leg.id} style={styles.legRow}>
            <TeamLogo teamId={leg.home_team_id} size={18} />
            <Txt style={{ flex: 1, color: colors.text }} numberOfLines={1}>
              {leg.selection_label}
            </Txt>
            {leg.clv_pct != null ? (
              <Txt
                variant="mono"
                style={{ fontSize: 11, color: leg.beat_close ? colors.positive : colors.negative }}
              >
                CLV {signed(leg.clv_pct, 1)}%
              </Txt>
            ) : null}
          </View>
        ))}
      </View>

      <View style={styles.betFooter}>
        <Txt variant="muted" style={{ fontSize: 12 }}>
          {num(bet.stake_units, 1)}u
          {bet.stake_dollars != null ? ` ($${num(bet.stake_dollars, 0)})` : ""}
          {bet.result_units != null ? ` · ${signed(bet.result_units, 2)}u` : ""}
        </Txt>
        <Pressable onPress={onDelete} hitSlop={8}>
          <Txt style={{ color: colors.negative, fontSize: 12 }}>Delete</Txt>
        </Pressable>
      </View>
    </Card>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.stat}>
      <Txt variant="label" style={{ fontSize: 10 }}>
        {label}
      </Txt>
      <Txt style={[styles.statValue, color ? { color } : null]}>{value}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  statGrid: { flexDirection: "row", flexWrap: "wrap" },
  stat: { width: "33.3%", paddingVertical: 6, gap: 2 },
  statValue: { color: colors.text, fontWeight: "800", fontSize: 18 },
  betHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  legs: {
    marginVertical: spacing.sm,
    gap: 6,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: spacing.sm,
  },
  legRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  betFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
});
