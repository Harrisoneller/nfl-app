import Link from "next/link";
import { GamePrediction } from "@/lib/api";
import { TeamLogo } from "@/components/TeamLogo";
import { WinProbBar } from "@/components/predictions/WinProbBar";

type Props = {
  season: number;
  games: GamePrediction[];
};

/**
 * Week 1 regular-season slate for the home page. Each row links to the
 * full H2H comparison at /h2h/[away]/[home].
 */
export function Week1Schedule({ season, games }: Props) {
  if (games.length === 0) {
    return (
      <EmptyState season={season} />
    );
  }

  const sorted = [...games].sort((a, b) => {
    const da = parseGameday(a.gameday)?.getTime() ?? 0;
    const db = parseGameday(b.gameday)?.getTime() ?? 0;
    return da - db || a.away_team_id.localeCompare(b.away_team_id);
  });

  return (
    <div className="space-y-2">
      {sorted.map((game) => (
        <MatchupPreviewRow key={game.id || `${game.away_team_id}-${game.home_team_id}`} game={game} />
      ))}
    </div>
  );
}

function EmptyState({ season }: { season: number }) {
  return (
    <div className="panel p-8 text-center border border-dashed divider">
      <p className="text-sm font-medium">Week 1 schedule coming soon</p>
      <p className="text-xs text-muted mt-2 max-w-md mx-auto">
        The {season > 0 ? season : "upcoming"} regular-season slate will appear here once schedule data is
        synced from nflverse (usually when the NFL releases it in May).
      </p>
    </div>
  );
}

/** Rich matchup preview row (Week 1 home + team overview "Next game"). */
export function MatchupPreviewRow({ game, showH2hHint = true }: { game: GamePrediction; showH2hHint?: boolean }) {
  const p = game.prediction;
  const fav = p.predicted_spread <= 0 ? game.home_team_id : game.away_team_id;
  const absSpread = Math.abs(p.predicted_spread);
  const eloDiff = Math.round(game.home_elo - game.away_elo);
  const played = game.home_score != null && game.away_score != null;
  const kickoff = formatKickoff(game.gameday, game.gametime);

  return (
    <Link
      href={`/h2h/${game.away_team_id}/${game.home_team_id}`}
      className="panel panel-hover-lift block p-4 group"
      aria-label={`${game.away_team_id} at ${game.home_team_id} head-to-head`}
    >
      <div className="flex flex-col sm:flex-row sm:items-center gap-4">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <TeamLogo teamId={game.away_team_id} size={36} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-lg group-hover:text-team-primary transition-colors">
                {game.away_team_id}
              </span>
              <span className="text-muted text-xs">@</span>
              <span className="font-bold text-lg group-hover:text-team-primary transition-colors">
                {game.home_team_id}
              </span>
            </div>
            {kickoff && (
              <p className="text-[11px] text-muted mt-0.5">{kickoff}</p>
            )}
          </div>
          <TeamLogo teamId={game.home_team_id} size={36} className="hidden sm:block shrink-0" />
        </div>

        {played ? (
          <div className="text-lg font-bold tabular-nums shrink-0">
            Final {game.away_score} – {game.home_score}
          </div>
        ) : (
          <div className="w-full sm:w-44 shrink-0">
            <WinProbBar
              awayTeam={game.away_team_id}
              awayProb={p.away_win_prob}
              homeTeam={game.home_team_id}
              homeProb={p.home_win_prob}
            />
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 shrink-0 w-full sm:w-auto sm:min-w-[280px]">
          <MetricPill
            label="Win prob"
            value={`${fav} ${Math.round((fav === game.home_team_id ? p.home_win_prob : p.away_win_prob) * 100)}%`}
          />
          <MetricPill
            label="Elo Δ"
            value={`${eloDiff >= 0 ? "+" : ""}${eloDiff}`}
            hint="home − away"
          />
          <MetricPill label="Spread" value={`${fav} -${absSpread.toFixed(1)}`} />
          <MetricPill
            label="Proj score"
            value={`${p.predicted_away_score.toFixed(0)}-${p.predicted_home_score.toFixed(0)}`}
          />
        </div>
      </div>

      {showH2hHint && (
        <p className="text-[10px] text-muted mt-3 sm:mt-2 group-hover:text-team-primary transition-colors">
          Full H2H breakdown →
        </p>
      )}
    </Link>
  );
}

function MetricPill({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="bg-bg/50 rounded-lg px-2.5 py-2 border divider">
      <div className="text-[9px] uppercase tracking-wide text-muted leading-none">
        {label}
        {hint && <span className="normal-case tracking-normal"> ({hint})</span>}
      </div>
      <div className="text-xs font-semibold tabular-nums mt-1 truncate">{value}</div>
    </div>
  );
}

function parseGameday(gameday: string): Date | null {
  if (!gameday) return null;
  const d = new Date(`${gameday}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatKickoff(gameday: string, gametime?: string): string | null {
  const d = parseGameday(gameday);
  if (!d) return gameday || null;
  if (gametime) {
    const parts = gametime.split(":");
    const h = Number(parts[0]);
    const m = Number(parts[1] ?? 0);
    if (!Number.isNaN(h)) {
      d.setHours(h, m, 0, 0);
      return d.toLocaleString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    }
  }
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}
