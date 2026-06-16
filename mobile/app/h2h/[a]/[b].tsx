import { StyleSheet, View } from "react-native";
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
import { colors, spacing } from "@/theme/theme";
import { num, signed, gradeColor } from "@/lib/format";

export default function H2HScreen() {
  const { a, b } = useLocalSearchParams<{ a: string; b: string }>();
  const teamA = String(a);
  const teamB = String(b);

  const h2h = useApi(() => api.h2h(teamA, teamB), [teamA, teamB]);
  const data = h2h.data;

  return (
    <>
      <Stack.Screen options={{ title: `${teamA} vs ${teamB}` }} />
      <Screen onRefresh={h2h.refetch} refreshing={h2h.isRefetching}>
        {h2h.isLoading ? (
          <Loading />
        ) : h2h.error || !data || data.error ? (
          <ErrorState message={data?.error ?? "Couldn't load matchup."} />
        ) : (
          <>
            <Card>
              <View style={styles.vsRow}>
                <TeamSide id={teamA} grade={data.grade.a} elo={data.elo.a} />
                <Txt style={styles.vs}>vs</Txt>
                <TeamSide id={teamB} grade={data.grade.b} elo={data.elo.b} alignRight />
              </View>
            </Card>

            {data.predicted_matchup ? (
              <Card>
                <Txt variant="label">
                  Predicted matchup
                  {data.predicted_matchup.hypothetical ? " (hypothetical)" : ""}
                </Txt>
                <View style={{ marginVertical: spacing.sm }}>
                  <WinProbBar
                    awayId={data.predicted_matchup.away_team}
                    homeId={data.predicted_matchup.home_team}
                    homeWinProb={data.predicted_matchup.prediction.home_win_prob}
                  />
                </View>
                <View style={styles.metaRow}>
                  <Meta label="Spread" value={signed(data.predicted_matchup.prediction.predicted_spread)} />
                  <Meta label="Total" value={num(data.predicted_matchup.prediction.predicted_total)} />
                  <Meta
                    label="Score"
                    value={`${Math.round(data.predicted_matchup.prediction.predicted_away_score)}-${Math.round(
                      data.predicted_matchup.prediction.predicted_home_score,
                    )}`}
                  />
                </View>
                {data.predicted_matchup.prediction.confidence_tier ? (
                  <Pill
                    label={`${data.predicted_matchup.prediction.confidence_tier} confidence`}
                    tone="info"
                    style={{ marginTop: spacing.sm }}
                  />
                ) : null}
              </Card>
            ) : null}

            {data.decision_metrics?.length ? (
              <Card>
                <Txt variant="label">Decision metrics</Txt>
                {data.decision_metrics.map((m) => (
                  <View key={m.key} style={styles.row}>
                    <Txt style={{ flex: 1, color: colors.text }}>{m.label}</Txt>
                    {m.favored ? <Pill label={m.favored} tone="brand" /> : null}
                    <Txt variant="mono" style={{ width: 64, textAlign: "right" }}>
                      {m.value != null ? num(m.value, 1) : "—"}
                    </Txt>
                  </View>
                ))}
              </Card>
            ) : null}

            {data.profile?.deltas?.length ? (
              <Card>
                <Txt variant="label">Statistical edges</Txt>
                {data.profile.deltas.slice(0, 12).map((d, i) => (
                  <View key={i} style={styles.row}>
                    <Txt style={{ flex: 1, color: colors.text }} numberOfLines={1}>
                      {d.metric}
                    </Txt>
                    <Txt variant="muted" style={{ width: 56, textAlign: "right" }}>
                      {num(d.a_value, 2)}
                    </Txt>
                    <Pill
                      label={d.winner === "a" ? teamA : d.winner === "b" ? teamB : "—"}
                      tone={d.winner ? "positive" : "neutral"}
                    />
                    <Txt variant="muted" style={{ width: 56, textAlign: "right" }}>
                      {num(d.b_value, 2)}
                    </Txt>
                  </View>
                ))}
              </Card>
            ) : null}

            {data.history?.games?.length ? (
              <Card>
                <Txt variant="label">
                  Recent meetings · {data.history.a_wins}-{data.history.b_wins}
                  {data.history.ties ? `-${data.history.ties}` : ""}
                </Txt>
                {data.history.games.slice(0, 8).map((g, i) => (
                  <View key={i} style={styles.row}>
                    <Txt variant="muted" style={{ width: 64 }}>
                      {g.season} W{g.week ?? "—"}
                    </Txt>
                    <Txt style={{ flex: 1, color: colors.text }}>
                      {g.away_team} @ {g.home_team}
                    </Txt>
                    <Txt variant="mono">
                      {g.away_score}-{g.home_score}
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

function TeamSide({
  id,
  grade,
  elo,
  alignRight = false,
}: {
  id: string;
  grade: string;
  elo: number;
  alignRight?: boolean;
}) {
  return (
    <View style={[styles.teamSide, alignRight && { alignItems: "flex-end" }]}>
      <TeamLogo teamId={id} size={44} />
      <Txt variant="h3">{id}</Txt>
      <View style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
        <Pill label={grade} color={gradeColor(grade)} />
        <Txt variant="muted">Elo {Math.round(elo)}</Txt>
      </View>
    </View>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ gap: 2 }}>
      <Txt variant="label" style={{ fontSize: 10 }}>
        {label}
      </Txt>
      <Txt style={{ color: colors.text, fontWeight: "800", fontSize: 16 }}>{value}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  vsRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  vs: { color: colors.mutedDim, fontWeight: "700" },
  teamSide: { gap: 6, alignItems: "flex-start", flex: 1 },
  metaRow: { flexDirection: "row", gap: spacing.xl, marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: 5 },
});
