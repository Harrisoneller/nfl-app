import Link from "next/link";
import { StadiumHeroBackdrop } from "./StadiumHeroBackdrop";

export function WelcomeHero({
  hasLiveGames,
  weekLabel,
}: {
  hasLiveGames: boolean;
  weekLabel: string | null;
}) {
  return (
    <section className="welcome-hero">
      <div className="welcome-hero__content">
        {weekLabel ? (
          <p className="welcome-hero__eyebrow">{weekLabel} · NFL One-Stop</p>
        ) : (
          <p className="welcome-hero__eyebrow">NFL One-Stop</p>
        )}

        <h1 className="welcome-hero__title">
          Every angle of the NFL.
          <span className="welcome-hero__title-accent"> In one place.</span>
        </h1>

        <p className="welcome-hero__subtitle">
          Live scores, sharp betting edges, fantasy intel, Elo-driven predictions, and an
          AI assistant — all backed by transparent, evaluated models.
        </p>

        <div className="welcome-hero__tags" aria-label="Capabilities">
          <span>Elo ratings</span>
          <span>EPA &amp; success rate</span>
          <span>Monte Carlo sims</span>
          <span>Live odds</span>
        </div>

        <div className="welcome-hero__actions">
          <Link href="/teams" className="welcome-hero__btn welcome-hero__btn--primary">
            Explore teams
          </Link>
          <Link href="/ai" className="welcome-hero__btn welcome-hero__btn--ghost">
            Ask the AI
          </Link>
        </div>

        {hasLiveGames && (
          <p className="welcome-hero__footnote">
            <span className="live-pill">Live</span>
            Games in progress — scroll down for scores
          </p>
        )}
        {!hasLiveGames && !weekLabel && (
          <p className="welcome-hero__footnote welcome-hero__footnote--muted">
            Offseason mode — predictions refresh when Week 1 schedules are live.
          </p>
        )}
      </div>

      <StadiumHeroBackdrop />
    </section>
  );
}
