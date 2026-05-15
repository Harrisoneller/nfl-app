"use client";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, Player, PlayerProfile } from "@/lib/api";
import { Card } from "@/components/Card";
import { SeasonSelect } from "@/components/SeasonSelect";
import { PercentileBar } from "@/components/charts/PercentileBar";
import { RadarProfile } from "@/components/charts/RadarProfile";
import { TrendLine } from "@/components/charts/TrendLine";
import { GameLogBar } from "@/components/charts/GameLogBar";
import { MultiRadar, RadarSeries } from "@/components/charts/MultiRadar";
import { MultiTrendLine, TrendSeries } from "@/components/charts/MultiTrendLine";
import { ComparisonPicker, Pickable } from "@/components/ComparisonPicker";
import { LiveFeed } from "@/components/LiveFeed";
import { SkeletonRadar, SkeletonTable } from "@/components/Skeleton";
import { pickColor } from "@/lib/colors";
import { PLAYER_METRIC_LABELS, playerMetricFmt, playerMetricLabel } from "@/lib/metrics";
import { readOverlayParams, syncUrlOverlays } from "@/lib/overlay-url";

const RADAR_KEYS_BY_POS: Record<string, string[]> = {
  QB: ["completion_pct", "yards_per_attempt", "passer_rating", "epa_per_play", "success_rate", "passing_tds", "rushing_yards"],
  RB: ["yards_per_carry", "rushing_yards", "snap_share", "target_share", "epa_per_touch", "success_rate", "fantasy_points_ppr"],
  WR: ["target_share", "air_yards_share", "yards_per_target", "catch_rate", "wopr", "racr", "yac", "fantasy_points_ppr"],
  TE: ["target_share", "air_yards_share", "yards_per_target", "catch_rate", "wopr", "racr", "yac", "fantasy_points_ppr"],
};

