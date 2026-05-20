"use client";
import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, H2HMatchup, MatchupSide, Team } from "@/lib/api";
import { Card } from "@/components/Card";
import { TeamLogo } from "@/components/TeamLogo";
import { EloBadge } from "@/components/predictions/EloBadge";
import { WinProbBar } from "@/components/predictions/WinProbBar";
import { MultiRadar, RadarSeries } from "@/components/charts/MultiRadar";
import { MultiTrendLine, TrendSeries } from "@/components/charts/MultiTrendLine";
import { TEAM_METRIC_LABELS, teamMetricFmt, teamMetricLabel } from "@/lib/metrics";

const OFFENSE_RADAR = [
  "off_epa_per_play", "off_success_rate", "off_explosive_play_rate",
  "off_red_zone_td_pct", "off_third_down_pct", "points_per_game",
];
const DEFENSE_RADAR = [
  "def_epa_per_play", "def_success_rate", "def_explosive_play_rate",
  "def_red_zone_td_pct", "def_yards_per_play", "points_allowed_per_game",
];

export default function H2HPage({ params }: { params: { a: string; b: string } }) {
  const router = useRouter();
  const a = params.a.toUpperCase();
  const b = params.b.toUpperCase();

  const { data: allTeams } = useSWR(["teams-list"], () => api.listTeams());
  const { data: teamA } = useSWR(["team", a], () => api.getTeam(a));
  const { data: teamB } = useSWR(["team", b], () => api.getTeam(b));
  const { data, isLoading, error } = useSWR(
    ["h2h", a, b],
    () => api.h2h(a, b),
    { revalidateOnFocus: false },
  );

  const swap = () => router.push(`/h2h/${b}/${a}`);
  const setA = (id: string) => id !== b && router.push(`/h2h/${id}/${b}`);
  const setB = (id: string) => id !== a && router.push(`/h2h/${a}/${id}`);

  return (
    <div className="space-y-6">
      <TeamSwitchBar teams={allTeams ?? []} a={a} b={b} setA={setA} setB={setB} onSwap={swap} />

      {(isLoading || !data) ? (
        <Card><p className="text-sm text-muted">Composing matchup view…</p></Card>
      ) : (error || data.error) ? (
        <Card><p className="text-sm text-red-400">{String(error || data.error)}</p></Card>
      ) : (
        <>
          <Banner data={data} teamA={teamA} teamB={teamB} />
          {data.predicted_matchup && <PredictionHero data={data} />}
          <MatchupBreakdownCard data={data} />
          <RadarSection data={data} teamA={teamA} teamB={teamB} />
          <DeltaTable data={data} />
          <HistorySection data={data} />
          <EloTrendCard data={data} />
        </>
      )}
    </div>
  );
}

// ============================================================================
// Selectors at the top — swap teams without leaving the page
// ============================================================================

function TeamSwitchBar({
  teams, a, b, setA, setB, onSwap,
}: {
  teams: Team[];
  a: string; b: string;
  setA: (id: string) => void;
  setB: (id: string) => void;
  onSwap: () => void;
}) {
  return (
    <div className="panel p-3 flex flex-wrap items-center gap-3">
      <span className="text-[11px] uppercase tracking-wider text-muted">Matchup</span>
      <TeamSelect teams={teams} value={a} exclude={b} onChange={setA} />
      <button
        onClick={onSwap}
        className="text-xs text-muted hover:text-text border divider rounded px-2 py-1"
        title="Swap teams"
      >
        ⇄
      </button>
      <TeamSelect teams={teams} value={b} exclude={a} onChange={setB} />
    </div>
  );
}

