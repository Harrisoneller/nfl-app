import { useMemo, useState } from "react";
import { StyleSheet, View } from "react-native";
import { router } from "expo-router";
import { api, type SparkyGame, type SparkyParlay } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Screen } from "@/components/ui/Screen";
import { Card } from "@/components/ui/Card";
import { Txt } from "@/components/ui/Text";
import { Pill } from "@/components/ui/Pill";
import { Segmented } from "@/components/ui/Segmented";
import { Loading, ErrorState, EmptyState } from "@/components/ui/States";
import { TeamLogo } from "@/components/TeamLogo";
import { WinProbBar } from "@/components/WinProbBar";
import { colors, radius, spacing } from "@/theme/theme";
import { americanOdds, pct, num, kickoff } from "@/lib/format";

type Tab = "dashboard" | "parlays" | "accuracy";
const TABS = [
  { id: "dashboard" as const, label: "Dashboard" },
  { id: "parlays" as const, label: "Parlays" },
  { id: "accuracy" as const, label: "Accuracy" },
];

export default function SparkyScreen() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const slate = useApi(() => api.sparkySlate(undefined, true), []);
  const accuracy = useApi(() => api.sparkyAccuracy(), [], { enabled: tab === "accuracy" });

  const games = slate.data?.games ?? [];
  const parlays = slate.data?.recommended_parlays ?? [];

  return (
    <Screen onRefresh={slate.refetch} refreshing={slate.isRefetching}>
      <View>
        <Txt variant="h1">Sparky</Txt>
        <Txt variant="muted">Model-vs-market predictions, signals, and parlay intelligence.</Txt>
        {slate.data?.slate_date ? (
          <Txt variant="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Slate: {slate.data.slate_date} · {games.length} games
          </Txt>
        ) : null}
      </View>

      <Segmented options={TABS} value={tab} onChange={setTab} />

      {tab === "dashboard" &&
        (slate.isLoading ? (
          <Loading label="Building slate…" />
        ) : slate.error ? (
          <ErrorState message="Couldn't load Sparky's slate." />
        ) : games.length === 0 ? (
          <EmptyState
            title="No predictions yet"
            subtitle="Sparky builds a slate once upcoming odds snapshots are available."
          />
        ) : (
          games.map((g) => <GameCard key={g.event_id} game={g} />)
        ))}

      {tab === "parlays" &&
        (parlays.length === 0 ? (
          <EmptyState title="No recommended parlays" subtitle="Check back when a slate is live." />
        ) : (
          parlays.map((p) => <ParlayCard key={p.rank} parlay={p} />)
        ))}

      {tab === "accuracy" &&
        (accuracy.isLoading ? (
          <Loading />
        ) : accuracy.error || !accuracy.data ? (
          <ErrorState message="Couldn't load accuracy." />
        ) : (
          <AccuracyPanel data={accuracy.data} />
        ))}
    </Screen>
  );
}

function GameCard({ game }: { game: SparkyGame }) {
  const conf = game.confidence_score ?? 0;
  const tone =
    game.classification?.toLowerCase().includes("strong") || conf >= 0.7
      ? "positive"
      : conf >= 0.55
        ? "warning"
        : "neutral";
  const homeWP = game.home_win_prob ?? (game.predicted_winner === game.home_team_id ? game.win_prob : 1 - game.win_prob);

  return (
    <Card onPress={() => router.push(`/sparky/${encodeURIComponent(game.event_id)}`)}>
      <View style={styles.head}>
        <View style={styles.matchup}>
          <TeamLogo teamId={game.away_team_id} size={24} />
          <Txt style={styles.at}>@</Txt>
          <TeamLogo teamId={game.home_team_id} size={24} />
          <Txt style={styles.teams} numberOfLines={1}>
            {game.away_team_id} @ {game.home_team_id}
          </Txt>
        </View>
        {game.classification ? <Pill label={game.classification} tone={tone as never} /> : null}
      </View>

      <View style={{ marginVertical: spacing.sm }}>
        <WinProbBar awayId={game.away_team_id} homeId={game.home_team_id} homeWinProb={homeWP} />
      </View>

      <View style={styles.metaRow}>
        <Meta label="Pick" value={game.predicted_winner ?? "—"} />
        <Meta label="Confidence" value={pct(conf)} />
        <Meta label="Model" value={pct(game.model_prob)} />
        <Meta label="Market" value={pct(game.market_prob)} />
      </View>

      {game.signals?.length ? (
        <View style={styles.signals}>
          {game.signals.slice(0, 3).map((s) => (
            <Pill
              key={s.key}
              label={s.label}
              tone={s.severity === "bullish" ? "positive" : s.severity === "warning" ? "warning" : "info"}
            />
          ))}
          {game.signals.length > 3 ? (
            <Txt variant="muted" style={{ fontSize: 11 }}>+{game.signals.length - 3} more</Txt>
          ) : null}
        </View>
      ) : null}

      {game.explanation ? (
        <Txt variant="muted" numberOfLines={2} style={{ marginTop: 6 }}>
          {game.explanation}
        </Txt>
      ) : null}
    </Card>
  );
}

