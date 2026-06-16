import { useMemo, useState } from "react";
import { Alert, Pressable, StyleSheet, View } from "react-native";
import { router } from "expo-router";
import * as Haptics from "expo-haptics";
import { api, type BetLegInput } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/context/AuthProvider";
import {
  groupByEvent,
  bestSpread,
  bestTotal,
  bestMoneyline,
  uniqueBooks,
  impliedProb,
  marketLabel,
  legFor,
  type GroupedGame,
} from "@/lib/odds";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Loading, ErrorState, EmptyState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, radius, spacing } from "@/theme/theme";
import { americanOdds, signed, relativeTime, kickoff } from "@/lib/format";

export default function OddsScreen() {
  const { isAuthenticated } = useAuth();
  const odds = useApi(() => api.odds(undefined, 400), []);
  const status = useApi(() => api.oddsStatus(), []);

  const grouped = useMemo(() => groupByEvent(odds.data ?? []), [odds.data]);

  async function trackLeg(leg: BetLegInput | null) {
    if (!leg) return;
    if (!isAuthenticated) {
      Alert.alert("Sign in required", "Sign in to track bets and see closing-line value.", [
        { text: "Cancel", style: "cancel" },
        { text: "Sign in", onPress: () => router.push("/login") },
      ]);
      return;
    }
    Alert.alert("Track this bet?", `${leg.selection_label} (${americanOdds(leg.odds_american)}) · 1 unit`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Track",
        onPress: async () => {
          try {
            await api.createBet({ bet_type: "straight", stake_units: 1, source: "odds", legs: [leg] });
            Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
            Alert.alert("Tracked", "Added to My Bets.");
          } catch (e) {
            Alert.alert("Couldn't track", e instanceof Error ? e.message : "Try again.");
          }
        },
      },
    ]);
  }

  return (
    <Screen
      onRefresh={() => {
        odds.refetch();
        status.refetch();
      }}
      refreshing={odds.isRefetching}
    >
      <View>
        <Txt variant="h1">Odds board</Txt>
        <Txt variant="muted">Best lines across major sportsbooks. Tap a price to track it.</Txt>
        {status.data?.last_updated ? (
          <Txt variant="muted" style={{ marginTop: 4, fontSize: 12 }}>
            Lines as of {relativeTime(status.data.last_updated)}
          </Txt>
        ) : null}
      </View>

      {odds.isLoading ? (
        <Loading label="Loading lines…" />
      ) : odds.error ? (
        <ErrorState message="Couldn't load odds." />
      ) : grouped.length === 0 ? (
        <EmptyState
          title="No lines posted yet"
          subtitle={
            status.data && !status.data.configured
              ? "The Odds API key isn't configured on the backend."
              : "Lines appear here once books post them (typically as kickoff approaches)."
          }
        />
      ) : (
        grouped.map((g) => <GameCard key={g.eventId} game={g} onTrack={trackLeg} />)
      )}
    </Screen>
  );
}

