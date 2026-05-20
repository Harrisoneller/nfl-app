"use client";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, Player, PlayerProfile } from "@/lib/api";
import { Card } from "@/components/Card";
import { TabBar, TabPanel } from "@/components/Tabs";
import { SeasonSelect } from "@/components/SeasonSelect";
import { PercentileBar } from "@/components/charts/PercentileBar";
import { RadarProfile } from "@/components/charts/RadarProfile";
import { TrendLine } from "@/components/charts/TrendLine";
import { GameLogBar } from "@/components/charts/GameLogBar";
import { MultiRadar, RadarSeries } from "@/components/charts/MultiRadar";
import { MultiTrendLine, TrendSeries } from "@/components/charts/MultiTrendLine";
import { ComparisonPicker, Pickable } from "@/components/ComparisonPicker";
import { LiveFeed } from "@/components/LiveFeed";
import {
  PlayerSeasonProjectionCard,
  PlayerGamePredictionsCard,
} from "@/components/predictions/PlayerProjections";
import { SkeletonRadar, SkeletonTable } from "@/components/Skeleton";
import { DataSourceBadge } from "@/components/DataSourceBadge";
import { BetaPill } from "@/components/BetaBanner";
import { pickColor } from "@/lib/colors";
import { PLAYER_METRIC_LABELS, playerMetricFmt, playerMetricLabel } from "@/lib/metrics";
import { readOverlayParams, syncUrlOverlays } from "@/lib/overlay-url";

const RADAR_KEYS_BY_POS: Record<string, string[]> = {
  QB: ["completion_pct", "yards_per_attempt", "passer_rating", "epa_per_play", "success_rate", "passing_tds", "rushing_yards"],
  RB: ["yards_per_carry", "rushing_yards", "snap_share", "target_share", "epa_per_touch", "success_rate", "fantasy_points_ppr"],
  WR: ["target_share", "air_yards_share", "yards_per_target", "catch_rate", "wopr", "racr", "yac", "fantasy_points_ppr"],
  TE: ["target_share", "air_yards_share", "yards_per_target", "catch_rate", "wopr", "racr", "yac", "fantasy_points_ppr"],
};

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "predictions", label: "Predictions" },
  { id: "news", label: "News" },
];