function ParlayCard({ parlay }: { parlay: SparkyParlay }) {
  return (
    <Card accent={parlay.is_value ? colors.positive : undefined}>
      <View style={styles.head}>
        <Txt variant="h3">
          #{parlay.rank} · {parlay.n_legs ?? parlay.legs.length}-leg parlay
        </Txt>
        <Txt variant="mono" style={{ color: colors.accent, fontSize: 16 }}>
          {americanOdds(parlay.parlay_odds_american)}
        </Txt>
      </View>

      <View style={styles.metaRow}>
        <Meta label="Win prob" value={pct(parlay.combined_win_prob)} />
        <Meta label="Implied" value={pct(parlay.implied_prob)} />
        {parlay.expected_value != null ? (
          <Meta
            label="EV"
            value={`${parlay.expected_value > 0 ? "+" : ""}${num(parlay.expected_value, 2)}u`}
          />
        ) : null}
        {parlay.kelly_fraction != null ? (
          <Meta label="Kelly" value={pct(parlay.kelly_fraction, 1)} />
        ) : null}
      </View>

      <View style={styles.legs}>
        {parlay.legs.map((leg, i) => (
          <View key={i} style={styles.legRow}>
            <TeamLogo teamId={leg.team_id} size={20} />
            <Txt style={{ flex: 1, color: colors.text }} numberOfLines={1}>
              {leg.team_id} {leg.is_underdog ? "(dog)" : ""}
            </Txt>
            <Txt variant="muted">{pct(leg.win_prob)}</Txt>
            <Txt variant="mono" style={{ width: 56, textAlign: "right" }}>
              {americanOdds(leg.price_american)}
            </Txt>
          </View>
        ))}
      </View>

      {parlay.explanation ? (
        <Txt variant="muted" numberOfLines={3} style={{ marginTop: 6 }}>
          {parlay.explanation}
        </Txt>
      ) : null}
    </Card>
  );
}

function AccuracyPanel({ data }: { data: import("@/lib/api").SparkyAccuracy }) {
  const rolling = useMemo(() => Object.entries(data.individual_picks.rolling ?? {}), [data]);
  return (
    <View style={{ gap: spacing.md }}>
      <Card>
        <Txt variant="label">Overall</Txt>
        <View style={styles.metaRow}>
          <Meta label="Pick acc." value={pctRaw(data.trends.overall_pick_accuracy_pct)} />
          <Meta label="Parlay #1" value={pctRaw(data.trends.overall_parlay_rank1_pct)} />
          <Meta label="Top-3" value={pctRaw(data.trends.overall_parlay_top3_pct)} />
        </View>
        <Txt variant="muted" style={{ marginTop: 6, fontSize: 12 }}>
          {data.trends.n_picks_settled} picks · {data.trends.n_parlays_settled} parlays settled · as of {data.as_of}
        </Txt>
      </Card>

      {rolling.length ? (
        <Card>
          <Txt variant="label">Rolling pick accuracy</Txt>
          {rolling.map(([k, w]) => (
            <View key={k} style={styles.legRow}>
              <Txt style={{ flex: 1, color: colors.text }}>{k}</Txt>
              <Txt variant="muted">n={w.n}</Txt>
              <Txt variant="mono" style={{ width: 64, textAlign: "right" }}>
                {pctRaw(w.accuracy_pct)}
              </Txt>
            </View>
          ))}
        </Card>
      ) : null}

      {data.individual_picks.by_signal?.length ? (
        <Card>
          <Txt variant="label">By signal</Txt>
          {data.individual_picks.by_signal.map((s) => (
            <View key={s.signal} style={styles.legRow}>
              <Txt style={{ flex: 1, color: colors.text }} numberOfLines={1}>{s.signal}</Txt>
              <Txt variant="muted">n={s.n}</Txt>
              <Txt variant="mono" style={{ width: 64, textAlign: "right" }}>
                {pctRaw(s.accuracy_pct)}
              </Txt>
            </View>
          ))}
        </Card>
      ) : null}
    </View>
  );
}

function pctRaw(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.meta}>
      <Txt variant="label" style={{ fontSize: 10 }}>
        {label}
      </Txt>
      <Txt style={styles.metaValue}>{value}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  head: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing.sm,
  },
  matchup: { flexDirection: "row", alignItems: "center", gap: 6, flex: 1 },
  at: { color: colors.mutedDim, fontSize: 12 },
  teams: { color: colors.text, fontWeight: "700", marginLeft: 4, flexShrink: 1 },
  metaRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md, marginTop: 4 },
  meta: { gap: 2 },
  metaValue: { color: colors.text, fontWeight: "700", fontSize: 14 },
  signals: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: spacing.sm, alignItems: "center" },
  legs: {
    marginTop: spacing.sm,
    gap: 6,
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: spacing.sm,
  },
  legRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: 2 },
});
