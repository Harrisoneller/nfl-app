import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Input } from "@/components/ui/Input";
import { Segmented } from "@/components/ui/Segmented";
import { Loading, EmptyState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, spacing } from "@/theme/theme";

const POSITIONS = [
  { id: "", label: "All" },
  { id: "QB", label: "QB" },
  { id: "RB", label: "RB" },
  { id: "WR", label: "WR" },
  { id: "TE", label: "TE" },
];

export default function PlayersScreen() {
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState("");

  const players = useApi(
    () => api.listPlayers({ query: query || undefined, position: position || undefined, limit: 60 }),
    [query, position],
  );

  return (
    <Screen onRefresh={players.refetch} refreshing={players.isRefetching}>
      <Input
        placeholder="Search players…"
        value={query}
        onChangeText={setQuery}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <Segmented options={POSITIONS} value={position} onChange={setPosition} />

      {players.isLoading ? (
        <Loading />
      ) : (players.data ?? []).length === 0 ? (
        <EmptyState title="No players found" subtitle="Try a different name or position." />
      ) : (
        (players.data ?? []).map((p) => (
          <Card key={p.id} onPress={() => router.push(`/players/${p.id}`)}>
            <View style={styles.row}>
              <TeamLogo teamId={p.team_id} size={30} />
              <View style={{ flex: 1 }}>
                <Txt variant="h3">{p.full_name}</Txt>
                <Txt variant="muted">
                  {p.position}
                  {p.team_id ? ` · ${p.team_id}` : ""}
                  {p.jersey_number ? ` · #${p.jersey_number}` : ""}
                </Txt>
              </View>
            </View>
          </Card>
        ))
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
});