function GameCard({
  game,
  onTrack,
}: {
  game: GroupedGame;
  onTrack: (leg: BetLegInput | null) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const spread = bestSpread(game);
  const total = bestTotal(game);
  const ml = bestMoneyline(game);

  return (
    <Card>
      <View style={styles.cardHead}>
        <View style={{ gap: 6, flex: 1 }}>
          <Side teamId={game.awayId} label={game.awayName} />
          <Side teamId={game.homeId} label={game.homeName} />
        </View>
        <Txt variant="muted" style={{ fontSize: 11, textAlign: "right" }}>
          {kickoff(game.kickoff)}
        </Txt>
      </View>

      <MarketRow label="Spread">
        {spread ? (
          <View style={styles.pairRow}>
            <PriceBox
              title={game.awayId ?? game.awayName}
              primary={signed(spread.away.point)}
              secondary={americanOdds(spread.away.price)}
              onPress={() => onTrack(legFor(game, "spread", game.awayId, spread.away.point, spread.away.price))}
            />
            <PriceBox
              title={game.homeId ?? game.homeName}
              primary={signed(spread.home.point)}
              secondary={americanOdds(spread.home.price)}
              onPress={() => onTrack(legFor(game, "spread", game.homeId, spread.home.point, spread.home.price))}
            />
          </View>
        ) : (
          <Txt variant="muted">No line</Txt>
        )}
      </MarketRow>

      <MarketRow label="Total">
        {total ? (
          <View style={styles.pairRow}>
            <PriceBox
              title="Over"
              primary={total.point != null ? `O ${total.point}` : "—"}
              secondary={americanOdds(total.overPrice)}
              onPress={() => onTrack(legFor(game, "total", "over", total.point, total.overPrice))}
            />
            <PriceBox
              title="Under"
              primary={total.point != null ? `U ${total.point}` : "—"}
              secondary={americanOdds(total.underPrice)}
              onPress={() => onTrack(legFor(game, "total", "under", total.point, total.underPrice))}
            />
          </View>
        ) : (
          <Txt variant="muted">No line</Txt>
        )}
      </MarketRow>

      <MarketRow label="Moneyline">
        {ml ? (
          <View style={styles.pairRow}>
            <PriceBox
              title={game.awayId ?? game.awayName}
              primary={americanOdds(ml.away)}
              secondary={impliedProb(ml.away)}
              onPress={() => onTrack(legFor(game, "moneyline", game.awayId, null, ml.away))}
            />
            <PriceBox
              title={game.homeId ?? game.homeName}
              primary={americanOdds(ml.home)}
              secondary={impliedProb(ml.home)}
              onPress={() => onTrack(legFor(game, "moneyline", game.homeId, null, ml.home))}
            />
          </View>
        ) : (
          <Txt variant="muted">No line</Txt>
        )}
      </MarketRow>

      <View style={styles.footer}>
        <Pressable onPress={() => setExpanded((e) => !e)}>
          <Txt variant="muted" style={styles.linkText}>
            {expanded ? "Hide" : "See"} all books ({uniqueBooks(game.lines)})
          </Txt>
        </Pressable>
        {game.homeId && game.awayId ? (
          <Pressable onPress={() => router.push(`/h2h/${game.awayId}/${game.homeId}`)}>
            <Txt style={[styles.linkText, { color: colors.accent }]}>Matchup →</Txt>
          </Pressable>
        ) : null}
      </View>

      {expanded ? (
        <View style={styles.allBooks}>
          {game.lines.map((l) => (
            <View key={l.id} style={styles.bookRow}>
              <Txt variant="muted" style={{ flex: 1.2 }}>{l.bookmaker}</Txt>
              <Txt variant="muted" style={{ flex: 1 }}>{marketLabel(l.market)}</Txt>
              <Txt variant="muted" style={{ flex: 1 }}>{l.label}</Txt>
              <Txt variant="mono" style={{ width: 44, textAlign: "right" }}>
                {l.point ?? "—"}
              </Txt>
              <Txt variant="mono" style={{ width: 52, textAlign: "right" }}>
                {l.price != null ? americanOdds(l.price) : "—"}
              </Txt>
            </View>
          ))}
        </View>
      ) : null}
    </Card>
  );
}

function Side({ teamId, label }: { teamId: string | null; label: string }) {
  return (
    <View style={styles.sideLine}>
      <TeamLogo teamId={teamId} size={22} />
      <Txt style={styles.sideName} numberOfLines={1}>
        {label}
      </Txt>
    </View>
  );
}

function MarketRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.marketRow}>
      <Txt variant="label" style={{ width: 78 }}>
        {label}
      </Txt>
      <View style={{ flex: 1 }}>{children}</View>
    </View>
  );
}

function PriceBox({
  title,
  primary,
  secondary,
  onPress,
}: {
  title: string;
  primary: string;
  secondary: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.priceBox, pressed && { opacity: 0.6 }]}
    >
      <Txt variant="muted" style={{ fontSize: 11 }} numberOfLines={1}>
        {title}
      </Txt>
      <Txt style={styles.pricePrimary}>{primary}</Txt>
      <Txt variant="muted" style={{ fontSize: 10 }} numberOfLines={1}>
        {secondary}
      </Txt>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  cardHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  sideLine: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  sideName: { color: colors.text, fontWeight: "600", flexShrink: 1 },
  marketRow: { flexDirection: "row", alignItems: "center", marginVertical: 5 },
  pairRow: { flexDirection: "row", gap: spacing.sm },
  priceBox: {
    flex: 1,
    backgroundColor: colors.panelAlt,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.sm,
    paddingVertical: 8,
    paddingHorizontal: 10,
    gap: 2,
  },
  pricePrimary: { color: colors.text, fontWeight: "700", fontSize: 15 },
  footer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.md,
  },
  linkText: { fontSize: 12, textDecorationLine: "underline" },
  allBooks: {
    marginTop: spacing.md,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: spacing.sm,
    gap: 4,
  },
  bookRow: { flexDirection: "row", alignItems: "center", gap: 4 },
});