export default function PlayerPage({ params }: { params: { id: string } }) {
  const [tab, setTab] = useState<string>("overview");
  const [season, setSeason] = useState<number | undefined>();
  const [trendMetric, setTrendMetric] = useState<string>("fantasy_points_ppr");
  const [gamelogMetric, setGamelogMetric] = useState<string>("fantasy_points_ppr");
  const [overlays, setOverlays] = useState<Pickable[]>([]);

  const { data: player } = useSWR(["player", params.id], () => api.getPlayer(params.id));
  const { data: profile, isLoading: profileLoading } = useSWR(
    season ? ["player-profile", params.id, season] : null,
    () => api.getPlayerProfile(params.id, season),
    { revalidateOnFocus: false },
  );
  const { data: gamelog } = useSWR(
    season ? ["player-gamelog", params.id, season] : null,
    () => api.getPlayerGamelog(params.id, season),
  );
  const { data: trend } = useSWR(
    season ? ["player-trend", params.id, trendMetric, season] : null,
    () => api.getPlayerTrend(params.id, trendMetric, undefined, season),
  );

  // Overlay queries
  const { data: overlayProfiles } = useSWR(
    season && overlays.length ? ["player-overlay-profiles", params.id, season, overlays.map((o) => o.id).join(",")] : null,
    () => Promise.all(overlays.map((o) => api.getPlayerProfile(o.id, season))),
  );
  const { data: overlayTrends } = useSWR(
    season && overlays.length ? ["player-overlay-trends", params.id, trendMetric, season, overlays.map((o) => o.id).join(",")] : null,
    () => Promise.all(overlays.map((o) => api.getPlayerTrend(o.id, trendMetric, undefined, season))),
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
        kind: "player", id, label: id, color: pickColor(i + 1), position: "",
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

  if (!player) return <p className="text-sm text-muted">Loading…</p>;
  const pos = (player.position || "").toUpperCase();
  const metricKeys = Object.keys(profile?.metrics || {});
  const positionMetrics = profileLoading ? [] : metricKeys.filter((k) => PLAYER_METRIC_LABELS[k]);

  return (
    <div className="space-y-5">
      <Header player={player} profile={profile} season={season} setSeason={setSeason} />
      <TabBar tabs={TABS} active={tab} onChange={setTab} />

      <TabPanel active={tab} value="overview">
        <OverviewTab
          player={player}
          profile={profile}
          positionMetrics={positionMetrics}
          setTab={setTab}
        />
      </TabPanel>

      <TabPanel active={tab} value="performance">
        <Card
          title="Compare against other players"
          action={
            <ComparisonPicker
              kind="player"
              excludeId={params.id}
              selected={coloredOverlays}
              onChange={(items) => setOverlays(items.map((it, i) => ({ ...it, color: pickColor(i + 1) })))}
            />
          }
        >
          <p className="text-xs text-muted">Add up to 3 players to overlay.</p>
        </Card>
        <RadarSection profile={profile} positionMetrics={positionMetrics} pos={pos} overlays={coloredOverlays} overlayProfiles={overlayProfiles} />
        <PercentileSection profile={profile} positionMetrics={positionMetrics} />
        <TrendSection playerName={player.full_name} metric={trendMetric} setMetric={setTrendMetric} availableMetrics={positionMetrics} trend={trend} overlays={coloredOverlays} overlayTrends={overlayTrends} />
        <GameLogSection playerName={player.full_name} gamelog={gamelog || []} metric={gamelogMetric} setMetric={setGamelogMetric} />
      </TabPanel>

      <TabPanel active={tab} value="predictions">
        <PlayerSeasonProjectionCard playerId={params.id} />
        <PlayerGamePredictionsCard playerId={params.id} />
      </TabPanel>

      <TabPanel active={tab} value="news">
        <LiveFeed
          title="Latest mentions"
          cacheKey={["player-news", params.id]}
          fetcher={() => api.getPlayerNews(params.id, 40)}
          emptyText={`No recent mentions of ${player.full_name}. Feed refreshes every 60s.`}
        />
      </TabPanel>
    </div>
  );
}

// =============================================================================
// Header
// =============================================================================

function Header({
  player, profile, season, setSeason,
}: {
  player: Player;
  profile: PlayerProfile | undefined;
  season: number | undefined;
  setSeason: (s: number) => void;
}) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-semibold">{player.full_name}</h1>
            <BetaPill />
          </div>
          <div className="mt-1 text-sm text-muted flex flex-wrap gap-x-3 gap-y-1">
            <span className="bg-team-primary/20 text-team-primary px-2 py-0.5 rounded text-xs font-semibold">
              {player.position}
            </span>
            {player.team_id && (
              <Link href={`/teams/${player.team_id}`} className="hover:underline font-medium">
                {player.team_id}
              </Link>
            )}
            <span>#{player.jersey_number ?? "—"}</span>
            {player.age && <span>Age {player.age}</span>}
            {player.height && <span>{player.height}</span>}
            {player.weight && <span>{player.weight} lbs</span>}
            {player.college && <span>{player.college}</span>}
          </div>
          {profile && !profile.error && (
            <div className="mt-1 text-xs text-muted flex items-center gap-2 flex-wrap">
              <span>vs. {profile.peer_count} {profile.position}s in {profile.season}</span>
              {(profile as any).fallback_from && (
                <DataSourceBadge fallbackFrom={(profile as any).fallback_from} />
              )}
            </div>
          )}
        </div>
        <SeasonSelect value={season} onChange={setSeason} />
      </div>
    </Card>
  );
}

// =============================================================================
// Overview: highlight metrics + season projection (compact) + news
// =============================================================================

