import { useMemo, useState } from "react";
import { StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { api, type Team } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Loading, ErrorState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, spacing } from "@/theme/theme";

export default function TeamsScreen() {
  const { data, error, isLoading, isRefetching, refetch } = useApi(
    () => api.listTeams(),
    [],
  );
  const [q, setQ] = useState("");

  const grouped = useMemo(() => {
    const teams = (data ?? []).filter((t) =>
      `${t.full_name} ${t.market} ${t.name} ${t.id}`
        .toLowerCase()
        .includes(q.toLowerCase()),
    );
    const map = new Map<string, Team[]>();
    for (const t of teams) {
      const key = `${t.conference} ${t.division}`;
      const arr = map.get(key) ?? [];
      arr.push(t);
      map.set(key, arr);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data, q]);

  return (
    <Screen onRefresh={refetch} refreshing={isRefetching}>
      <Input
        placeholder="Search teams…"
        value={q}
        onChangeText={setQ}
        autoCapitalize="none"
        autoCorrect={false}
      />

      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorState message="Couldn't load teams." />
      ) : (
        grouped.map(([division, teams]) => (
          <View key={division} style={{ gap: spacing.sm }}>
            <Txt variant="label">{division}</Txt>
            {teams.map((t) => (
              <Card
                key={t.id}
                accent={t.primary_color || undefined}
                onPress={() => router.push(`/teams/${t.id}`)}
              >
                <View style={styles.row}>
                  <TeamLogo teamId={t.id} size={36} />
                  <View style={{ flex: 1 }}>
                    <Txt variant="h3">{t.full_name}</Txt>
                    <Txt variant="muted">{t.id}</Txt>
                  </View>
                </View>
              </Card>
            ))}
          </View>
        ))
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
});
