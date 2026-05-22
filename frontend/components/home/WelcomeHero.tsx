import Image from "next/image";
import Link from "next/link";

const HUD_STATS = [
  { label: "Win prob", value: "67%" },
  { label: "EPA/play", value: "+0.14" },
  { label: "Elo", value: "1684" },
  { label: "Spread", value: "-3.5" },
] as const;

export function WelcomeHero({
  hasLiveGames,
  weekLabel,
}: {
  hasLiveGames: boolean;
  weekLabel: string | null;
}) {
  return (
    <section className="welcome-hero">
      <Image
        src="/hero-stadium.jpg"
        alt=""
        fill
        priority
        sizes="(max-width: 1280px) 100vw, 1280px"
        className="welcome-hero__photo"
      />

      <div className="welcome-hero__shade" aria-hidden />
      <div className="welcome-hero__grid" aria-hidden />

      <div className="welcome-hero__hud" aria-hidden>
        {HUD_STATS.map((s) => (
          <div key={s.label} className="welcome-hero__hud-stat">
            <span className="welcome-hero__hud-label">{s.label}</span>
            <span className="welcome-hero__hud-value">{s.value}</span>
          </div>
        ))}
      </div>

      <div className="welcome-hero__content">
        {weekLabel ? (
          <p className="welcome-hero__eyebrow">{weekLabel} · Statletics NFL</p>
        ) : (
          <p className="welcome-hero__eyebrow">Statletics NFL</p>
        )}

        <h1 className="welcome-hero__title">
          Every angle of the NFL.
          <span className="welcome-hero__title-accent"> In one place.</span>
        </h1>

        <p className="welcome-hero__subtitle">
          Live scores, betting edges, league intel, ML predictions, and
          advanced metrics
        </p>

        <div className="welcome-hero__tags" aria-label="Capabilities">
          <span>Elo ratings</span>
          <span>EPA &amp; success rate</span>
          <span>ML predictions</span>
          <span>Live odds</span>
        </div>

        <div className="welcome-hero__actions">
          <Link href="/teams" className="welcome-hero__btn welcome-hero__btn--primary">
            Explore teams
          </Link>
        </div>

        {hasLiveGames && (
          <p className="welcome-hero__footnote">
            <span className="live-pill">Live</span>
            Games in progress — scroll down for scores
          </p>
        )}
        {!hasLiveGames && !weekLabel && (
          <p className="welcome-hero__footnote">
            Offseason mode — predictions refresh when Week 1 schedules are live.
          </p>
        )}
      </div>
    </section>
  );
}
