/**
 * Two-color horizontal bar showing home vs. away win probability.
 * Casual-friendly visualization of "who's expected to win and by how much".
 */
export function WinProbBar({
  awayTeam,
  awayProb,
  homeTeam,
  homeProb,
  awayColor = "#475569",
  homeColor = "var(--team-primary)",
}: {
  awayTeam: string;
  awayProb: number;
  homeTeam: string;
  homeProb: number;
  awayColor?: string;
  homeColor?: string;
}) {
  const awayPct = Math.round(awayProb * 100);
  const homePct = Math.round(homeProb * 100);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">{awayTeam} <span className="text-muted">{awayPct}%</span></span>
        <span className="font-medium"><span className="text-muted">{homePct}%</span> {homeTeam}</span>
      </div>
      <div className="h-2 rounded-full overflow-hidden flex border divider">
        <div style={{ width: `${awayPct}%`, background: awayColor }} />
        <div style={{ width: `${homePct}%`, background: homeColor }} />
      </div>
    </div>
  );
}
