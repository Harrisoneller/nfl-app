import { pairColors } from "@/lib/team-colors";

/**
 * Two-color horizontal bar showing home vs. away win probability.
 * Casual-friendly visualization of "who's expected to win and by how much".
 *
 * Each half is tinted to its team's color, resolved from the team id/name props
 * via `pairColors` (which falls back to a team's secondary or a neutral when the
 * two primaries are too close to tell apart). Pass `awayColor`/`homeColor` to
 * override the automatic resolution.
 */
export function WinProbBar({
  awayTeam,
  awayProb,
  homeTeam,
  homeProb,
  awayColor,
  homeColor,
}: {
  awayTeam: string;
  awayProb: number;
  homeTeam: string;
  homeProb: number;
  awayColor?: string;
  homeColor?: string;
}) {
  const resolved = pairColors(awayTeam, homeTeam);
  const aColor = awayColor ?? resolved.away;
  const hColor = homeColor ?? resolved.home;
  const awayPct = Math.round(awayProb * 100);
  const homePct = Math.round(homeProb * 100);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">{awayTeam} <span className="text-muted">{awayPct}%</span></span>
        <span className="font-medium"><span className="text-muted">{homePct}%</span> {homeTeam}</span>
      </div>
      <div className="h-2 rounded-full overflow-hidden flex border divider">
        <div style={{ width: `${awayPct}%`, background: aColor }} />
        <div style={{ width: `${homePct}%`, background: hColor }} />
      </div>
    </div>
  );
}