function TeamSelect({
  teams, value, exclude, onChange,
}: { teams: Team[]; value: string; exclude: string; onChange: (id: string) => void }) {
  const grouped = useMemo(() => {
    const out: Record<string, Team[]> = {};
    for (const t of teams) {
      const key = `${t.conference} ${t.division}`;
      out[key] ??= [];
      out[key].push(t);
    }
    for (const k of Object.keys(out)) out[k].sort((x, y) => x.full_name.localeCompare(y.full_name));
    return out;
  }, [teams]);
  return (
    <div className="flex items-center gap-2">
      <TeamLogo teamId={value} size={28} />
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-bg border divider rounded px-2 py-1.5 text-sm min-w-[180px]"
      >
        {Object.entries(grouped).sort().map(([div, ts]) => (
          <optgroup key={div} label={div}>
            {ts.map((t) => (
              <option key={t.id} value={t.id} disabled={t.id === exclude}>
                {t.full_name} ({t.id})
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}

// ============================================================================
// Banner with both teams + records + Elo
// ============================================================================

function Banner({ data, teamA, teamB }: { data: H2HMatchup; teamA: any; teamB: any }) {
  return (
    <div className="panel p-5 md:p-6 relative overflow-hidden">
      <div className="text-[11px] uppercase tracking-wider text-muted mb-3">
        Head-to-head · {data.season} season
      </div>
      <div className="grid grid-cols-3 items-center gap-4">
        <TeamCorner team={teamA} record={data.record.a} elo={data.elo.a} grade={data.grade.a} />
        <div className="text-center text-3xl font-bold text-muted">vs</div>
        <TeamCorner team={teamB} record={data.record.b} elo={data.elo.b} grade={data.grade.b} rightAligned />
      </div>
    </div>
  );
}

function TeamCorner({ team, record, elo, grade, rightAligned }: { team: any; record: any; elo: number; grade: string; rightAligned?: boolean }) {
  if (!team) return <div />;
  return (
    <div className={`flex items-center gap-3 ${rightAligned ? "flex-row-reverse text-right" : ""}`}>
      <TeamLogo teamId={team.id} size={64} />
      <div>
        <Link href={`/teams/${team.id}`} className="text-xl font-bold hover:underline block">
          {team.full_name}
        </Link>
        <div className="text-xs text-muted">{team.conference} {team.division}</div>
        <div className="mt-1 text-sm tabular-nums">
          {record.wins}-{record.losses}{record.ties ? `-${record.ties}` : ""}
        </div>
        <div className={`mt-1 inline-flex ${rightAligned ? "justify-end" : ""}`}>
          <EloBadge rating={elo} grade={grade} size="sm" />
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Game prediction (or hypothetical neutral-site simulation)
// ============================================================================

function PredictionHero({ data }: { data: H2HMatchup }) {
  const pred = data.predicted_matchup!;
  const isHypothetical = pred.hypothetical;
  const p = pred.prediction;
  const fav = p.predicted_spread <= 0 ? pred.home_team : pred.away_team;
  const absSpread = Math.abs(p.predicted_spread);

  return (
    <Card
      title={
        isHypothetical ? "Hypothetical neutral-site matchup" :
        pred.played ? "Most recent matchup" :
        "Upcoming meeting prediction"
      }
      action={
        pred.week && <span className="text-[11px] text-muted">Wk {pred.week}{pred.gameday ? ` · ${pred.gameday}` : ""}</span>
      }
    >
      <WinProbBar
        awayTeam={pred.away_team}
        awayProb={p.away_win_prob}
        homeTeam={pred.home_team}
        homeProb={p.home_win_prob}
      />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-sm">
        <Mini label="Spread" value={`${fav} -${absSpread.toFixed(1)}`} />
        <Mini label="Total" value={p.predicted_total.toFixed(1)} />
        <Mini label="Score" value={`${p.predicted_away_score.toFixed(0)}-${p.predicted_home_score.toFixed(0)}`} />
        <Mini label="Game script" value={(p as any).game_script || "—"} />
      </div>
      {pred.played && pred.home_score != null && (
        <p className="text-xs text-muted mt-3">
          Final: {pred.away_team} {pred.away_score} – {pred.home_team} {pred.home_score}
        </p>
      )}
      {isHypothetical && (
        <p className="text-xs text-muted mt-3">
          These teams aren't scheduled to play this season. This is a model-only simulation
          using current Elo ratings and scoring tendencies.
        </p>
      )}
    </Card>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg rounded px-2 py-2">
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}

// ============================================================================
// NEW: Cross-side matchup — offense vs opp defense, both directions
// ============================================================================

function MatchupBreakdownCard({ data }: { data: H2HMatchup }) {
  const a = data.matchup_breakdown.when_a_has_ball;
  const b = data.matchup_breakdown.when_b_has_ball;
  if (a.rows.length === 0 && b.rows.length === 0) return null;

  return (
    <Card
      title="Strength vs weakness"
      action={<span className="text-[11px] text-muted">Each team's offense vs the other's defense</span>}
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <MatchupSidePanel side={a} />
        <MatchupSidePanel side={b} />
      </div>
      <p className="text-[10px] text-muted mt-3">
        "Expected" = midpoint of the offense's season pace and the defense's
        season-allowed average. Green delta = offense projects to outperform
        what the defense typically allows.
      </p>
    </Card>
  );
}

function MatchupSidePanel({ side }: { side: MatchupSide }) {
  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">
          When <span className="text-team-primary">{side.offense}</span> has the ball
        </h3>
        <span className="text-[11px] text-muted">
          {side.advantage_count}/{side.metrics_count} edges
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-2">Metric</th>
              <th className="pr-2 text-right">{side.offense} off</th>
              <th className="pr-2 text-right">{side.defense} def</th>
              <th className="pr-2 text-right">Expected</th>
              <th className="pr-2 text-right">Δ</th>
            </tr>
          </thead>
          <tbody>
            {side.rows.map((r) => (
              <tr key={r.metric} className="border-t divider">
                <td className="py-1 pr-2">{r.label}</td>
                <td className="pr-2 text-right tabular-nums">{fmtVal(r.metric, r.off_value)}</td>
                <td className="pr-2 text-right tabular-nums">{fmtVal(r.metric, r.def_value)}</td>
                <td className="pr-2 text-right tabular-nums font-medium">{fmtVal(r.metric, r.expected)}</td>
                <td
                  className="pr-2 text-right tabular-nums font-medium"
                  style={{ color: r.offense_has_edge ? "#22c55e" : "#f97316" }}
                >
                  {r.delta > 0 ? "+" : ""}{r.delta.toFixed(r.metric === "points_per_game" ? 1 : 3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmtVal(metricKey: string, v: number): string {
  // Reuse the team-metric formatter — but matchup table uses the offense key
  // so percentages render correctly.
  return teamMetricFmt(metricKey, v);
}

// ============================================================================
// Existing sections (radars, delta table, history, Elo trends)
// ============================================================================

function RadarSection({ data, teamA, teamB }: { data: H2HMatchup; teamA: any; teamB: any }) {
  const profileA = data.profile.a;
  const profileB = data.profile.b;
  if (!profileA?.metrics || !profileB?.metrics) return null;

  const buildSeries = (keys: string[], _isDefense: boolean): RadarSeries[] => {
    // Backend percentiles are already "higher = better for this team" for both
    // offensive and defensive metrics, so we plot them directly. No inversion.
    const valuesFor = (p: any): Record<string, number | null> => {
      const out: Record<string, number | null> = {};
      for (const k of keys) {
        const m = p?.metrics?.[k];
        out[teamMetricLabel(k)] = m?.percentile ?? null;
      }
      return out;
    };
    return [
      { name: data.team_a, color: teamA?.primary_color || "#22d3ee", values: valuesFor(profileA) },
      { name: data.team_b, color: teamB?.primary_color || "#f97316", values: valuesFor(profileB) },
    ];
  };

  const offKeys = OFFENSE_RADAR.filter((k) => profileA.metrics[k] && profileB.metrics[k]);
  const defKeys = DEFENSE_RADAR.filter((k) => profileA.metrics[k] && profileB.metrics[k]);

  return (
    <Card title={`Profile comparison — ${profileA.season}`}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm text-muted mb-2">Offense (percentile)</h3>
          <MultiRadar metrics={offKeys.map(teamMetricLabel)} series={buildSeries(offKeys, false)} />
        </div>
        <div>
          <h3 className="text-sm text-muted mb-2">Defense (higher = better)</h3>
          <MultiRadar metrics={defKeys.map(teamMetricLabel)} series={buildSeries(defKeys, true)} />
        </div>
      </div>
    </Card>
  );
}

function DeltaTable({ data }: { data: H2HMatchup }) {
  const rows = useMemo(() => {
    return [...data.profile.deltas]
      .filter((d) => d.a_value != null && d.b_value != null)
      .sort((x, y) => y.delta - x.delta);
  }, [data]);
  if (rows.length === 0) return null;
  return (
    <Card title="Direct stat comparison" action={<span className="text-[11px] text-muted">Same-side metrics, sorted by biggest gaps</span>}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Metric</th>
              <th className="pr-3 text-right">{data.team_a}</th>
              <th className="pr-3 text-center w-10">Edge</th>
              <th className="pr-3 text-right">{data.team_b}</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 18).map((r) => (
              <tr key={r.metric} className="border-t divider">
                <td className="py-1.5 pr-3">{teamMetricLabel(r.metric)}</td>
                <td className={`pr-3 text-right tabular-nums ${r.winner === "a" ? "font-semibold text-emerald-400" : ""}`}>
                  {teamMetricFmt(r.metric, r.a_value)}
                  {r.a_percentile != null && (
                    <span className="text-[10px] text-muted ml-1">({r.a_percentile.toFixed(0)})</span>
                  )}
                </td>
                <td className="pr-3 text-center text-xs text-muted">
                  {r.winner ? (r.winner === "a" ? "←" : "→") : "="}
                </td>
                <td className={`pr-3 text-right tabular-nums ${r.winner === "b" ? "font-semibold text-emerald-400" : ""}`}>
                  {teamMetricFmt(r.metric, r.b_value)}
                  {r.b_percentile != null && (
                    <span className="text-[10px] text-muted ml-1">({r.b_percentile.toFixed(0)})</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function HistorySection({ data }: { data: H2HMatchup }) {
  if (data.history.games.length === 0) {
    return (
      <Card title="Head-to-head history">
        <p className="text-sm text-muted">No completed games between these teams in the last 8 seasons.</p>
      </Card>
    );
  }
  return (
    <Card title="Head-to-head history" action={
      <span className="text-xs tabular-nums text-muted">
        {data.team_a} {data.history.a_wins} - {data.history.b_wins} {data.team_b}
        {data.history.ties ? ` (${data.history.ties} ties)` : ""}
      </span>
    }>
      <ul className="text-sm divide-y divider">
        {data.history.games.map((g, i) => {
          const winnerIsA = g.winner === data.team_a;
          return (
            <li key={i} className="py-1.5 flex items-center justify-between gap-3">
              <span className="text-muted text-xs w-16">{g.season} Wk {g.week ?? "?"}</span>
              <span className="flex-1 tabular-nums">
                <span className={winnerIsA && g.away_team === data.team_a ? "font-semibold" :
                                 !winnerIsA && g.away_team === data.team_b ? "font-semibold" : ""}>
                  {g.away_team} {g.away_score}
                </span>
                <span className="text-muted mx-1">@</span>
                <span className={winnerIsA && g.home_team === data.team_a ? "font-semibold" :
                                 !winnerIsA && g.home_team === data.team_b ? "font-semibold" : ""}>
                  {g.home_team} {g.home_score}
                </span>
              </span>
              <span className="text-xs text-muted tabular-nums">
                {g.spread_line != null ? `Spread ${g.spread_line > 0 ? "+" : ""}${g.spread_line.toFixed(1)}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function EloTrendCard({ data }: { data: H2HMatchup }) {
  const series: TrendSeries[] = [
    {
      name: data.team_a, color: "#22d3ee",
      points: data.elo_history.a.map((p) => ({ season: p.season * 100 + p.week, value: p.rating })),
    },
    {
      name: data.team_b, color: "#f97316",
      points: data.elo_history.b.map((p) => ({ season: p.season * 100 + p.week, value: p.rating })),
    },
  ];
  if (series.every((s) => s.points.length === 0)) return null;
  return (
    <Card title="Elo trajectories" action={<span className="text-[11px] text-muted">Per-week ratings, all available seasons</span>}>
      <MultiTrendLine series={series} yLabel="Elo rating" />
    </Card>
  );
}
