"use client";
import useSWR from "swr";
import { api, TeamSeasonOutlook } from "@/lib/api";
import { Card } from "../Card";

/**
 * One team's season outlook card: predicted record, division/playoff/SB odds.
 * Casual-friendly framing ("Eagles: 10.4 wins, 67% playoff odds") with the
 * exact Monte Carlo percentiles available below.
 */
export function SeasonOdds({ teamId, season }: { teamId: string; season?: number }) {
  const { data, isLoading } = useSWR(
    ["team-season-outlook", teamId, season ?? "default"],
    () => api.teamSeasonOutlook(teamId, season),
    { revalidateOnFocus: false },
  );

  if (isLoading || !data) {
    return (
      <Card title="Season outlook">
        <p className="text-sm text-muted">Running 10,000 Monte Carlo simulations…</p>
      </Card>
    );
  }
  if (data.mean_wins == null) {
    return (
      <Card title="Season outlook">
        <p className="text-sm text-muted">
          Predictions aren't available for this team yet. The Elo system runs once
          on backend startup — check back in ~60s, or hit
          {" "}<code className="text-xs">POST /predictions/admin/elo/rebuild</code>.
        </p>
      </Card>
    );
  }

  const winSentence = winsCommentary(data.mean_wins);
  const playoffSentence = playoffCommentary(data.playoff_pct ?? 0);

  return (
    <Card title={`Season outlook${data.season ? ` — ${data.season}` : ""}`}>
      <p className="text-sm leading-relaxed mb-3">
        Projected record:{" "}
        <span className="font-semibold tabular-nums">
          {data.mean_wins?.toFixed(1)}–{(17 - (data.mean_wins ?? 0)).toFixed(1)}
        </span>
        . {winSentence} {playoffSentence}
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
        <Stat label="Win division" value={pct(data.division_winner_pct)} hint={`p95: ${data.p95_wins} W`} />
        <Stat label="Make playoffs" value={pct(data.playoff_pct)} hint={`p5: ${data.p5_wins} W`} />
        <Stat label="Reach SB" value={pct(data.sb_appearance_pct)} hint={`median: ${data.median_wins} W`} />
        <Stat label="Elo grade" value={data.grade} hint={`rating ${Math.round(data.current_elo)}`} />
      </div>
    </Card>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="panel p-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-muted">{hint}</div>}
    </div>
  );
}

function pct(v: number | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}%`;
}

function winsCommentary(w: number | undefined): string {
  if (w == null) return "";
  if (w >= 12) return "Elite tier — championship contender.";
  if (w >= 10) return "Strong contender, should be in the playoff mix.";
  if (w >= 8) return "Hovering around .500 — competitive but not safe.";
  if (w >= 6) return "Long road — needs breaks to stay in it.";
  return "Tough year ahead — rebuilding mode.";
}

function playoffCommentary(p: number): string {
  if (p >= 80) return "Playoff lock barring disaster.";
  if (p >= 50) return "More likely than not to make it.";
  if (p >= 25) return "On the bubble.";
  return "Long shot.";
}
