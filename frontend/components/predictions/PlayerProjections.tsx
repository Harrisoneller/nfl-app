"use client";
import useSWR from "swr";
import { api, ProjectionEvidence } from "@/lib/api";
import { Card } from "@/components/Card";
import { playerMetricLabel } from "@/lib/metrics";

/**
 * PlayerProjections — game-by-game stat distributions + season outlook.
 * Backed by the v2 engine: Bayesian multi-year priors updated weekly, coupled
 * to the game model (implied points, game script, positional defense).
 */

const GRADE_COLORS: Record<string, string> = {
  A: "#22c55e", B: "#84cc16", C: "#eab308", D: "#f97316", F: "#ef4444",
};

const STAT_DISPLAY_ORDER: Record<string, string[]> = {
  QB: ["passing_yards", "passing_tds", "interceptions", "completions", "attempts", "rushing_yards"],
  RB: ["rushing_yards", "rushing_tds", "carries", "receptions", "receiving_yards"],
  WR: ["receiving_yards", "receiving_tds", "receptions", "targets"],
  TE: ["receiving_yards", "receiving_tds", "receptions", "targets"],
};

const SCORING_LABELS: Record<string, string> = {
  ppr: "PPR", half_ppr: "Half-PPR", standard: "Standard",
};

function EvidenceNote({ evidence }: { evidence?: ProjectionEvidence }) {
  if (!evidence) return null;
  const parts: string[] = [];
  if (evidence.games_observed > 0) {
    parts.push(`${evidence.games_observed} games observed this season`);
  }
  if (evidence.prior_games > 0) {
    parts.push(`prior from ${evidence.prior_games} games across ${evidence.prior_seasons.length} seasons`);
  }
  if (evidence.rookie_prior) parts.push("rookie archetype prior");
  if (parts.length === 0) return null;
  return (
    <p className="text-[10px] text-muted mt-2">
      Evidence: {parts.join(" · ")}. Projections shift toward observed play as games accrue.
    </p>
  );
}

