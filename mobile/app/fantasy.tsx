import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Segmented } from "@/components/ui/Segmented";
import { Loading, EmptyState } from "@/components/ui/States";
import { colors, spacing } from "@/theme/theme";
import { relativeTime } from "@/lib/format";

type Tab = "add" | "drop" | "news";
const TABS = [
  { id: "add" as const, label: "Trending Add" },
  { id: "drop" as const, label: "Trending Drop" },
  { id: "news" as const, label: "News" },
];

export default function FantasyScreen() {
  const [tab, setTab] = useState<Tab>("add");

  const trending = useApi(
    () => api.fantasyTrending(tab === "drop" ? "drop" : "add", 25),
    [tab],
    { enabled: tab !== "news" },
  );
  const news = useApi(() => api.fantasyNews(25), [], { enabled: tab === "news" });

  return (
    <Screen
      onRefresh={tab === "news" ? news.refetch : trending.refetch}
      refreshing={trending.isRefetching || news.isRefetching}
    >
      <Segmented options={TABS} value={tab} onChange={setTab} />

      {tab === "news" ? (
        news.isLoading ? (
          <Loading />
        ) : (news.data ?? []).length === 0 ? (
          <EmptyState title="No fantasy news" />
        ) : (
          (news.data ?? []).map((n) => (
            <Card key={n.id}>
              <Txt variant="h3" numberOfLines={2}>
                {n.title}
              </Txt>
              <View style={styles.metaRow}>
                <Pill label={n.source_label || n.source} tone="info" />
                <Txt variant="muted">{relativeTime(n.published_at)}</Txt>
              </View>
              {n.summary ? (
                <Txt variant="muted" numberOfLines={3} style={{ marginTop: 6 }}>
                  {n.summary}
                </Txt>
              ) : null}
            </Card>
          ))
        )
      ) : trending.isLoading ? (
        <Loading />
      ) : (trending.data?.items ?? []).length === 0 ? (
        <EmptyState title="No trending players" subtitle="Check back closer to game week." />
      ) : (
        (trending.data?.items ?? []).map((it, i) => (
          <Card key={i}>
            <View style={styles.row}>
              <Txt style={styles.rank}>{i + 1}</Txt>
              <View style={{ flex: 1 }}>
                <Txt variant="h3">{trendingName(it)}</Txt>
                {trendingMeta(it) ? <Txt variant="muted">{trendingMeta(it)}</Txt> : null}
              </View>
              {trendingCount(it) != null ? (
                <Pill label={`${trendingCount(it)}`} tone={tab === "add" ? "positive" : "negative"} />
              ) : null}
            </View>
          </Card>
        ))
      )}
    </Screen>
  );
}

// Trending items are loosely typed (`any`) from the backend; pull common fields
// defensively so we render something useful regardless of exact shape.
function trendingName(it: any): string {
  return it?.name || it?.full_name || it?.player_name || it?.player || "Player";
}
function trendingMeta(it: any): string {
  return [it?.position, it?.team, it?.team_id].filter(Boolean).join(" · ");
}
function trendingCount(it: any): number | null {
  const c = it?.count ?? it?.adds ?? it?.drops ?? it?.transactions;
  return typeof c === "number" ? c : null;
}

const styles = StyleSheet.create({
  metaRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginTop: 6 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  rank: { width: 26, textAlign: "center", color: colors.muted, fontWeight: "800", fontSize: 16 },
});