export default function PlayerPage({ params }: { params: { id: string } }) {
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
  const { data: overlayGamelogs } = useSWR(
    season && overlays.length ? ["player-overlay-gamelogs", params.id, season, overlays.map((o) => o.id).join(",")] : null,
    () => Promise.all(overlays.map((o) => api.getPlayerGamelog(o.id, season))),
  );

  // Hydrate from URL on mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const parsed = readOverlayParams(new URLSearchParams(window.location.search));
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

  // Sync URL whenever overlay set / season / trend metric changes
  useEffect(() => {
    syncUrlOverlays(coloredOverlays, season, trendMetric);
  }, [coloredOverlays, season, trendMetric]);

  if (!player) return <p className="text-sm text-muted">Loading…</p>;
  const pos = (player.position || "").toUpperCase();
  const metricKeys = Object.keys(profile?.metrics || {});
  const positionMetrics = profileLoading ? [] : metricKeys.filter((k) => PLAYER_METRIC_LABELS[k]);

  const CATS: Record<string, string[]> = {
    Volume: [
      "games", "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
      "carries", "rushing_yards", "rushing_tds",
      "targets", "receptions", "receiving_yards", "receiving_tds",
    ],
    Opportunity: ["snap_share", "target_share", "air_yards_share", "red_zone_carries", "adot"],
    Efficiency: [
      "completion_pct", "yards_per_attempt", "passer_rating", "sack_rate",
      "yards_per_carry", "yards_per_reception", "yards_per_target", "catch_rate", "yac",
    ],
    "Advanced / EPA": ["epa_per_play", "epa_per_touch", "success_rate", "cpoe", "racr", "wopr"],
    "Fantasy": ["fantasy_points_ppr"],
  };
  const grouped = Object.fromEntries(
    Object.entries(CATS).map(([cat, keys]) => [cat, keys.filter((k) => positionMetrics.includes(k))]),
  );

  return (
    <div className="space-y-6">
      <Header player={player} profile={profile} season={season} setSeason={setSeason} />

      <LiveFeed
        title="Latest mentions"
        cacheKey={["player-news", params.id]}
        fetcher={() => api.getPlayerNews(params.id, 20)}
        emptyText={`No recent mentions of ${player.full_name}. Feed refreshes every 60s.`}
      />

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
        <p className="text-xs text-muted">
          Add up to 3 players to overlay on the radar + trend + game log below. Same-position
          comparisons are most useful, but anyone works.
        </p>
      </Card>

      <RadarSection
        profile={profile}
        positionMetrics={positionMetrics}
        pos={pos}
        overlays={coloredOverlays}
        overlayProfiles={overlayProfiles}
      />

      {(["Volume", "Opportunity", "Efficiency", "Advanced / EPA", "Fantasy"] as const).map((cat) =>
        grouped[cat]?.length > 0 ? (
          <Card key={cat} title={cat}>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1">
              {grouped[cat].map((k) => {
                const m = profile?.metrics?.[k];
                if (!m) return null;
                const p = m.higher_is_better
                  ? m.percentile
                  : m.percentile == null ? null : 100 - m.percentile;
                return (
                  <PercentileBar
                    key={k}
                    label={playerMetricLabel(k)}
                    value={playerMetricFmt(k, m.value)}
                    percentile={p}
                  />
                );
              })}
            </div>
          </Card>
        ) : null,
      )}

      <TrendSection
        playerName={player.full_name}
        metric={trendMetric}
        setMetric={setTrendMetric}
        availableMetrics={positionMetrics}
        trend={trend}
        overlays={coloredOverlays}
        overlayTrends={overlayTrends}
      />

      <GameLogSection
        playerName={player.full_name}
        gamelog={gamelog || []}
        metric={gamelogMetric}
        setMetric={setGamelogMetric}
        overlays={coloredOverlays}
        overlayGamelogs={overlayGamelogs}
      />
    </div>
  );
}

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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">{player.full_name}</h1>
          <div className="mt-1 text-sm text-muted flex flex-wrap gap-3">
            <span>{player.position}</span>
            {player.team_id && (
              <Link href={`/teams/${player.team_id}`} className="hover:underline">{player.team_id}</Link>
            )}
            <span>#{player.jersey_number ?? "—"}</span>
            {player.age && <span>Age {player.age}</span>}
            {player.height && <span>{player.height}</span>}
            {player.weight && <span>{player.weight} lbs</span>}
            {player.college && <span>{player.college}</span>}
          </div>
          {profile && !profile.error && (
            <div className="mt-1 text-xs text-muted">
              Compared against {profile.peer_count} {profile.position}s in {profile.season}.
            </div>
          )}
        </div>
        <SeasonSelect value={season} onChange={setSeason} />
      </div>
    </Card>
  );
}

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
        <p className="text-sm text-muted">
          {profile?.error
            ? `${profile.error} — pick a different season or wait for the data sync to complete.`
            : "Loading profile…"}
        </p>
      </Card>
    );
  }

  const keys = (RADAR_KEYS_BY_POS[profile.position] || RADAR_KEYS_BY_POS.WR).filter((k) =>
    positionMetrics.includes(k),
  );
  const labels = keys.map((k) => playerMetricLabel(k));

  const valuesFor = (p: any): Record<string, number | null> => {
    const out: Record<string, number | null> = {};
    for (const k of keys) {
      const m = p?.metrics?.[k];
      const v = m?.higher_is_better ? m?.percentile : (m?.percentile == null ? null : 100 - m.percentile);
      out[playerMetricLabel(k)] = v ?? null;
    }
    return out;
  };

  if (overlays.length === 0) {
    const data = keys.map((k) => {
      const m = profile.metrics[k];
      const p = m.higher_is_better ? m.percentile : m.percentile == null ? null : 100 - m.percentile;
      return { metric: playerMetricLabel(k), percentile: p };
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

function TrendSection({
  playerName, metric, setMetric, availableMetrics, trend, overlays, overlayTrends,
}: {
  playerName: string;
  metric: string;
  setMetric: (m: string) => void;
  availableMetrics: string[];
  trend: any;
  overlays: Pickable[];
  overlayTrends: any[] | undefined;
}) {
  const myPoints = useMemo(
    () => (trend?.points || []).filter((p: any) => p.value != null),
    [trend],
  );

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
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
          className="bg-bg border divider rounded px-2 py-1 text-xs"
        >
          {availableMetrics.map((k) => (
            <option key={k} value={k}>{playerMetricLabel(k)}</option>
          ))}
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
  playerName, gamelog, metric, setMetric, overlays, overlayGamelogs,
}: {
  playerName: string;
  gamelog: any[];
  metric: string;
  setMetric: (m: string) => void;
  overlays: Pickable[];
  overlayGamelogs: any[][] | undefined;
}) {
  if (gamelog.length === 0) {
    return (
      <Card title="Game log">
        <p className="text-sm text-muted">No weekly data for this season yet.</p>
      </Card>
    );
  }
  const numericKeys = Object.keys(gamelog[0]).filter(
    (k) => k !== "week" && k !== "opponent_team" && typeof gamelog[0][k] === "number",
  );

  // Multi-series view: turn each gamelog into [{ season: week, value }]-shaped points
  const series: TrendSeries[] = useMemo(() => {
    const mk = (rows: any[], name: string, color: string): TrendSeries => ({
      name, color,
      points: rows
        .filter((r) => r[metric] != null)
        .map((r) => ({ season: r.week, value: typeof r[metric] === "number" ? r[metric] : null })),
    });
    const out: TrendSeries[] = [mk(gamelog, playerName, "#22d3ee")];
    if (overlayGamelogs) {
      overlays.forEach((o, i) => {
        const g = overlayGamelogs[i];
        if (Array.isArray(g) && g.length) out.push(mk(g, o.label, o.color));
      });
    }
    return out;
  }, [gamelog, overlayGamelogs, overlays, metric, playerName]);

  return (
    <Card
      title="Game log"
      action={
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
          className="bg-bg border divider rounded px-2 py-1 text-xs"
        >
          {numericKeys.map((k) => (
            <option key={k} value={k}>{playerMetricLabel(k) || k}</option>
          ))}
        </select>
      }
    >
      <MultiTrendLine series={series} yLabel={playerMetricLabel(metric) || metric} />
      <div className="overflow-x-auto mt-3">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">Opp</th>
              {numericKeys.slice(0, 8).map((k) => (
                <th key={k} className="pr-3">{playerMetricLabel(k) || k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {gamelog.map((row) => (
              <tr key={row.week} className="border-t divider">
                <td className="py-1 pr-3">{row.week}</td>
                <td className="pr-3">{row.opponent_team ?? "—"}</td>
                {numericKeys.slice(0, 8).map((k) => (
                  <td key={k} className="pr-3 tabular-nums">
                    {row[k] == null
                      ? "—"
                      : typeof row[k] === "number"
                        ? row[k].toFixed(row[k] % 1 === 0 ? 0 : 1)
                        : String(row[k])}
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