function OverviewTab({
  player, profile, positionMetrics, setTab,
}: {
  player: Player;
  profile: PlayerProfile | undefined;
  positionMetrics: string[];
  setTab: (t: string) => void;
}) {
  // Top 6 most position-meaningful metrics for the overview
  const PRIORITY: Record<string, string[]> = {
    QB: ["passing_yards", "passing_tds", "yards_per_attempt", "passer_rating", "epa_per_play", "fantasy_points_ppr"],
    RB: ["rushing_yards", "rushing_tds", "yards_per_carry", "snap_share", "receiving_yards", "fantasy_points_ppr"],
    WR: ["receiving_yards", "receiving_tds", "target_share", "yards_per_target", "wopr", "fantasy_points_ppr"],
    TE: ["receiving_yards", "receiving_tds", "target_share", "yards_per_target", "wopr", "fantasy_points_ppr"],
  };
  const keys = (PRIORITY[profile?.position || ""] || []).filter((k) => positionMetrics.includes(k));

  return (
    <>
      {profile && !profile.error && keys.length > 0 && (
        <Card
          title={`${profile.season} highlights`}
          action={
            <button onClick={() => setTab("performance")} className="text-[11px] text-muted hover:underline">
              All metrics →
            </button>
          }
        >
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-2">
            {keys.map((k) => {
              const m = profile.metrics[k];
              // Backend already orients percentile toward "good for this
              // player" (e.g. low sack rate ⇒ high percentile).
              return (
                <PercentileBar
                  key={k}
                  label={playerMetricLabel(k)}
                  value={playerMetricFmt(k, m.value)}
                  percentile={m.percentile}
                />
              );
            })}
          </div>
        </Card>
      )}

      <PlayerSeasonProjectionCard playerId={player.id} />

      <LiveFeed
        title="Latest mentions"
        cacheKey={["player-news-overview", player.id]}
        fetcher={() => api.getPlayerNews(player.id, 8)}
        emptyText={`No recent mentions of ${player.full_name}.`}
      />
    </>
  );
}

// =============================================================================
// Performance sections (extracted)
// =============================================================================

function RadarSection({
  profile, positionMetrics, pos, overlays, overlayProfiles,
}: {
  profile: PlayerProfile | undefined;
  positionMetrics: string[];
  pos: string;
  overlays: Pickable[];
  overlayProfiles: any[] | undefined;
}) {
  if (!profile || profile.error) {
    return (
      <Card title="Profile">
        <div className="text-sm text-muted space-y-2">
          <p>{profile?.error ? profile.error : "Loading profile…"}</p>
          {profile?.error && (
            <p className="text-xs">
              The seasonal stats source hasn't published data for the season
              you picked (or this player has no rows in it). Try selecting a
              past season from the dropdown, or check{" "}
              <code className="text-[10px] px-1 bg-bg/60 rounded">/admin/data-availability</code>{" "}
              to confirm what's loaded.
            </p>
          )}
        </div>
      </Card>
    );
  }
  const keys = (RADAR_KEYS_BY_POS[profile.position] || RADAR_KEYS_BY_POS.WR).filter((k) =>
    positionMetrics.includes(k),
  );
  const labels = keys.map((k) => playerMetricLabel(k));
  const valuesFor = (p: any): Record<string, number | null> => {
    // percentile from backend is already "good for player" oriented; no flip.
    const out: Record<string, number | null> = {};
    for (const k of keys) {
      const m = p?.metrics?.[k];
      out[playerMetricLabel(k)] = m?.percentile ?? null;
    }
    return out;
  };
  if (overlays.length === 0) {
    const data = keys.map((k) => {
      const m = profile.metrics[k];
      return { metric: playerMetricLabel(k), percentile: m.percentile };
    });
    return (
      <Card title={`Player profile — ${profile.season}`}>
        <RadarProfile data={data} />
      </Card>
    );
  }
  const series: RadarSeries[] = [
    { name: "self", color: "var(--team-primary)", values: valuesFor(profile) },
    ...overlays.map((o, i) => ({
      name: o.label,
      color: o.color,
      values: valuesFor(overlayProfiles?.[i]),
    })),
  ];
  return (
    <Card title={`Player profile — ${profile.season}`}>
      <MultiRadar metrics={labels} series={series} />
    </Card>
  );
}

