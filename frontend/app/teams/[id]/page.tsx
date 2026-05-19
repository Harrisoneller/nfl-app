"use client";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, GamePrediction, Team } from "@/lib/api";
import { Card } from "@/components/Card";
import { TabBar, TabPanel } from "@/components/Tabs";
import { TeamTheme } from "@/components/ThemeProvider";
import { SeasonSelect } from "@/components/SeasonSelect";
import { PercentileBar } from "@/components/charts/PercentileBar";
import { RadarProfile } from "@/components/charts/RadarProfile";
import { MultiRadar, RadarSeries } from "@/components/charts/MultiRadar";
import { MultiTrendLine, TrendSeries } from "@/components/charts/MultiTrendLine";
import { ComparisonPicker, Pickable } from "@/components/ComparisonPicker";
import { LiveFeed } from "@/components/LiveFeed";
import { UpcomingSeason } from "@/components/UpcomingSeason";
import { SkeletonRadar, SkeletonTable } from "@/components/Skeleton";
import { EloBadge } from "@/components/predictions/EloBadge";
import { WinProbBar } from "@/components/predictions/WinProbBar";
import { MatchupPreviewRow } from "@/components/Week1Schedule";
import { SeasonOdds } from "@/components/predictions/SeasonOdds";
import { EloHistoryChart } from "@/components/predictions/EloHistoryChart";
import { TeamRemainingScheduleCard } from "@/components/predictions/TeamRemainingSchedule";
import { BettingHistoryCard } from "@/components/betting/BettingHistoryCard";
import { TeamEdgeCard } from "@/components/betting/EdgeBoard";
import { LeagueBestBetsCard } from "@/components/betting/LeagueBestBets";
import { H2HLauncher } from "@/components/H2HLauncher";
import { RecentFormCard } from "@/components/RecentFormCard";
import { DivisionStandingCard } from "@/components/DivisionStandingCard";
import { TopPerformersCard } from "@/components/TopPerformersCard";
import { pickColor } from "@/lib/colors";
import { TEAM_METRIC_LABELS, teamMetricFmt, teamMetricLabel } from "@/lib/metrics";
import { readOverlayParams, syncUrlOverlays } from "@/lib/overlay-url";

const OFFENSE_RADAR = [
  "off_epa_per_play", "off_success_rate", "off_explosive_play_rate",
  "off_red_zone_td_pct", "off_third_down_pct", "points_per_game",
];
const DEFENSE_RADAR = [
  "def_epa_per_play", "def_success_rate", "def_explosive_play_rate",
  "def_red_zone_td_pct", "def_yards_per_play", "points_allowed_per_game",
];

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "schedule", label: "Schedule" },
  { id: "predictions", label: "Predictions" },
  { id: "betting", label: "Betting" },
  { id: "news", label: "News" },
];

