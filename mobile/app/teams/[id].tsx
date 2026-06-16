import { useMemo, useState } from "react";
import { StyleSheet, useWindowDimensions, View } from "react-native";
import { router, useLocalSearchParams, Stack } from "expo-router";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { teamMetricLabel, teamMetricFmt } from "@/lib/metrics";
import { teamColor } from "@/lib/team-colors";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Segmented } from "@/components/ui/Segmented";
import { Loading, ErrorState, EmptyState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { StatRow } from "@/components/StatRow";
import { SeasonSelect } from "@/components/SeasonSelect";
import { MiniLineChart } from "@/components/MiniLineChart";
import { colors, spacing } from "@/theme/theme";
import { num, kickoff, gradeColor } from "@/lib/format";

type Tab = "overview" | "roster" | "schedule";
const TABS = [
  { id: "overview" as const, label: "Overview" },
  { id: "roster" as const, label: "Roster" },
  { id: "schedule" as const, label: "Schedule" },
];

export default function TeamDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const teamId = String(id);
  const { width } = useWindowDimensions();
  const [tab, setTab] = useState<Tab>("overview");
  const [season, setSeason] = useState<number | undefined>(undefined);

  const team = useApi(() => api.getTeam(teamId), [teamId]);
  const profile = useApi(() => api.getTeamProfile(teamId, season), [teamId, season]);
  const outlook = useApi(() => api.teamSeasonOutlook(teamId, season), [teamId, season]);
  const elo = useApi(() => api.teamEloHistory(teamId), [teamId]);
  const roster = useApi(() => api.getTeamRoster(teamId), [teamId], { enabled: tab === "roster" });
  const schedule = useApi(() => api.getTeamSchedule(teamId, season), [teamId, season], {
    enabled: tab === "schedule",
  });
  const seasons = useApi(() => api.seasons(), []);

  const accent = teamColor(teamId);

  const eloSeries = useMemo(
    () => [{ points: (elo.data?.history ?? []).map((p) => p.rating), color: accent }],
    [elo.data, accent],
  );

  const metricEntries = useMemo(() => {
    const m = profile.data?.metrics ?? {};
    return Object.entries(m);
  }, [profile.data]);

  return (
    <>
      <Stack.Screen options={{ title: team.data?.full_name ?? teamId }} />
      <Screen
        onRefresh={() => {
          profile.refetch();
          outlook.refetch();
        }}
        refreshing={profile.isRefetching}
      >
        <Card accent={accent}>
          <View style={styles.headerRow}>
            <TeamLogo teamId={teamId} size={48} />
            <View style={{ flex: 1 }}>
              <Txt variant="h2">{team.data?.full_name ?? teamId}</Txt>
              <Txt variant="muted">
                {team.data ? `${team.data.conference} ${team.data.division}` : ""}
              </Txt>
            </View>
            {outlook.data?.grade ? (
              <Pill label={`Grade ${outlook.data.grade}`} color={gradeColor(outlook.data.grade)} />
            ) : null}
          </View>
          {profile.data?.record ? (
            <Txt variant="muted" style={{ marginTop: spacing.sm }}>
              Record: {profile.data.record.wins}-{profile.data.record.losses}
              {profile.data.record.ties ? `-${profile.data.record.ties}` : ""}
              {outlook.data?.current_elo ? ` · Elo ${Math.round(outlook.data.current_elo)}` : ""}
            </Txt>
          ) : null}
        </Card>

        {seasons.data ? (
          <SeasonSelect
            seasons={seasons.data.available}
            value={season ?? seasons.data.default}
            onChange={setSeason}
          />
        ) : null}

        <Segmented options={TABS} value={tab} onChange={setTab} />

        {tab === "overview" && (
          <>
            {elo.data?.history?.length ? (
              <Card>
                <Txt variant="label">Elo trend</Txt>
                <MiniLineChart series={eloSeries} width={width - spacing.lg * 4} height={140} />
              </Card>
            ) : null}

            {outlook.data ? (
              <Card>
                <Txt variant="label">Season outlook</Txt>
                <View style={styles.outlookGrid}>
                  <Outlook label="Proj. wins" value={num(outlook.data.mean_wins, 1)} />
                  <Outlook label="Playoffs" value={pctMaybe(outlook.data.playoff_pct)} />
                  <Outlook label="Div winner" value={pctMaybe(outlook.data.division_winner_pct)} />
                  <Outlook label="SB appear" value={pctMaybe(outlook.data.sb_appearance_pct)} />
                </View>
              </Card>
            ) : null}

            {profile.isLoading ? (
              <Loading />
            ) : profile.error ? (
              <ErrorState message="Couldn't load team profile." />
            ) : (
              <Card>
                <Txt variant="label">Team metrics (percentile vs league)</Txt>
                {metricEntries.length === 0 ? (
                  <Txt variant="muted">No metrics for this season.</Txt>
                ) : (
                  metricEntries.map(([key, m]) => (
                    <StatRow
                      key={key}
                      label={teamMetricLabel(key)}
                      value={teamMetricFmt(key, m.value)}
                      percentile={m.percentile}
                      higherIsBetter={m.higher_is_better}
                    />
                  ))
                )}
              </Card>
            )}
          </>
        )}

        {tab === "roster" &&
          (roster.isLoading ? (
            <Loading />
          ) : (roster.data ?? []).length === 0 ? (
            <EmptyState title="No roster data" />
          ) : (
            (roster.data ?? []).map((p) => (
              <Card key={p.id} onPress={() => router.push(`/players/${p.id}`)}>
                <View style={styles.playerRow}>
                  <Txt style={styles.jersey}>{p.jersey_number ?? "—"}</Txt>
                  <View style={{ flex: 1 }}>
                    <Txt variant="h3">{p.full_name}</Txt>
                    <Txt variant="muted">
                      {p.position}
                      {p.age ? ` · ${p.age} yo` : ""}
                      {p.college ? ` · ${p.college}` : ""}
                    </Txt>
                  </View>
                </View>
              </Card>
            ))
          ))}

        {tab === "schedule" &&
          (schedule.isLoading ? (
            <Loading />
          ) : (schedule.data ?? []).length === 0 ? (
            <EmptyState title="No schedule data" />
          ) : (
            (schedule.data ?? []).map((g) => {
              const isHome = g.home_team_id === teamId;
              const opp = isHome ? g.away_team_id : g.home_team_id;
              return (
                <Card key={g.id}>
                  <View style={styles.schedRow}>
                    <Txt variant="muted" style={{ width: 40 }}>
                      W{g.week ?? "—"}
                    </Txt>
                    <TeamLogo teamId={opp} size={22} />
                    <Txt style={{ flex: 1, color: colors.text }}>
                      {isHome ? "vs" : "@"} {opp ?? "TBD"}
                    </Txt>
                    {g.status === "post" ? (
                      <Txt variant="mono">
                        {g.away_score}-{g.home_score}
                      </Txt>
                    ) : (
                      <Txt variant="muted" style={{ fontSize: 11 }}>
                        {kickoff(g.start_time)}
                      </Txt>
                    )}
                  </View>
                </Card>
              );
            })
          ))}
      </Screen>
    </>
  );
}

function pctMaybe(n: number | null | undefined): string {
  if (n == null) return "—";
  // outlook percentages are 0..1
  return `${(n * 100).toFixed(0)}%`;
}

function Outlook({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.outlook}>
      <Txt variant="label" style={{ fontSize: 10 }}>
        {label}
      </Txt>
      <Txt style={{ color: colors.text, fontWeight: "800", fontSize: 18 }}>{value}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  outlookGrid: { flexDirection: "row", flexWrap: "wrap", marginTop: 4 },
  outlook: { width: "50%", paddingVertical: 6, gap: 2 },
  playerRow: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  jersey: {
    width: 32,
    textAlign: "center",
    color: colors.muted,
    fontWeight: "800",
    fontSize: 16,
  },
  schedRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
});
