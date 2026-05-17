import Link from "next/link";
import { api, GamePrediction } from "@/lib/api";
import { Card } from "@/components/Card";
import { PredictionCard } from "@/components/predictions/PredictionCard";
import { EloBadge } from "@/components/predictions/EloBadge";
import { WinProbBar } from "@/components/predictions/WinProbBar";
import { AwardRaceCard } from "@/components/predictions/AwardRaceCard";
import { LeagueBestBetsCard } from "@/components/betting/LeagueBestBets";
import { TeamLogo } from "@/components/TeamLogo";

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

// Pick the most compelling game of the week: closest predicted win prob,
// preferring games involving top-10 Elo teams.
function pickFeatured(games: GamePrediction[], topTeams: Set<string>): GamePrediction | undefined {
  if (games.length === 0) return undefined;
  const upcoming = games.filter((g) => g.home_score == null);
  if (upcoming.length === 0) return undefined;
  const scored = upcoming.map((g) => {
    const competitive = 1 - Math.abs(g.prediction.home_win_prob - 0.5) * 2; // 0..1
    const topMatch = (topTeams.has(g.home_team_id) ? 1 : 0) + (topTeams.has(g.away_team_id) ? 1 : 0);
    return { g, score: competitive * 2 + topMatch };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored[0].g;
}

export default async function HomePage() {
  const [scoreboard, predictions, news, standings, eloRatings, trendingAdds, widgets] =
    await Promise.all([
      safe(api.scoreboard(12), []),
      safe(api.predictGames(undefined, undefined, true), { season: 0, week: null, games: [] }),
      safe(api.news(8), []),
      safe(api.projectedStandings(), { season: 0, divisions: [] }),
      safe(api.currentElo(), { ratings: [] }),
      safe(api.fantasyTrending("add", 6), { kind: "add", items: [] }),
      safe(api.listWidgets(), []),
    ]);

  const topTeamIds = new Set(eloRatings.ratings.slice(0, 10).map((r) => r.team_id));
  const featured = pickFeatured(predictions.games, topTeamIds);
  const otherGames = featured
    ? predictions.games.filter((g) => g.id !== featured.id).slice(0, 8)
    : predictions.games.slice(0, 9);
  const hasLiveGames = scoreboard.some((g) => g.status === "in" || g.status === "live");

  return (
    <div className="space-y-7">
      {/* ============ HERO ============ */}
      {featured ? (
        <FeaturedGame game={featured} />
      ) : predictions.games.length > 0 ? (
        <p className="text-xl font-semibold">
          {hasLiveGames ? "Live now" : predictions.week ? `Week ${predictions.week}` : "This week"}
        </p>
      ) : null}

      {/* ============ THE WEEK ============ */}
      {otherGames.length > 0 && (
        <section>
          <div className="flex items-end justify-between mb-3">
            <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
              The rest of the slate
            </h2>
            <Link href="/odds" className="text-xs text-muted hover:underline">Full board →</Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {otherGames.map((g) => (
              <PredictionCard key={g.id || `${g.home_team_id}-${g.away_team_id}`} game={g} />
            ))}
          </div>
        </section>
      )}

      {/* ============ STORYLINES + POWER RANKINGS ============ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Today's storylines" className="lg:col-span-2">
          {news.length === 0 ? (
            <p className="text-sm text-muted">News populates on the next refresh (~5 min).</p>
          ) : (
            <ul className="space-y-3 text-sm">
              {news.map((n, i) => (
                <li key={n.id} className={i === 0 ? "pb-3 border-b divider" : ""}>
                  <a href={n.link} target="_blank" rel="noreferrer" className="hover:underline block">
                    <div className={i === 0 ? "text-base font-medium" : ""}>{n.title}</div>
                    <div className="text-[11px] text-muted mt-0.5">
                      {n.source_label}
                      {n.published_at && ` · ${timeAgo(n.published_at)}`}
                    </div>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Power rankings"
          action={<span className="text-[11px] text-muted">via Elo</span>}
        >
          {eloRatings.ratings.length === 0 ? (
            <p className="text-sm text-muted">Ratings build on first boot.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {eloRatings.ratings.slice(0, 12).map((r, i) => (
                <li key={r.team_id} className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-muted tabular-nums w-5 text-right">{i + 1}</span>
                    <TeamLogo teamId={r.team_id} size={20} />
                    <Link href={`/teams/${r.team_id}`} className="hover:underline font-medium truncate">
                      {r.team_id}
                    </Link>
                  </div>
                  <EloBadge rating={r.rating} grade={r.grade} size="sm" showRating={false} />
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      {/* ============ PROJECTED STANDINGS ============ */}
      {standings.divisions.length > 0 && (
        <Card title="Projected standings" action={<span className="text-[11px] text-muted">10,000 Monte Carlo sims</span>}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4">
            {standings.divisions.map((d) => (
              <div key={`${d.conference}-${d.division}`}>
                <h3 className="text-xs uppercase tracking-wide text-muted mb-2">
                  {d.conference} {d.division}
                </h3>
                <ul className="space-y-1 text-sm">
                  {d.teams.map((t, i) => (
                    <li key={t.team_id} className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-muted text-xs w-3 text-right">{i + 1}</span>
                        <TeamLogo teamId={t.team_id} size={18} />
                        <Link href={`/teams/${t.team_id}`} className="hover:underline font-medium">
                          {t.team_id}
                        </Link>
                      </div>
                      <span className="text-[11px] tabular-nums">
                        <span className="font-medium">{t.mean_wins.toFixed(1)}W</span>
                        <span className="text-muted ml-1.5">{t.playoff_pct.toFixed(0)}% PO</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ============ AWARDS + BEST BETS ============ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <AwardRaceCard />
        <LeagueBestBetsCard />
      </div>

      {/* ============ PERSONA SNIPPETS ============ */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Fantasy: trending adds" action={<Link href="/fantasy" className="text-[11px] hover:underline">More →</Link>}>
          {trendingAdds.items.length === 0 ? (
            <p className="text-sm text-muted">Sleeper refreshes every 5 min.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {trendingAdds.items.slice(0, 6).map((r: any) => (
                <li key={r.player_id} className="flex justify-between">
                  <span className="truncate pr-2">
                    {r.player_id ? (
                      <Link href={`/players/${r.player_id}`} className="hover:underline font-medium">
                        {r.name ?? r.player_id}
                      </Link>
                    ) : (
                      <span>{r.name ?? r.player_id}</span>
                    )}
                    <span className="text-muted text-xs ml-2">{r.position} · {r.team ?? "—"}</span>
                  </span>
                  <span className="text-muted text-xs tabular-nums whitespace-nowrap">
                    +{r.count?.toLocaleString?.()}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <Card title="Ask the AI" action={<Link href="/ai" className="text-[11px] hover:underline">Open →</Link>}>
          <p className="text-sm text-muted mb-2">Specific questions, real data, instant answer.</p>
          <ul className="text-sm space-y-1 text-muted">
            <li>"Compare PHI and SF rushing efficiency"</li>
            <li>"Best red-zone defenses this year"</li>
            <li>"Build a widget of QB EPA leaders"</li>
          </ul>
        </Card>

        <Card title="Your widgets" action={<Link href="/ai" className="text-[11px] hover:underline">Build →</Link>}>
          {widgets.length === 0 ? (
            <p className="text-sm text-muted">
              Use the AI page to create a widget. Saved widgets appear here.
            </p>
          ) : (
            <ul className="space-y-1 text-sm">
              {widgets.slice(0, 6).map((w) => (
                <li key={w.id}>
                  <Link href={`/widget/${w.id}`} className="hover:underline">{w.title}</Link>
                  <span className="text-muted text-xs"> · {w.kind}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function FeaturedGame({ game }: { game: GamePrediction }) {
  const p = game.prediction;
  const fav = p.predicted_spread <= 0 ? game.home_team_id : game.away_team_id;
  const absSpread = Math.abs(p.predicted_spread);
  return (
    <section className="panel p-5 md:p-7 relative overflow-hidden">
      <div
        className="absolute inset-0 opacity-[0.06] pointer-events-none"
        style={{
          background: "radial-gradient(ellipse at top left, var(--team-primary), transparent 60%)",
        }}
      />
      <div className="relative">
        <div className="text-[11px] uppercase tracking-wider text-muted mb-2">
          Featured · Wk {game.week ?? "—"}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-3">
            <TeamLogo teamId={game.away_team_id} size={48} />
            <div>
              <div className="text-2xl font-bold">{game.away_team_id}</div>
              <div className="text-[11px] text-muted">Elo {Math.round(game.away_elo)}</div>
            </div>
          </div>
          <div className="text-muted text-lg">@</div>
          <div className="flex items-center gap-3 flex-row-reverse">
            <TeamLogo teamId={game.home_team_id} size={48} />
            <div className="text-right">
              <div className="text-2xl font-bold">{game.home_team_id}</div>
              <div className="text-[11px] text-muted">Elo {Math.round(game.home_elo)}</div>
            </div>
          </div>
        </div>
        <WinProbBar
          awayTeam={game.away_team_id}
          awayProb={p.away_win_prob}
          homeTeam={game.home_team_id}
          homeProb={p.home_win_prob}
        />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 text-sm">
          <Stat label="Pick" value={`${fav} -${absSpread.toFixed(1)}`} />
          <Stat label="Total" value={p.predicted_total.toFixed(1)} />
          <Stat label="Predicted score" value={`${p.predicted_away_score.toFixed(0)}-${p.predicted_home_score.toFixed(0)}`} />
          <Stat
            label="ML model"
            value={
              game.ml_prediction
                ? `${game.ml_prediction.predicted_spread <= 0 ? game.home_team_id : game.away_team_id} -${Math.abs(game.ml_prediction.predicted_spread).toFixed(1)}`
                : "—"
            }
          />
        </div>
        <p className="text-[11px] text-muted mt-3">{game.gameday}</p>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-muted uppercase tracking-wide">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diff = Date.now() - t;
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