export default function TeamPage({ params }: { params: { id: string } }) {
  const id = params.id.toUpperCase();
  const [tab, setTab] = useState<string>("overview");
  const [season, setSeason] = useState<number | undefined>();
  const [trendMetric, setTrendMetric] = useState<string>("off_epa_per_play");
  const [overlays, setOverlays] = useState<Pickable[]>([]);

  const { data: team } = useSWR(["team", id], () => api.getTeam(id));
  const { data: profile, isLoading: profileLoading } = useSWR(
    season ? ["team-profile", id, season] : null,
    () => api.getTeamProfile(id, season),
    { revalidateOnFocus: false },
  );
  const { data: schedule } = useSWR(
    season ? ["team-sched", id, season] : null,
    () => api.getTeamSchedule(id, season),
  );
  const { data: roster } = useSWR(["team-roster", id], () => api.getTeamRoster(id));
  const { data: trend } = useSWR(
    season ? ["team-trend", id, trendMetric, season] : null,
    () => api.getTeamTrend(id, trendMetric, undefined, season),
  );

  // Predictions data
  const { data: outlook } = useSWR(
    ["team-outlook", id],
    () => api.teamSeasonOutlook(id),
    { revalidateOnFocus: false },
  );
  const { data: eloHistory } = useSWR(
    ["team-elo-history", id],
    () => api.teamEloHistory(id),
  );
  const { data: weekPreds } = useSWR(
    ["all-week-preds"],
    () => api.predictGames(undefined, undefined, true),
    { revalidateOnFocus: false },
  );
  const { data: teamSchedule } = useSWR(
    ["team-remaining-schedule", id],
    () => api.teamRemainingSchedule(id),
    { revalidateOnFocus: false },
  );

  // Overlay queries
  const { data: overlayProfiles } = useSWR(
    season && overlays.length ? ["team-overlay-profiles", id, season, overlays.map((o) => o.id).join(",")] : null,
    () => Promise.all(overlays.map((o) => api.getTeamProfile(o.id, season))),
  );
  const { data: overlayTrends } = useSWR(
    season && overlays.length ? ["team-overlay-trends", id, trendMetric, season, overlays.map((o) => o.id).join(",")] : null,
    () => Promise.all(overlays.map((o) => api.getTeamTrend(o.id, trendMetric, undefined, season))),
  );

  // URL state hydration
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    const parsed = readOverlayParams(sp);
    const t = sp.get("tab");
    if (t && TABS.some((x) => x.id === t)) setTab(t);
    if (parsed.season) setSeason(parsed.season);
    if (parsed.trendMetric) setTrendMetric(parsed.trendMetric);
    if (parsed.compareIds.length) {
      setOverlays(parsed.compareIds.map((id, i) => ({
        kind: "team", id, label: id, color: pickColor(i + 1),
      })));
    }
    if (season === undefined && !parsed.season) {
      api.seasons().then((m) => setSeason(m.default)).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const coloredOverlays: Pickable[] = useMemo(
    () => overlays.map((o, i) => ({ ...o, color: o.color || pickColor(i + 1) })),
    [overlays],
  );

  // Sync URL on state changes
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    params.set("tab", tab);
    if (season) params.set("season", String(season)); else params.delete("season");
    if (trendMetric) params.set("trend", trendMetric); else params.delete("trend");
    if (coloredOverlays.length) params.set("compare", coloredOverlays.map((o) => o.id).join(","));
    else params.delete("compare");
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? `?${qs}` : "");
    if (newUrl !== window.location.pathname + window.location.search) {
      window.history.replaceState({}, "", newUrl);
    }
  }, [tab, season, trendMetric, coloredOverlays]);

  // Next unplayed REG game for this team (full-season order), enriched with
  // current-week predictions when available.
  const nextGame: GamePrediction | undefined = useMemo(() => {
    const upcoming = teamSchedule?.games?.find((g) => !g.played);
    if (!upcoming) {
      const fromWeek = weekPreds?.games?.find(
        (g) => (g.home_team_id === id || g.away_team_id === id) && g.home_score == null,
      );
      return fromWeek;
    }

    const rich = weekPreds?.games?.find((g) => g.id && g.id === upcoming.id);
    if (rich) return rich;

    const isHome = upcoming.is_home;
    const homeId = isHome ? id : upcoming.opponent;
    const awayId = isHome ? upcoming.opponent : id;
    const myProb = upcoming.win_prob;
    const spread = isHome ? upcoming.predicted_spread_for_team : -upcoming.predicted_spread_for_team;
    return {
      id: upcoming.id,
      season: teamSchedule.season,
      week: upcoming.week ?? 0,
      gameday: upcoming.gameday,
      home_team_id: homeId,
      away_team_id: awayId,
      home_score: null,
      away_score: null,
      home_elo: isHome ? 1500 : upcoming.opp_elo,
      away_elo: isHome ? upcoming.opp_elo : 1500,
      prediction: {
        home_win_prob: isHome ? myProb : 1 - myProb,
        away_win_prob: isHome ? 1 - myProb : myProb,
        predicted_spread: spread,
        predicted_total: upcoming.predicted_total,
        predicted_home_score: upcoming.predicted_total / 2 + -spread / 2,
        predicted_away_score: upcoming.predicted_total / 2 - -spread / 2,
      },
    };
  }, [weekPreds, teamSchedule, id]);

  if (!team) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <TeamTheme primary={team.primary_color} secondary={team.secondary_color}>
      <div className="space-y-5">
        <Header team={team} record={profile?.record} season={season} setSeason={setSeason} outlook={outlook} />
        <TabBar tabs={TABS} active={tab} onChange={setTab} />

        <TabPanel active={tab} value="overview">
          <OverviewTab
            team={team}
            outlook={outlook}
            nextGame={nextGame}
            profile={profile}
            id={id}
            setTab={setTab}
          />
        </TabPanel>

        <TabPanel active={tab} value="performance">
          <Card
            title="Compare against other teams"
            action={
              <ComparisonPicker
                kind="team"
                excludeId={id}
                selected={coloredOverlays}
                onChange={(items) => setOverlays(items.map((it, i) => ({ ...it, color: pickColor(i + 1) })))}
              />
            }
          >
            <p className="text-xs text-muted">
              Add up to 3 teams to overlay on the radar and YoY trend below.
            </p>
          </Card>
          <ProfileSection
            team={team}
            profile={profile}
            loading={profileLoading}
            season={season}
            overlays={coloredOverlays}
            overlayProfiles={overlayProfiles}
          />
          <TrendSection
            metric={trendMetric}
            setMetric={setTrendMetric}
            trend={trend}
            team={team}
            overlays={coloredOverlays}
            overlayTrends={overlayTrends}
          />
        </TabPanel>

        <TabPanel active={tab} value="schedule">
          <UpcomingSeason teamId={id} />
          <ScheduleSection schedule={schedule || []} season={season} />
          <RosterSection roster={roster || []} />
        </TabPanel>

        <TabPanel active={tab} value="predictions">
          <PredictionsTab
            id={id}
            team={team}
            outlook={outlook}
            eloHistory={eloHistory}
            weekPreds={weekPreds}
          />
        </TabPanel>

        <TabPanel active={tab} value="betting">
          <TeamEdgeCard teamId={id} />
          <BettingHistoryCard teamId={id} />
          <LeagueBestBetsCard />
        </TabPanel>

        <TabPanel active={tab} value="news">
          <LiveFeed
            title="Live news & buzz"
            cacheKey={["team-news", id]}
            fetcher={() => api.getTeamNews(id, 50)}
            emptyText="No tagged news yet. The scheduler refreshes feeds every 5 min."
          />
        </TabPanel>
      </div>
    </TeamTheme>
  );
}

