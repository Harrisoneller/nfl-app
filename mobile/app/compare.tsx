import { useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { teamMetricLabel, teamMetricFmt } from "@/lib/metrics";
import { teamColor } from "@/lib/team-colors";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Loading } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { colors, radius, spacing } from "@/theme/theme";

/**
 * Team-vs-team comparison. Picks two teams and shows their profile metrics
 * side by side, highlighting the better percentile per row.
 */
export default function CompareScreen() {
  const teams = useApi(() => api.listTeams(), []);
  const [a, setA] = useState("PHI");
  const [b, setB] = useState("SF");

  const profA = useApi(() => api.getTeamProfile(a), [a]);
  const profB = useApi(() => api.getTeamProfile(b), [b]);

  const rows = useMemo(() => {
    const ma = profA.data?.metrics ?? {};
    const mb = profB.data?.metrics ?? {};
    const keys = Array.from(new Set([...Object.keys(ma), ...Object.keys(mb)]));
    return keys.map((k) => ({
      key: k,
      a: ma[k],
      b: mb[k],
    }));
  }, [profA.data, profB.data]);

  return (
    <Screen>
      <Txt variant="muted">Pick two teams to compare their season profiles.</Txt>

      <TeamPicker
        label="Team A"
        teams={(teams.data ?? []).map((t) => t.id)}
        value={a}
        onChange={setA}
        color={teamColor(a)}
      />
      <TeamPicker
        label="Team B"
        teams={(teams.data ?? []).map((t) => t.id)}
        value={b}
        onChange={setB}
        color={teamColor(b)}
      />

      <Card>
        <View style={styles.headRow}>
          <View style={styles.headTeam}>
            <TeamLogo teamId={a} size={28} />
            <Txt variant="h3">{a}</Txt>
          </View>
          <Txt variant="label">Metric</Txt>
          <View style={[styles.headTeam, { justifyContent: "flex-end" }]}>
            <Txt variant="h3">{b}</Txt>
            <TeamLogo teamId={b} size={28} />
          </View>
        </View>

        {profA.isLoading || profB.isLoading ? (
          <Loading />
        ) : (
          rows.map((r) => {
            const aPct = r.a?.percentile ?? null;
            const bPct = r.b?.percentile ?? null;
            const aWins = aPct != null && bPct != null && aPct > bPct;
            const bWins = aPct != null && bPct != null && bPct > aPct;
            return (
              <View key={r.key} style={styles.row}>
                <Txt
                  variant="mono"
                  style={[styles.cell, { textAlign: "left" }, aWins && styles.win]}
                >
                  {r.a ? teamMetricFmt(r.key, r.a.value) : "—"}
                </Txt>
                <Txt variant="muted" style={styles.metric} numberOfLines={2}>
                  {teamMetricLabel(r.key)}
                </Txt>
                <Txt
                  variant="mono"
                  style={[styles.cell, { textAlign: "right" }, bWins && styles.win]}
                >
                  {r.b ? teamMetricFmt(r.key, r.b.value) : "—"}
                </Txt>
              </View>
            );
          })
        )}
      </Card>
    </Screen>
  );
}

function TeamPicker({
  label,
  teams,
  value,
  onChange,
  color,
}: {
  label: string;
  teams: string[];
  value: string;
  onChange: (id: string) => void;
  color: string;
}) {
  return (
    <View style={{ gap: 6 }}>
      <Txt variant="label">{label}</Txt>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {teams.map((t) => {
          const active = t === value;
          return (
            <Pressable
              key={t}
              onPress={() => onChange(t)}
              style={[
                styles.chip,
                active && { backgroundColor: color, borderColor: color },
              ]}
            >
              <Txt style={[styles.chipText, active && { color: "#fff" }]}>{t}</Txt>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  chips: { gap: 6, paddingVertical: 2 },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.panelAlt,
  },
  chipText: { color: colors.muted, fontWeight: "700", fontSize: 13 },
  headRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: spacing.sm,
  },
  headTeam: { flexDirection: "row", alignItems: "center", gap: 6, flex: 1 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 6,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  cell: { width: 70, color: colors.text },
  metric: { flex: 1, textAlign: "center", fontSize: 11 },
  win: { color: colors.positive, fontWeight: "800" },
});
