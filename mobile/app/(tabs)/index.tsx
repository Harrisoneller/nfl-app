import { useCallback } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/context/AuthProvider";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Loading, ErrorState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, radius, spacing } from "@/theme/theme";
import { kickoff, relativeTime } from "@/lib/format";

const QUICK = [
  { label: "Players", icon: "people", route: "/players" },
  { label: "Compare", icon: "git-compare", route: "/compare" },
  { label: "Fantasy", icon: "american-football", route: "/fantasy" },
  { label: "Ask AI", icon: "sparkles", route: "/ai" },
] as const;

export default function HomeScreen() {
  const { user, isAuthenticated } = useAuth();
  const scores = useApi(() => api.scoreboard(16), []);
  const news = useApi(() => api.news(8), []);

  const onRefresh = useCallback(() => {
    scores.refetch();
    news.refetch();
  }, [scores, news]);

  return (
    <Screen
      onRefresh={onRefresh}
      refreshing={scores.isRefetching || news.isRefetching}
    >
      <View style={styles.headerRow}>
        <View>
          <Txt variant="h1">Statletics</Txt>
          <Txt variant="muted">
            {isAuthenticated ? `Welcome back, ${user?.display_name || "fan"}` : "All things NFL, in one place"}
          </Txt>
        </View>
        <Pressable
          onPress={() => router.push(isAuthenticated ? "/account" : "/login")}
          hitSlop={10}
        >
          <Ionicons
            name={isAuthenticated ? "person-circle" : "log-in-outline"}
            size={32}
            color={colors.accent}
          />
        </Pressable>
      </View>

      {/* Quick links */}
      <View style={styles.quickGrid}>
        {QUICK.map((q) => (
          <Pressable
            key={q.label}
            style={styles.quick}
            onPress={() => router.push(q.route as never)}
          >
            <Ionicons name={q.icon as never} size={22} color={colors.accent} />
            <Txt style={styles.quickLabel}>{q.label}</Txt>
          </Pressable>
        ))}
      </View>

      {/* Scores */}
      <Txt variant="label" style={styles.sectionLabel}>
        Scores & schedule
      </Txt>
      {scores.isLoading ? (
        <Loading />
      ) : scores.error ? (
        <ErrorState message="Couldn't load scores." />
      ) : (
        (scores.data ?? []).slice(0, 8).map((g) => (
          <Card key={g.id} padded>
            <View style={styles.gameRow}>
              <View style={styles.gameTeams}>
                <Team teamId={g.away_team_id} score={g.away_score} />
                <Team teamId={g.home_team_id} score={g.home_score} />
              </View>
              <View style={styles.gameMeta}>
                <Pill
                  label={statusLabel(g)}
                  tone={g.status === "in" ? "negative" : "neutral"}
                />
                {g.start_time ? (
                  <Txt variant="muted" style={{ marginTop: 4 }}>
                    {kickoff(g.start_time)}
                  </Txt>
                ) : null}
              </View>
            </View>
          </Card>
        ))
      )}

      {/* News */}
      <Txt variant="label" style={styles.sectionLabel}>
        Latest news
      </Txt>
      {news.isLoading ? (
        <Loading />
      ) : (
        (news.data ?? []).slice(0, 8).map((n) => (
          <Card key={n.id} padded>
            <Txt variant="h3" numberOfLines={2}>
              {n.title}
            </Txt>
            <View style={styles.newsMeta}>
              <Pill label={n.source_label || n.source} tone="info" />
              <Txt variant="muted">{relativeTime(n.published_at)}</Txt>
            </View>
            {n.summary ? (
              <Txt variant="muted" numberOfLines={2} style={{ marginTop: 6 }}>
                {n.summary}
              </Txt>
            ) : null}
          </Card>
        ))
      )}
    </Screen>
  );
}

function Team({
  teamId,
  score,
}: {
  teamId: string | null;
  score: number | null;
}) {
  return (
    <View style={styles.teamLine}>
      <TeamLogo teamId={teamId} size={24} />
      <Txt style={styles.teamName}>{teamId ?? "TBD"}</Txt>
      <Txt variant="mono" style={styles.score}>
        {score ?? "–"}
      </Txt>
    </View>
  );
}

function statusLabel(g: { status: string; status_detail: string }): string {
  if (g.status === "post") return "Final";
  if (g.status === "in") return g.status_detail || "Live";
  return g.status_detail || "Scheduled";
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  quickGrid: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  quick: {
    flex: 1,
    backgroundColor: colors.panel,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    gap: 6,
  },
  quickLabel: { fontSize: 12, color: colors.text, fontWeight: "600" },
  sectionLabel: { marginTop: spacing.sm },
  gameRow: { flexDirection: "row", justifyContent: "space-between" },
  gameTeams: { gap: 8, flex: 1 },
  gameMeta: { alignItems: "flex-end", justifyContent: "center" },
  teamLine: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  teamName: { flex: 1, color: colors.text, fontWeight: "600" },
  score: { fontSize: 16 },
  newsMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: 6,
  },
});