// =============================================================================
// Header (banner + grade chip + record + season select)
// =============================================================================

function Header({
  team, record, season, setSeason, outlook,
}: {
  team: Team;
  record: { wins: number; losses: number; ties: number } | undefined;
  season: number | undefined;
  setSeason: (s: number) => void;
  outlook: any;
}) {
  const rec = record
    ? `${record.wins}-${record.losses}${record.ties ? `-${record.ties}` : ""}`
    : "—";
  return (
    <div className="team-banner rounded-2xl p-6 text-white flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="text-sm opacity-80">{team.conference} {team.division}</div>
        <h1 className="text-3xl font-semibold">{team.full_name}</h1>
        <div className="mt-1 flex items-center gap-3 text-sm opacity-90">
          <span>{season ? `${season} record: ${rec}` : rec}</span>
          {outlook?.grade && (
            <span className="bg-black/30 rounded px-2 py-0.5 text-xs">
              {outlook.grade} · Elo {Math.round(outlook.current_elo ?? 0)}
            </span>
          )}
        </div>
      </div>
      <div className="bg-black/30 rounded-lg px-3 py-2 backdrop-blur">
        <SeasonSelect value={season} onChange={setSeason} />
      </div>
    </div>
  );
}

// =============================================================================
// Tab: Overview — casual-friendly hero
// =============================================================================