export function PlayerSeasonProjectionCard({ playerId }: { playerId: string }) {
  const { data, isLoading } = useSWR(
    ["player-season-projection", playerId],
    () => api.playerSeasonProjection(playerId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Season projection">
        <p className="text-sm text-muted">Building priors + running the schedule…</p>
      </Card>
    );
  }
  if (data.error) {
    return (
      <Card title="Season projection">
        <div className="text-sm text-muted space-y-2">
          <p>{data.error}.</p>
          <p className="text-xs">
            This usually means nflverse hasn't published weekly data for the
            relevant seasons yet, or this player has no NFL usage on record.
          </p>
        </div>
      </Card>
    );
  }

  const order = STAT_DISPLAY_ORDER[data.position] || Object.keys(data.stats);
  const headlineStats = order.filter((k) => data.stats[k]).slice(0, 4);
  if (headlineStats.length === 0) {
    return (
      <Card title="Season projection">
        <p className="text-sm text-muted">No usable stats found for this player.</p>
      </Card>
    );
  }

  return (
    <Card
      title={`Season projection — ${data.season}`}
      action={
        <span className="text-[11px] text-muted">
          {data.games_played} played · {data.games_remaining} remaining
          {data.model_version ? ` · ${data.model_version}` : ""}
        </span>
      }
    >
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        {headlineStats.map((k) => {
          const s = data.stats[k];
          return (
            <div key={k} className="panel p-3">
              <div className="text-xs text-muted">{playerMetricLabel(k)}</div>
              <div className="text-2xl font-bold tabular-nums">{fmt(s.projected_final)}</div>
              <div className="text-[10px] text-muted">
                p10–p90: {fmt(s.quantiles?.p10 ?? s.low_final)}–{fmt(s.quantiles?.p90 ?? s.high_final)}
              </div>
              <div className="text-[10px] text-muted">
                Pace: {fmt(s.per_game_pace)} / game
              </div>
            </div>
          );
        })}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Stat</th>
              <th className="pr-3">YTD</th>
              <th className="pr-3">Per game</th>
              <th className="pr-3">Proj. remaining</th>
              <th className="pr-3">Proj. final</th>
              <th className="pr-3">p25–p75</th>
              <th className="pr-3">p10–p90</th>
            </tr>
          </thead>
          <tbody>
            {order.filter((k) => data.stats[k]).map((k) => {
              const s = data.stats[k];
              return (
                <tr key={k} className="border-t divider">
                  <td className="py-1 pr-3">{playerMetricLabel(k)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.ytd)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.per_game_pace)}</td>
                  <td className="pr-3 tabular-nums">{fmt(s.projected_remaining)}</td>
                  <td className="pr-3 tabular-nums font-medium">{fmt(s.projected_final)}</td>
                  <td className="pr-3 tabular-nums text-muted">{fmt(s.low_final)}–{fmt(s.high_final)}</td>
                  <td className="pr-3 tabular-nums text-muted">
                    {fmt(s.quantiles?.p10 ?? s.low_final)}–{fmt(s.quantiles?.p90 ?? s.high_final)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* Fantasy breakdown — supplemental, derived from the stat projections */}
      {data.fantasy && (
        <div className="mt-4">
          <div className="text-xs text-muted font-medium mb-2">
            Fantasy breakdown (derived from the stat projections above)
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {(["ppr", "half_ppr", "standard"] as const).map((fmtKey) => {
              const f = data.fantasy?.[fmtKey];
              if (!f) return null;
              return (
                <div key={fmtKey} className="panel p-3">
                  <div className="text-xs text-muted">{SCORING_LABELS[fmtKey]} points</div>
                  <div className="text-xl font-bold tabular-nums">{f.mean.toFixed(0)}</div>
                  <div className="text-[10px] text-muted">
                    p10–p90: {f.quantiles?.p10?.toFixed(0)}–{f.quantiles?.p90?.toFixed(0)}
                    {" · "}{f.per_game.toFixed(1)}/gm
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {data.role && data.role.multiplier < 1 && (
        <p className="text-[11px] text-amber-500 mt-3">
          Depth chart: {data.position}{data.role.depth_chart_order ?? "?"} — projections
          reflect a backup role (~{Math.round(data.role.multiplier * 100)}% of a
          starter's opportunity). If the depth chart changes, projections update
          on the next sync.
        </p>
      )}
      <EvidenceNote evidence={data.evidence} />
      <p className="text-[10px] text-muted mt-1">
        Bands separate two uncertainties: how wrong our per-game rate might be
        (correlated across the season) and week-to-week noise — the same
        hierarchical structure as the team season simulation.
      </p>
    </Card>
  );
}


export function PlayerGamePredictionsCard({ playerId }: { playerId: string }) {
  const { data, isLoading } = useSWR(
    ["player-game-predictions", playerId],
    () => api.playerGamePredictions(playerId),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Upcoming game projections">
        <p className="text-sm text-muted">Coupling to game model…</p>
      </Card>
    );
  }
  if (data.error) {
    return (
      <Card title="Upcoming game projections">
        <div className="text-sm text-muted space-y-2">
          <p>{data.error}.</p>
          <p className="text-xs">
            Projections require some NFL history (rookies get archetype priors
            once rosters + draft data sync) and an active team assignment.
          </p>
        </div>
      </Card>
    );
  }
  if (data.games.length === 0) {
    return (
      <Card title="Upcoming game projections">
        <p className="text-sm text-muted">
          No upcoming games found on this team's remaining schedule.
        </p>
      </Card>
    );
  }

  const order = STAT_DISPLAY_ORDER[data.position] || Object.keys(data.games[0]?.predicted || {});
  const cols = order.filter((k) => data.games[0]?.predicted?.[k]).slice(0, 5);

  return (
    <Card
      title="Upcoming game projections"
      action={
        <span className="text-[11px] text-muted">
          game-model coupled{data.model_version ? ` · ${data.model_version}` : ""}
        </span>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 pr-3">Wk</th>
              <th className="pr-3">vs/@</th>
              <th className="pr-3">Matchup</th>
              <th className="pr-3">Implied pts</th>
              <th className="pr-3">Script</th>
              {cols.map((k) => (
                <th key={k} className="pr-3">{playerMetricLabel(k)}</th>
              ))}
              <th className="pr-3">PPR</th>
            </tr>
          </thead>
          <tbody>
            {data.games.map((g, i) => (
              <tr key={i} className="border-t divider">
                <td className="py-1 pr-3">{g.week ?? "—"}</td>
                <td className="pr-3">
                  <span className="text-muted">{g.is_home ? "vs" : "@"}</span>{" "}
                  <span className="font-medium">{g.opponent}</span>
                </td>
                <td className="pr-3">
                  <span
                    className="inline-block rounded px-1.5 py-0.5 text-[10px] font-bold"
                    style={{
                      color: GRADE_COLORS[g.matchup_grade],
                      borderColor: GRADE_COLORS[g.matchup_grade],
                      border: "1px solid",
                    }}
                    title={`Opponent positional defense factor: ${g.defense_factor}`}
                  >
                    {g.matchup_grade}
                  </span>
                </td>
                <td
                  className="pr-3 tabular-nums"
                  title={`Opponent implied: ${g.game_env?.opp_implied_pts} · total ${g.game_env?.predicted_total}`}
                >
                  {g.game_env?.team_implied_pts?.toFixed?.(0) ?? "—"}
                </td>
                <td className="pr-3 text-muted">{g.game_env?.game_script ?? "—"}</td>
                {cols.map((k) => {
                  const p = g.predicted[k];
                  if (!p) return <td key={k} className="pr-3">—</td>;
                  const anchorNote = p.market_anchor
                    ? ` · anchored to market line ${p.market_anchor.line} (${p.market_anchor.books} books, raw ${p.market_anchor.raw_mean})`
                    : "";
                  return (
                    <td
                      key={k}
                      className="pr-3 tabular-nums"
                      title={`80% range: ${fmt(p.interval_80?.[0])}–${fmt(p.interval_80?.[1])}${p.anytime_prob != null ? ` · P(≥1) ${(p.anytime_prob * 100).toFixed(0)}%` : ""}${anchorNote}`}
                    >
                      {fmt(p.predicted)}
                      {p.market_anchor && <span className="text-[9px] ml-0.5" aria-label="market-anchored">⚓</span>}
                      <span className="text-[9px] text-muted ml-1">({fmt(p.low)}–{fmt(p.high)})</span>
                    </td>
                  );
                })}
                <td className="pr-3 tabular-nums font-medium">
                  {g.fantasy?.ppr ? g.fantasy.ppr.mean.toFixed(1) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <EvidenceNote evidence={data.evidence} />
      <p className="text-[10px] text-muted mt-1">
        Implied points + game script come from the game predictor (Elo + scoring
        tendencies); matchup grade from opponent positional defense (A = leaky,
        F = elite). Parentheses show the 50% range; hover for the 80% range and
        TD probabilities. Weather and injury status shift means when available.
      </p>
    </Card>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toFixed(1);
}
