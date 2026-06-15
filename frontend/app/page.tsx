import Link from "next/link";
import { Suspense } from "react";
import { api, Game, GamePrediction } from "@/lib/api";
import { Card } from "@/components/Card";
import { PredictionCard } from "@/components/predictions/PredictionCard";
import { EloBadge } from "@/components/predictions/EloBadge";
import { WinProbBar } from "@/components/predictions/WinProbBar";
import { AwardRaceCard } from "@/components/predictions/AwardRaceCard";
import { LeagueBestBetsCard } from "@/components/betting/LeagueBestBets";
import { TeamLogo } from "@/components/TeamLogo";
import { LeaguePulse } from "@/components/LeaguePulse";
import { WelcomeHero } from "@/components/home/WelcomeHero";
import { Week1Schedule } from "@/components/Week1Schedule";
import { FreshnessBadges } from "@/components/FreshnessBadges";
import { ExperimentedInsightCards } from "@/components/home/ExperimentedInsightCards";
import { PersonaGate } from "@/components/persona/PersonaGate";

export const revalidate = 60;

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

function pickFeatured(games: GamePrediction[], topTeams: Set<string>): GamePrediction | undefined {
  if (games.length === 0) return undefined;
  const upcoming = games.filter((g) => g.home_score == null);
  if (upcoming.length === 0) return undefined;
  const scored = upcoming.map((g) => {
    const competitive = 1 - Math.abs(g.prediction.home_win_prob - 0.5) * 2;
    const topMatch = (topTeams.has(g.home_team_id) ? 1 : 0) + (topTeams.has(g.away_team_id) ? 1 : 0);
    return { g, score: competitive * 2 + topMatch };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored[0].g;
}

// /fantasy and /ai hidden from quick links until ready — routes still work via direct URL
const QUICK_LINKS = [
  { href: "/teams", label: "Teams", Icon: TeamsIcon },
  { href: "/odds", label: "Odds", Icon: OddsIcon },
  { href: "/h2h/PHI/SF", label: "H2H", Icon: H2HIcon },
] as const;

export default async function HomePage() {
  const [scoreboard, predictions, week1Predictions, eloRatings, freshness] =
    await Promise.all([
      safe(api.scoreboard(12, { revalidate: 15 }), []),
      safe(api.predictGames(undefined, undefined, true, { revalidate: 60 }), { season: 0, week: null, games: [] }),
      safe(api.predictGames(undefined, 1, true, { revalidate: 1800 }), { season: 0, week: 1, games: [] }),
      safe(api.currentElo({ revalidate: 300 }), { ratings: [] }),
      safe(api.freshness({ revalidate: 60 }), null),
    ]);

  const topTeamIds = new Set(eloRatings.ratings.slice(0, 10).map((r) => r.team_id));
  const featured = pickFeatured(predictions.games, topTeamIds);
  const week1Season = week1Predictions.season || predictions.season;
  const hasWeek1Games = week1Predictions.games.length > 0;
  const otherGames = (featured
    ? predictions.games.filter((g) => g.id !== featured.id)
    : predictions.games
  )
    .filter((g) => !(hasWeek1Games && predictions.week === 1 && g.week === 1))
    .slice(0, 8);
  const hasLiveGames = scoreboard.some((g) => g.status === "in" || g.status === "live");
  const liveGames = scoreboard.filter((g) => g.status === "in" || g.status === "live");
  const weekLabel = predictions.week ? `Week ${predictions.week}` : null;
  const tossup = predictions.games
    .filter((g) => g.home_score == null)
    .sort((x, y) => Math.abs(x.prediction.home_win_prob - 0.5) - Math.abs(y.prediction.home_win_prob - 0.5))[0];
  const likelyShootout = predictions.games
    .filter((g) => g.home_score == null)
    .sort((x, y) => y.prediction.predicted_total - x.prediction.predicted_total)[0];
  const highestConfidence = predictions.games
    .filter((g) => g.home_score == null && g.prediction.confidence_tier === "high")
    .sort((x, y) => Math.abs(y.prediction.home_win_prob - 0.5) - Math.abs(x.prediction.home_win_prob - 0.5))[0];

  return (
    <div className="space-y-10">
      <WelcomeHero hasLiveGames={hasLiveGames} weekLabel={weekLabel} />
      <FreshnessBadges freshness={freshness} />

      <QuickExploreBar />

      <section>
        <SectionHeader
          title={week1Season ? `Week 1 · ${week1Season} season` : "Week 1 schedule"}
          href={hasWeek1Games ? "/odds" : undefined}
          linkLabel={hasWeek1Games ? "Betting edges →" : undefined}
        />
        <Week1Schedule season={week1Season} games={week1Predictions.games} />
      </section>

      {featured && <FeaturedGame game={featured} weekLabel={weekLabel} />}

      <ExperimentedInsightCards
        tossup={tossup}
        likelyShootout={likelyShootout}
        highestConfidence={highestConfidence}
      />

      {hasLiveGames && liveGames.length > 0 && (
        <section>
          <SectionHeader title="Live now" />
          <LiveScoreboardStrip games={liveGames} />
        </section>
      )}

      <Suspense fallback={<DeferredHomeSkeleton />}>
        <HomeDeferredSections
          eloRatings={eloRatings.ratings}
          otherGames={otherGames}
          weekLabel={weekLabel}
          week={predictions.week}
        />
      </Suspense>
    </div>
  );
}

function DeferredHomeSkeleton() {
  return (
    <div className="space-y-4">
      <Card>
        <p className="text-sm text-muted">Loading deeper insights…</p>
      </Card>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card><p className="text-sm text-muted">Loading analysis cards…</p></Card>
        <Card><p className="text-sm text-muted">Loading models and market context…</p></Card>
      </div>
    </div>
  );
}

async function HomeDeferredSections({
  eloRatings,
  otherGames,
  weekLabel,
  week,
}: {
  eloRatings: Array<{ team_id: string; rating: number; grade: string }>;
  otherGames: GamePrediction[];
  weekLabel: string | null;
  week: number | null;
}) {
  const [news, standings, trendingAdds] = await Promise.all([
    safe(api.news(8, undefined, { revalidate: 60 }), []),
    safe(api.projectedStandings(undefined, { revalidate: 900 }), { season: 0, divisions: [] }),
    safe(api.fantasyTrending("add", 6, { revalidate: 300 }), { kind: "add", items: [] }),
  ]);

  return (
    <>
      {(eloRatings.length > 0 || standings.divisions.length > 0) && (
        <section>
          <SectionHeader title="League pulse" />
          <LeaguePulse elo={eloRatings} standings={standings.divisions} />
        </section>
      )}

      {otherGames.length > 0 && (
        <section>
          <SectionHeader
            title={week ? `Week ${week} slate` : "Upcoming games"}
            href="/odds"
            linkLabel="Full board →"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {otherGames.map((g) => (
              <PredictionCard key={g.id || `${g.home_team_id}-${g.away_team_id}`} game={g} />
            ))}
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Today's storylines" className="lg:col-span-2">
          {news.length === 0 ? (
            <p className="text-sm text-muted">News populates on the next refresh (~5 min).</p>
          ) : (
            <ul className="space-y-3 text-sm">
              {news.map((n, i) => (
                <li key={n.id}>
                  {i === 0 ? (
                    <a
                      href={n.link}
                      target="_blank"
                      rel="noreferrer"
                      className="block rounded-xl border divider bg-bg/40 p-4 hover:border-team-primary/50 transition-colors group"
                    >
                      <div className="text-[10px] uppercase tracking-wider text-team-primary font-semibold mb-1.5">
                        Lead story
                      </div>
                      <div className="text-lg font-semibold leading-snug group-hover:text-team-primary transition-colors">
                        {n.title}
                      </div>
                      <div className="text-[11px] text-muted mt-2">
                        {n.source_label}
                        {n.published_at && ` · ${timeAgo(n.published_at)}`}
                      </div>
                    </a>
                  ) : (
                    <a href={n.link} target="_blank" rel="noreferrer" className="hover:underline block py-0.5">
                      <div>{n.title}</div>
                      <div className="text-[11px] text-muted mt-0.5">
                        {n.source_label}
                        {n.published_at && ` · ${timeAgo(n.published_at)}`}
                      </div>
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Power rankings" action={<span className="text-[11px] text-muted">via Elo</span>}>
          {eloRatings.length === 0 ? (
            <p className="text-sm text-muted">Ratings build on first boot.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {eloRatings.slice(0, 12).map((r, i) => (
                <li key={r.team_id} className="flex items-center justify-between gap-2 group">
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className={`tabular-nums w-5 text-right text-xs font-bold ${
                        i < 3 ? "text-team-primary" : "text-muted"
                      }`}
                    >
                      {i + 1}
                    </span>
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

      <PersonaGate allowed={["analyst"]}>
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
      </PersonaGate>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <PersonaGate allowed={["fantasy", "analyst"]}>
          <AwardRaceCard />
        </PersonaGate>
        <PersonaGate allowed={["bettor", "analyst"]}>
          <LeagueBestBetsCard />
        </PersonaGate>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <PersonaGate allowed={["fantasy", "analyst"]}>
          <Card title="Fantasy: trending adds">
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
        </PersonaGate>

        <Card title="New here? Start here">
          <ul className="space-y-2.5 text-sm">
            {[
              { href: "/teams", title: "Browse all 32 teams", desc: "Rosters, stats, schedules, and recent form" },
              { href: "/odds", title: "See the odds board", desc: "Live lines from major books, in plain English" },
              { href: "/bets", title: "Track your bets", desc: "Log wagers and see if you beat the closing line" },
              { href: "/h2h/PHI/SF", title: "Compare two teams", desc: "Head-to-head matchup breakdowns" },
            ].map((l) => (
              <li key={l.href}>
                <Link href={l.href} className="group flex items-start gap-2">
                  <span className="text-team-primary mt-0.5">→</span>
                  <span>
                    <span className="font-medium group-hover:text-team-primary transition-colors">
                      {l.title}
                    </span>
                    <span className="block text-[11px] text-muted">{l.desc}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>

        <Card title="What the numbers mean">
          <dl className="space-y-2.5 text-sm">
            {[
              { term: "Win probability", def: "Each team's modeled chance to win — e.g. 65% vs 35%." },
              { term: "Elo rating", def: "A power-ranking score; higher is stronger, ~1500 is average." },
              { term: "Spread", def: "The favorite's expected margin. “PHI -3.5” = win by 4+ to cover." },
              { term: "Total (O/U)", def: "Combined points the market expects both teams to score." },
            ].map((t) => (
              <div key={t.term}>
                <dt className="font-medium">{t.term}</dt>
                <dd className="text-[11px] text-muted">{t.def}</dd>
              </div>
            ))}
          </dl>
          <Link href="/odds" className="inline-block mt-3 text-xs text-team-primary hover:underline">
            See this week&rsquo;s odds &rarr;
          </Link>
        </Card>
      </div>

      <div className="text-[10px] text-muted">
        Deeper cards load after the first insight so this page stays fast on slower networks.
      </div>
    </>
  );
}

function SectionHeader({
  title,
  href,
  linkLabel,
}: {
  title: string;
  href?: string;
  linkLabel?: string;
}) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="home-section-title">{title}</h2>
      {href && linkLabel && (
        <Link href={href} className="text-xs text-muted hover:text-text">
          {linkLabel}
        </Link>
      )}
    </div>
  );
}

function QuickExploreBar() {
  return (
    <nav className="flex flex-wrap items-center gap-2" aria-label="Quick explore">
      <span className="text-[11px] text-muted mr-1 hidden sm:inline">Jump to</span>
      {QUICK_LINKS.map(({ href, label, Icon }) => (
        <Link key={href} href={href} className="home-quick-link">
          <Icon />
          {label}
        </Link>
      ))}
    </nav>
  );
}

function LiveScoreboardStrip({ games }: { games: Game[] }) {
  return (
    <div className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
      {games.map((g) => {
        const away = g.away_team_id;
        const home = g.home_team_id;
        if (!away || !home) return null;
        return (
          <Link
            key={g.id}
            href={`/h2h/${away}/${home}`}
            className="panel panel-hover-lift shrink-0 px-4 py-3 min-w-[200px] flex items-center gap-3"
          >
            <TeamLogo teamId={away} size={28} />
            <div className="text-center min-w-[4rem]">
              <div className="text-lg font-bold tabular-nums leading-none">
                {g.away_score ?? "—"} – {g.home_score ?? "—"}
              </div>
              <span className="live-pill mt-1.5 mx-auto">Live</span>
            </div>
            <TeamLogo teamId={home} size={28} />
          </Link>
        );
      })}
    </div>
  );
}

function FeaturedGame({ game, weekLabel }: { game: GamePrediction; weekLabel: string | null }) {
  const p = game.prediction;
  const fav = p.predicted_spread <= 0 ? game.home_team_id : game.away_team_id;
  const absSpread = Math.abs(p.predicted_spread);

  return (
    <section className="panel hero-glow p-6 md:p-8 border-t-2 border-t-team-primary/40">
      <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px] uppercase tracking-wider font-semibold text-team-primary">
            Featured matchup
          </span>
          <span className="text-[11px] text-muted">
            {weekLabel ?? `Wk ${game.week ?? "—"}`}
            {game.gameday ? ` · ${game.gameday}` : ""}
          </span>
        </div>
        <Link
          href={`/h2h/${game.away_team_id}/${game.home_team_id}`}
          className="text-xs font-medium text-team-primary hover:underline"
        >
          Full breakdown →
        </Link>
      </div>

      <div className="flex flex-col md:flex-row items-center justify-between gap-6">
        <TeamLockup teamId={game.away_team_id} elo={game.away_elo} />
        <div className="flex flex-col items-center gap-2">
          <span className="text-[10px] uppercase tracking-widest text-muted font-semibold">vs</span>
          <div className="text-3xl md:text-4xl font-bold tabular-nums">
            <span className="text-team-primary">{Math.round(p.away_win_prob * 100)}%</span>
            <span className="text-muted mx-2 font-normal text-2xl">/</span>
            <span className="text-team-secondary">{Math.round(p.home_win_prob * 100)}%</span>
          </div>
          <div className="text-[11px] text-muted">win probability</div>
        </div>
        <TeamLockup teamId={game.home_team_id} elo={game.home_elo} rightAligned />
      </div>

      <div className="mt-6">
        <WinProbBar
          awayTeam={game.away_team_id}
          awayProb={p.away_win_prob}
          homeTeam={game.home_team_id}
          homeProb={p.home_win_prob}
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5 text-sm">
        <Stat label="Spread" value={`${fav} -${absSpread.toFixed(1)}`} />
        <Stat label="Total" value={p.predicted_total.toFixed(1)} />
        <Stat label="Predicted score" value={`${p.predicted_away_score.toFixed(0)}-${p.predicted_home_score.toFixed(0)}`} />
        <Stat label="Game script" value={p.game_script ?? "—"} />
      </div>
    </section>
  );
}

function TeamLockup({ teamId, elo, rightAligned }: { teamId: string; elo: number; rightAligned?: boolean }) {
  return (
    <Link
      href={`/teams/${teamId}`}
      className={`flex items-center gap-4 group flex-1 ${rightAligned ? "flex-row-reverse text-right md:justify-end" : "md:justify-start"}`}
    >
      <TeamLogo teamId={teamId} size={72} />
      <div>
        <div className="text-3xl md:text-4xl font-extrabold tracking-tight group-hover:text-team-primary transition-colors">
          {teamId}
        </div>
        <div className="text-xs text-muted tabular-nums">Elo {Math.round(elo)}</div>
      </div>
    </Link>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg/60 rounded-lg px-3 py-2.5 border divider">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className="font-bold tabular-nums">{value}</div>
    </div>
  );
}

/* ---------- Quick-link icons (match the nav tab bar) ---------- */
const ICON_PROPS = {
  width: 15,
  height: 15,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

function TeamsIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M12 3l7 3v5c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6l7-3Z" />
      <path d="M9 11l2 2 4-4" />
    </svg>
  );
}
function OddsIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M8 16v-4" />
      <path d="M13 16V8" />
      <path d="M18 16v-6" />
    </svg>
  );
}
function H2HIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M3 8h13l-3-3" />
      <path d="M21 16H8l3 3" />
    </svg>
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