function OverviewTab({
  team, outlook, nextGame, profile, id, setTab,
}: {
  team: Team;
  outlook: any;
  nextGame: GamePrediction | undefined;
  profile: any;
  id: string;
  setTab: (t: string) => void;
}) {
  // 3 most "interesting" headline metrics from the profile to surface up top
  const headlineMetrics = useMemo(() => {
    if (!profile?.metrics) return [];
    const candidates = [
      "off_epa_per_play", "def_epa_per_play",
      "points_per_game", "points_allowed_per_game",
      "turnover_margin_per_game",
    ];
    return candidates
      .filter((k) => profile.metrics[k])
      .slice(0, 4)
      .map((k) => {
        const m = profile.metrics[k];
        const p = m.higher_is_better ? m.percentile : (m.percentile == null ? null : 100 - m.percentile);
        return { key: k, label: teamMetricLabel(k), value: teamMetricFmt(k, m.value), percentile: p };
      });
  }, [profile]);

  return (
    <>
      {/* Outlook, next game, division; compare under outlook + next game */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <SeasonOdds teamId={id} />
        <Card title="Next game">
          {nextGame ? (
            <div className="space-y-3">
              <MatchupPreviewRow game={nextGame} variant="compact" />
              <p className="text-xs text-muted">
                Tap{" "}
                <button onClick={() => setTab("predictions")} className="underline hover:no-underline">
                  Predictions
                </button>{" "}
                for the full week's slate.
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted">
              No upcoming game found for {team.id}. Check the{" "}
              <button onClick={() => setTab("schedule")} className="underline">Schedule</button> tab.
            </p>
          )}
        </Card>
        <div className="lg:row-span-2">
          <DivisionStandingCard teamId={id} />
        </div>
        <div className="lg:col-span-2">
          <H2HLauncher teamId={id} />
        </div>
      </div>

      {/* Recent form + key players */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <RecentFormCard teamId={id} />
        <TopPerformersCard teamId={id} />
      </div>

      {/* Row 3: at a glance metrics */}
      {headlineMetrics.length > 0 && (
        <Card title="At a glance" action={<button onClick={() => setTab("performance")} className="text-[11px] text-muted hover:underline">All metrics →</button>}>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-2">
            {headlineMetrics.map((m) => (
              <PercentileBar key={m.key} label={m.label} value={m.value} percentile={m.percentile} />
            ))}
          </div>
        </Card>
      )}

      {/* Team news */}
      <LiveFeed
        title="Latest team news"
        cacheKey={["team-news-overview", id]}
        fetcher={() => api.getTeamNews(id, 10)}
        emptyText="Headlines populate on the next news refresh (~5 min)."
      />
    </>
  );
}

// =============================================================================
// Tab: Predictions — the analytics-geek dream
// =============================================================================

function PredictionsTab({
  id, team, outlook, eloHistory, weekPreds,
}: {
  id: string;
  team: Team;
  outlook: any;
  eloHistory: any;
  weekPreds: any;
}) {
  const myUpcoming = useMemo(() => {
    if (!weekPreds?.games) return [];
    return weekPreds.games.filter(
      (g: GamePrediction) => g.home_team_id === id || g.away_team_id === id,
    );
  }, [weekPreds, id]);

  return (
    <>
      <SeasonOdds teamId={id} />

      <Card
        title="Elo history"
        action={
          outlook && (
            <EloBadge rating={outlook.current_elo ?? 0} grade={outlook.grade ?? "C"} size="sm" />
          )
        }
      >
        <p className="text-xs text-muted mb-2">
          K=20, HFA=55, MOV-dampened, season-regressed (75% carry). 1500 = league average.
        </p>
        <EloHistoryChart points={eloHistory?.history ?? []} color={team.primary_color} />
      </Card>

      <TeamRemainingScheduleCard teamId={id} />

      <Card title="Upcoming games — Elo + ML predictions">
        {myUpcoming.length === 0 ? (
          <p className="text-sm text-muted">
            No upcoming games found. The system shows predictions for the next unplayed
            week of the current season.
          </p>
        ) : (
          <div className="space-y-3">
            {myUpcoming.map((g: GamePrediction) => (
              <UpcomingPredictionRow key={g.id || `${g.home_team_id}-${g.away_team_id}`} g={g} myId={id} primary={team.primary_color} />
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function UpcomingPredictionRow({
  g, myId, primary,
}: { g: GamePrediction; myId: string; primary: string }) {
  const isHome = g.home_team_id === myId;
  const myProb = isHome ? g.prediction.home_win_prob : g.prediction.away_win_prob;
  const oppId = isHome ? g.away_team_id : g.home_team_id;
  return (
    <div className="panel p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div className="text-sm">
          <span className="text-muted">{isHome ? "vs" : "@"}</span>{" "}
          <Link href={`/teams/${oppId}`} className="font-semibold hover:underline">{oppId}</Link>
          <span className="text-muted ml-2 text-xs">{g.gameday || `Wk ${g.week}`}</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-muted">Elo:</span>
          <span className="tabular-nums">{Math.round(g.home_elo)} – {Math.round(g.away_elo)}</span>
        </div>
      </div>
      <WinProbBar
        awayTeam={g.away_team_id}
        awayProb={g.prediction.away_win_prob}
        homeTeam={g.home_team_id}
        homeProb={g.prediction.home_win_prob}
        homeColor={isHome ? primary : "#475569"}
        awayColor={!isHome ? primary : "#475569"}
      />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 text-xs">
        <Mini label="Win prob" value={`${(myProb * 100).toFixed(0)}%`} />
        <Mini
          label="Predicted spread"
          value={`${g.prediction.predicted_spread <= 0 ? g.home_team_id : g.away_team_id} -${Math.abs(g.prediction.predicted_spread).toFixed(1)}`}
        />
        <Mini label="Predicted total" value={g.prediction.predicted_total.toFixed(1)} />
        <Mini
          label="ML spread"
          value={
            g.ml_prediction
              ? `${g.ml_prediction.predicted_spread <= 0 ? g.home_team_id : g.away_team_id} -${Math.abs(g.ml_prediction.predicted_spread).toFixed(1)}`
              : "n/a"
          }
        />
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg rounded px-2 py-1.5">
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="font-medium tabular-nums">{value}</div>
    </div>
  );
}

// =============================================================================
// Tab: Performance — existing radars + percentile bars + trend
// =============================================================================

function ProfileSection({
  team, profile, loading, season, overlays, overlayProfiles,
}: {
  team: Team;
  profile: any;
  loading: boolean;
  season: number | undefined;
  overlays: Pickable[];
  overlayProfiles: any[] | undefined;
}) {
  if (loading || !profile) {
    return (
      <Card title={`Season profile${season ? ` — ${season}` : ""}`}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-4">
          <SkeletonRadar />
          <SkeletonRadar />
        </div>
        <SkeletonTable rows={6} />
      </Card>
    );
  }
  if (profile.error) {
    return (
      <Card title={`Season profile — ${profile.season}`}>
        <p className="text-sm text-red-400">No play-by-play data for {profile.season}: {String(profile.error)}</p>
      </Card>
    );
  }

  const buildSeries = (radarKeys: string[], isDefense: boolean): RadarSeries[] => {
    const series: RadarSeries[] = [];
    const valuesFromProfile = (p: any) => {
      const out: Record<string, number | null> = {};
      for (const k of radarKeys) {
        const m = p?.metrics?.[k];
        let v = m?.percentile;
        if (isDefense && v != null) v = 100 - v;
        out[teamMetricLabel(k)] = v ?? null;
      }
      return out;
    };
    series.push({ name: team.id, color: team.primary_color, values: valuesFromProfile(profile) });
    if (overlayProfiles) {
      overlays.forEach((o, i) => {
        const op = overlayProfiles[i];
        if (op && !op.error) series.push({ name: o.label, color: o.color, values: valuesFromProfile(op) });
      });
    }
    return series;
  };

  const metricEntries = Object.entries(profile.metrics || {}) as [string, any][];
  const byGroup: Record<string, [string, any][]> = { offense: [], defense: [], team: [] };
  for (const [k, v] of metricEntries) {
    const g = TEAM_METRIC_LABELS[k]?.group || "team";
    byGroup[g].push([k, v]);
  }

  return (
    <Card title={`Season profile — ${profile.season}`}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div>
          <h3 className="text-sm text-muted mb-2">Offense (percentile vs league)</h3>
          {overlays.length === 0 ? (
            <RadarProfile
              data={OFFENSE_RADAR
                .filter((k) => profile.metrics[k])
                .map((k) => ({ metric: teamMetricLabel(k), percentile: profile.metrics[k]?.percentile }))}
              color={team.primary_color}
            />
          ) : (
            <MultiRadar
              metrics={OFFENSE_RADAR.filter((k) => profile.metrics[k]).map(teamMetricLabel)}
              series={buildSeries(OFFENSE_RADAR.filter((k) => profile.metrics[k]), false)}
            />
          )}
        </div>
        <div>
          <h3 className="text-sm text-muted mb-2">Defense (higher = better)</h3>
          {overlays.length === 0 ? (
            <RadarProfile
              data={DEFENSE_RADAR
                .filter((k) => profile.metrics[k])
                .map((k) => {
                  const m = profile.metrics[k];
                  const p = m?.percentile == null ? null : 100 - m.percentile;
                  return { metric: teamMetricLabel(k).replace("allowed", "").trim(), percentile: p };
                })}
              color={team.secondary_color}
            />
          ) : (
            <MultiRadar
              metrics={DEFENSE_RADAR.filter((k) => profile.metrics[k]).map(teamMetricLabel)}
              series={buildSeries(DEFENSE_RADAR.filter((k) => profile.metrics[k]), true)}
            />
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-2">
        {(["offense", "defense", "team"] as const).map((g) => (
          <div key={g}>
            <h3 className="text-sm uppercase tracking-wide text-muted mb-2">{g}</h3>
            {byGroup[g].map(([k, v]) => (
              <PercentileBar
                key={k}
                label={teamMetricLabel(k)}
                value={teamMetricFmt(k, v.value)}
                percentile={v.higher_is_better ? v.percentile : v.percentile == null ? null : 100 - v.percentile}
              />
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}

function TrendSection({
  metric, setMetric, trend, team, overlays, overlayTrends,
}: {
  metric: string;
  setMetric: (m: string) => void;
  trend: any;
  team: Team;
  overlays: Pickable[];
  overlayTrends: any[] | undefined;
}) {
  const myPoints = useMemo(
    () => (trend?.points || []).filter((p: any) => p.value != null),
    [trend],
  );
  const series: TrendSeries[] = useMemo(() => {
    const out: TrendSeries[] = [{ name: team.id, color: team.primary_color, points: myPoints }];
    if (overlayTrends) {
      overlays.forEach((o, i) => {
        const t = overlayTrends[i];
        if (t && Array.isArray(t.points)) {
          out.push({ name: o.label, color: o.color, points: t.points.filter((p: any) => p.value != null) });
        }
      });
    }
    return out;
  }, [overlayTrends, overlays, myPoints, team]);

  return (
    <Card
      title="Year-over-year trend"
      action={
        <select value={metric} onChange={(e) => setMetric(e.target.value)} className="bg-bg border divider rounded px-2 py-1 text-xs">
          {Object.entries(TEAM_METRIC_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
      }
    >
      {myPoints.length === 0 ? (
        <p className="text-sm text-muted">Loading trend…</p>
      ) : (
        <MultiTrendLine series={series} yLabel={TEAM_METRIC_LABELS[metric]?.label} />
      )}
    </Card>
  );
}

// =============================================================================
// Tab: Schedule
// =============================================================================

function ScheduleSection({ schedule, season }: { schedule: any[]; season: number | undefined }) {
  return (
    <Card title={`Schedule${season ? ` — ${season}` : ""}`}>
      {schedule.length === 0 ? (
        <p className="text-sm text-muted">
          No games loaded for this season yet. The scheduler is auto-syncing 6 seasons of
          schedules on first boot.
        </p>
      ) : (
        <ul className="text-sm divide-y divider">
          {schedule.slice(0, 22).map((g) => (
            <li key={g.id} className="py-1.5 flex justify-between gap-3">
              <span className="text-muted w-12">Wk {g.week ?? "—"}</span>
              <span className="flex-1">{g.away_team_id} @ {g.home_team_id}</span>
              <span className="text-muted">{g.status_detail || g.status}</span>
              <span className="tabular-nums w-16 text-right">
                {g.away_score ?? ""}–{g.home_score ?? ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RosterSection({ roster }: { roster: any[] }) {
  const grouped: Record<string, any[]> = {};
  for (const p of roster) {
    grouped[p.position] ??= [];
    grouped[p.position].push(p);
  }
  const positions = Object.keys(grouped).sort();
  return (
    <Card title={`Roster (${roster.length})`}>
      {roster.length === 0 ? (
        <p className="text-sm text-muted">
          Roster is empty. The scheduler syncs from Sleeper a few seconds after startup.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {positions.map((pos) => (
            <div key={pos}>
              <h3 className="text-sm text-muted mb-2">{pos}</h3>
              <ul className="space-y-0.5 text-sm">
                {grouped[pos].map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <Link href={`/players/${p.id}`} className="hover:underline">{p.full_name}</Link>
                    <span className="text-muted">#{p.jersey_number ?? "—"}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
