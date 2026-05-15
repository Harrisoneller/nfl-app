import Link from "next/link";
import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { PredictionCard } from "@/components/predictions/PredictionCard";
import { EloBadge } from "@/components/predictions/EloBadge";

async function safe<T>(p: Promise<T>, fallback: T): Promise<T> {
  try { return await p; } catch { return fallback; }
}

export default async function HomePage() {
  // Fan-out parallel fetches. The page renders whatever responds; sections
  // with no data gracefully degrade.
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

  const hasLiveGames = scoreboard.some((g) => g.status === "in" || g.status === "live");

  return (
    <div className="space-y-6">
      {/* Hero: scoreboard with predictions */}
      <section>
        <div className="flex items-end justify-between mb-2">
          <h1 className="text-xl font-semibold">
            {hasLiveGames ? "Live now" : predictions.week ? `Week ${predictions.week}` : "This week"}
          </h1>
          <Link href="/odds" className="text-xs text-muted hover:underline">Full board →</Link>
        </div>
        {predictions.games.length === 0 ? (
          <Card>
            <p className="text-sm text-muted">
              Predictions populate once the Elo system finishes its first build
              (~60s after backend start), then refresh every 30 minutes.
            </p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {predictions.games.slice(0, 9).map((g) => (
              <PredictionCard key={g.id || `${g.home_team_id}-${g.away_team_id}`} game={g} />
            ))}
          </div>
        )}
      </section>

      {/* Top storylines + power rankings side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Today's storylines" className="lg:col-span-2">
          {news.length === 0 ? (
            <p className="text-sm text-muted">News populates on the next refresh (~5 min).</p>
          ) : (
            <ul className="space-y-2.5 text-sm">
              {news.map((n) => (
                <li key={n.id} className="flex items-start gap-3">
                  <span className="text-muted text-xs whitespace-nowrap mt-0.5 w-28">
                    [{n.source_label}]
                  </span>
                  <a href={n.link} target="_blank" rel="noreferrer" className="hover:underline flex-1">
                    {n.title}
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
            <ol className="space-y-1 text-sm">
              {eloRatings.ratings.slice(0, 12).map((r, i) => (
                <li key={r.team_id} className="flex items-center justify-between">
                  <span>
                    <span className="text-muted tabular-nums w-5 inline-block">{i + 1}.</span>{" "}
                    <Link href={`/teams/${r.team_id}`} className="hover:underline font-medium">
                      {r.team_id}
                    </Link>
                  </span>
                  <EloBadge rating={r.rating} grade={r.grade} size="sm" showRating />
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      {/* Projected standings */}
      {standings.divisions.length > 0 && (
        <Card title="Projected standings" action={<span className="text-[11px] text-muted">10,000 sims</span>}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {standings.divisions.map((d) => (
              <div key={`${d.conference}-${d.division}`}>
                <h3 className="text-xs text-muted mb-1.5">{d.conference} {d.division}</h3>
                <ul className="space-y-1 text-sm">
                  {d.teams.map((t, i) => (
                    <li key={t.team_id} className="flex items-center justify-between">
                      <span>
                        <span className="text-muted text-xs w-4 inline-block">{i + 1}.</span>
                        <Link href={`/teams/${t.team_id}`} className="hover:underline font-medium">
                          {t.team_id}
                        </Link>
                      </span>
                      <span className="text-xs tabular-nums">
                        {t.mean_wins.toFixed(1)}W{" "}
                        <span className="text-muted">· {t.playoff_pct.toFixed(0)}% PO</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Persona snippets row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card title="Fantasy: trending adds" action={<Link href="/fantasy" className="text-[11px] hover:underline">More →</Link>}>
          {trendingAdds.items.length === 0 ? (
            <p className="text-sm text-muted">Sleeper trending refreshes every 5 min.</p>
          ) : (
            <ol className="space-y-1 text-sm">
              {trendingAdds.items.slice(0, 6).map((r: any) => (
                <li key={r.player_id} className="flex justify-between">
                  <span>
                    {r.player_id ? (
                      <Link href={`/players/${r.player_id}`} className="hover:underline">{r.name ?? r.player_id}</Link>
                    ) : (
                      <span>{r.name ?? r.player_id}</span>
                    )}
                    <span className="text-muted text-xs ml-2">{r.position} · {r.team ?? "—"}</span>
                  </span>
                  <span className="text-muted text-xs tabular-nums">+{r.count?.toLocaleString?.()}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <Card title="Ask the AI" action={<Link href="/ai" className="text-[11px] hover:underline">Open →</Link>}>
          <p className="text-sm text-muted mb-2">Get specific answers backed by live data.</p>
          <ul className="text-sm space-y-1">
            <li>"Compare PHI and SF rushing efficiency"</li>
            <li>"Best red-zone defenses this season"</li>
            <li>"Build a widget of QB EPA leaders"</li>
          </ul>
        </Card>

        <Card title="Your widgets" action={<Link href="/ai" className="text-[11px] hover:underline">Build →</Link>}>
          {widgets.length === 0 ? (
            <p className="text-sm text-muted">
              Use the AI page to create a widget; saved widgets appear here.
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

      {/* Live scoreboard fallback (when no predictions yet) */}
      {scoreboard.length > 0 && predictions.games.length === 0 && (
        <Card title="Live scoreboard">
          <ul className="divide-y divider text-sm">
            {scoreboard.map((g) => (
              <li key={g.id} className="py-2 flex items-center justify-between gap-3">
                <span>{g.away_team_id ?? "TBD"} @ {g.home_team_id ?? "TBD"}</span>
                <span className="text-xs text-muted">{g.status_detail || g.status}</span>
                <span className="tabular-nums">{g.away_score ?? "—"} : {g.home_score ?? "—"}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
