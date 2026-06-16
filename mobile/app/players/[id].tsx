import { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import { useLocalSearchParams, Stack } from "expo-router";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { playerMetricLabel, playerMetricFmt } from "@/lib/metrics";
import { teamColor } from "@/lib/team-colors";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Loading, ErrorState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { StatRow } from "@/components/StatRow";
import { colors, spacing } from "@/theme/theme";
import { num, gradeColor } from "@/lib/format";

export default function PlayerDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const playerId = String(id);

  const player = useApi(() => api.getPlayer(playerId), [playerId]);
  const profile = useApi(() => api.getPlayerProfile(playerId), [playerId]);
  const projection = useApi(() => api.playerSeasonProjection(playerId), [playerId]);
  const games = useApi(() => api.playerGamePredictions(playerId), [playerId]);

  const accent = teamColor(player.data?.team_id);

  const metricEntries = useMemo(
    () => Object.entries(profile.data?.metrics ?? {}),
    [profile.data],
  );
  const projEntries = useMemo(
    () => Object.entries(projection.data?.stats ?? {}),
    [projection.data],
  );

  return (
    <>
      <Stack.Screen options={{ title: player.data?.full_name ?? "Player" }} />
      <Screen onRefresh={profile.refetch} refreshing={profile.isRefetching}>
        <Card accent={accent}>
          <View style={styles.headerRow}>
            <TeamLogo teamId={player.data?.team_id} size={44} />
            <View style={{ flex: 1 }}>
              <Txt variant="h2">{player.data?.full_name ?? "Player"}</Txt>
              <Txt variant="muted">
                {player.data?.position}
                {player.data?.team_id ? ` · ${player.data.team_id}` : ""}
                {player.data?.jersey_number ? ` · #${player.data.jersey_number}` : ""}
              </Txt>
            </View>
          </View>
          {player.data ? (
            <Txt variant="muted" style={{ marginTop: spacing.sm }}>
              {[player.data.height, player.data.weight ? `${player.data.weight} lb` : null, player.data.age ? `${player.data.age} yo` : null, player.data.college]
                .filter(Boolean)
                .join(" · ")}
            </Txt>
          ) : null}
        </Card>

        {profile.isLoading ? (
          <Loading />
        ) : profile.error ? (
          <ErrorState message="Couldn't load player profile." />
        ) : (
          <Card>
            <Txt variant="label">
              Profile percentiles{profile.data?.peer_count ? ` (vs ${profile.data.peer_count} peers)` : ""}
            </Txt>
            {metricEntries.length === 0 ? (
              <Txt variant="muted">No profile metrics available.</Txt>
            ) : (
              metricEntries.map(([key, m]) => (
                <StatRow
                  key={key}
                  label={playerMetricLabel(key)}
                  value={playerMetricFmt(key, m.value)}
                  percentile={m.percentile}
                  higherIsBetter={m.higher_is_better}
                />
              ))
            )}
          </Card>
        )}

        {projEntries.length ? (
          <Card>
            <Txt variant="label">Season projection</Txt>
            {projEntries.map(([key, s]) => (
              <View key={key} style={styles.projRow}>
                <Txt style={{ flex: 1, color: colors.text }}>{playerMetricLabel(key)}</Txt>
                <Txt variant="muted" style={{ width: 70, textAlign: "right" }}>
                  {num(s.ytd, 0)} ytd
                </Txt>
                <Txt variant="mono" style={{ width: 70, textAlign: "right" }}>
                  {num(s.projected_final, 0)}
                </Txt>
              </View>
            ))}
          </Card>
        ) : null}

        {games.data?.games?.length ? (
          <Card>
            <Txt variant="label">Upcoming matchups</Txt>
            {games.data.games.slice(0, 6).map((g, i) => (
              <View key={i} style={styles.gameRow}>
                <Txt variant="muted" style={{ width: 38 }}>W{g.week ?? "—"}</Txt>
                <TeamLogo teamId={g.opponent} size={20} />
                <Txt style={{ flex: 1, color: colors.text }}>
                  {g.is_home ? "vs" : "@"} {g.opponent}
                </Txt>
                <Pill label={`Matchup ${g.matchup_grade}`} color={gradeColor(g.matchup_grade)} />
              </View>
            ))}
          </Card>
        ) : null}
      </Screen>
    </>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  projRow: { flexDirection: "row", alignItems: "center", paddingVertical: 5, gap: spacing.sm },
  gameRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: 5 },
});