function PercentileSection({
  profile, positionMetrics,
}: { profile: PlayerProfile | undefined; positionMetrics: string[] }) {
  if (!profile || profile.error) return null;
  const CATS: Record<string, string[]> = {
    Volume: ["games", "attempts", "completions", "passing_yards", "passing_tds", "interceptions", "carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds"],
    Opportunity: ["snap_share", "target_share", "air_yards_share", "red_zone_carries", "adot"],
    Efficiency: ["completion_pct", "yards_per_attempt", "passer_rating", "sack_rate", "yards_per_carry", "yards_per_reception", "yards_per_target", "catch_rate", "yac"],
    "Advanced / EPA": ["epa_per_play", "epa_per_touch", "success_rate", "cpoe", "racr", "wopr"],
    "Fantasy": ["fantasy_points_ppr"],
  };
  const grouped = Object.fromEntries(
    Object.entries(CATS).map(([cat, keys]) => [cat, keys.filter((k) => positionMetrics.includes(k))]),
  );
  return (
    <>
      {(["Volume", "Opportunity", "Efficiency", "Advanced / EPA", "Fantasy"] as const).map((cat) =>
        grouped[cat]?.length > 0 ? (
          <Card key={cat} title={cat}>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1">
              {grouped[cat].map((k) => {
                const m = profile.metrics[k];
                if (!m) return null;
                return (
                  <PercentileBar
                    key={k}
                    label={playerMetricLabel(k)}
                    value={playerMetricFmt(k, m.value)}
                    percentile={m.percentile}
                  />
                );
              })}
            </div>
          </Card>
        ) : null,
      )}
    </>
  );
}

function TrendSection({
  playerName, metric, setMetric, availableMetrics, trend, overlays, overlayTrends,
}: {
  playerName: string; metric: string; setMetric: (m: string) => void;
  availableMetrics: string[]; trend: any;
  overlays: Pickable[]; overlayTrends: any[] | undefined;
}) {
  const myPoints = useMemo(() => (trend?.points || []).filter((p: any) => p.value != null), [trend]);
  const series: TrendSeries[] = useMemo(() => {
    const out: TrendSeries[] = [{ name: playerName, color: "#22d3ee", points: myPoints }];
    if (overlayTrends) {
      overlays.forEach((o, i) => {
        const t = overlayTrends[i];
        if (t && Array.isArray(t.points)) {
          out.push({ name: o.label, color: o.color, points: t.points.filter((p: any) => p.value != null) });
        }
      });
    }
    return out;
  }, [overlayTrends, overlays, myPoints, playerName]);
  return (
    <Card
      title="Career trend"
      action={
        <select value={metric} onChange={(e) => setMetric(e.target.value)} className="bg-bg border divider rounded px-2 py-1 text-xs">
          {availableMetrics.map((k) => <option key={k} value={k}>{playerMetricLabel(k)}</option>)}
        </select>
      }
    >
      {myPoints.length === 0 ? (
        <p className="text-sm text-muted">Computing across seasons…</p>
      ) : overlays.length === 0 ? (
        <TrendLine data={myPoints} yLabel={playerMetricLabel(metric)} />
      ) : (
        <MultiTrendLine series={series} yLabel={playerMetricLabel(metric)} />
      )}
    </Card>
  );
}

function GameLogSection({
  playerName, gamelog, metric, setMetric,
}: { playerName: string; gamelog: any[]; metric: string; setMetric: (m: string) => void }) {
  if (gamelog.length === 0) {
    return <Card title="Game log"><p className="text-sm text-muted">No weekly data for this season yet.</p></Card>;
  }
  const numericKeys = Object.keys(gamelog[0]).filter(
    (k) => k !== "week" && k !== "opponent_team" && typeof gamelog[0][k] === "number",
  );
  return (
    <Card
      title="Game log"
      action={
        <select value={metric} onChange={(e) => setMetric(e.target.value)} className="bg-bg border divider rounded px-2 py-1 text-xs">
          {numericKeys.map((k) => <option key={k} value={k}>{playerMetricLabel(k) || k}</option>)}
        </select>
      }
    >
      <GameLogBar data={gamelog} yKey={metric} yLabel={playerMetricLabel(metric) || metric} />
      <div className="overflow-x-auto mt-3">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">Opp</th>
              {numericKeys.slice(0, 8).map((k) => <th key={k} className="pr-3">{playerMetricLabel(k) || k}</th>)}
            </tr>
          </thead>
          <tbody>
            {gamelog.map((row) => (
              <tr key={row.week} className="border-t divider">
                <td className="py-1 pr-3">{row.week}</td>
                <td className="pr-3">{row.opponent_team ?? "—"}</td>
                {numericKeys.slice(0, 8).map((k) => (
                  <td key={k} className="pr-3 tabular-nums">
                    {row[k] == null ? "—" : typeof row[k] === "number" ? row[k].toFixed(row[k] % 1 === 0 ? 0 : 1) : String(row[k])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
