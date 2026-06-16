import { useWindowDimensions, StyleSheet, View } from "react-native";
import { useLocalSearchParams, Stack } from "expo-router";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Loading, ErrorState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { WinProbBar } from "@/components/WinProbBar";
import { MiniLineChart } from "@/components/MiniLineChart";
import { colors, spacing } from "@/theme/theme";
import { americanOdds, pct, num, signed } from "@/lib/format";

export default function SparkyGameDetail() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const id = String(eventId);
  const { width } = useWindowDimensions();

  const detail = useApi(() => api.sparkyGame(id), [id]);
  const d = detail.data;
  const pred = d?.prediction;

  const movementSeries = [
    {
      points: (d?.movement ?? []).map((m) => m.home_prob),
      color: colors.accent,
    },
  ];

  return (
    <>
      <Stack.Screen options={{ title: pred ? `${pred.away_team_id} @ ${pred.home_team_id}` : "Game" }} />
      <Screen onRefresh={detail.refetch} refreshing={detail.isRefetching}>
        {detail.isLoading ? (
          <Loading />
        ) : detail.error || !d ? (
          <ErrorState message="Couldn't load game detail." />
        ) : (
          <>
            {pred ? (
              <Card>
                <View style={styles.head}>
                  <View style={styles.matchup}>
                    <TeamLogo teamId={pred.away_team_id} size={28} />
                    <Txt style={styles.at}>@</Txt>
                    <TeamLogo teamId={pred.home_team_id} size={28} />
                    <Txt style={styles.teams}>
                      {pred.away_team_id} @ {pred.home_team_id}
                    </Txt>
                  </View>
                  {pred.classification ? <Pill label={pred.classification} tone="info" /> : null}
                </View>

                <View style={{ marginVertical: spacing.sm }}>
                  <WinProbBar
                    awayId={pred.away_team_id}
                    homeId={pred.home_team_id}
                    homeWinProb={
                      pred.home_win_prob ??
                      (pred.predicted_winner === pred.home_team_id ? pred.win_prob : 1 - pred.win_prob)
                    }
                  />
                </View>

                <View style={styles.metaRow}>
                  <Meta label="Pick" value={pred.predicted_winner ?? "—"} />
                  <Meta label="Confidence" value={pct(pred.confidence_score)} />
                  <Meta label="Model" value={pct(pred.model_prob)} />
                  <Meta label="Market" value={pct(pred.market_prob)} />
                </View>

                {pred.explanation ? (
                  <Txt variant="muted" style={{ marginTop: spacing.sm }}>
                    {pred.explanation}
                  </Txt>
                ) : null}
              </Card>
            ) : null}

            {pred?.market ? (
              <Card>
                <Txt variant="label">Market consensus ({pred.market.book_count} books)</Txt>
                <View style={styles.metaRow}>
                  <Meta label="Spread" value={signed(pred.market.spread_home)} />
                  <Meta label="Total" value={num(pred.market.total)} />
                  <Meta label="Home ML" value={americanOdds(pred.market.home_ml)} />
                  <Meta label="Away ML" value={americanOdds(pred.market.away_ml)} />
                </View>
              </Card>
            ) : null}

            {pred?.signals?.length ? (
              <Card>
                <Txt variant="label">Signals</Txt>
                {pred.signals.map((s) => (
                  <View key={s.key} style={styles.signalRow}>
                    <Pill
                      label={s.side}
                      tone={s.severity === "bullish" ? "positive" : s.severity === "warning" ? "warning" : "info"}
                    />
                    <View style={{ flex: 1 }}>
                      <Txt style={{ color: colors.text, fontWeight: "600" }}>{s.label}</Txt>
                      <Txt variant="muted" style={{ fontSize: 12 }}>
                        {s.explanation}
                      </Txt>
                    </View>
                  </View>
                ))}
              </Card>
            ) : null}

            {d.movement?.length ? (
              <Card>
                <Txt variant="label">Line movement (home win prob)</Txt>
                <MiniLineChart series={movementSeries} width={width - spacing.lg * 4} height={130} />
              </Card>
            ) : null}

            {d.books?.length ? (
              <Card>
                <Txt variant="label">By book</Txt>
                {d.books.map((b, i) => (
                  <View key={i} style={styles.bookRow}>
                    <Txt style={{ flex: 1, color: colors.text }}>{b.book}</Txt>
                    <Txt variant="mono" style={{ width: 56, textAlign: "right" }}>
                      {signed(b.home_spread)}
                    </Txt>
                    <Txt variant="mono" style={{ width: 48, textAlign: "right" }}>
                      {num(b.total)}
                    </Txt>
                    <Txt variant="mono" style={{ width: 56, textAlign: "right" }}>
                      {americanOdds(b.home_ml)}
                    </Txt>
                  </View>
                ))}
              </Card>
            ) : null}
          </>
        )}
      </Screen>
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ gap: 2 }}>
      <Txt variant="label" style={{ fontSize: 10 }}>
        {label}
      </Txt>
      <Txt style={{ color: colors.text, fontWeight: "800", fontSize: 15 }}>{value}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  matchup: { flexDirection: "row", alignItems: "center", gap: 6, flex: 1 },
  at: { color: colors.mutedDim },
  teams: { color: colors.text, fontWeight: "700", marginLeft: 4 },
  metaRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.lg, marginTop: 4 },
  signalRow: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm, paddingVertical: 6 },
  bookRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: 4 },
});
