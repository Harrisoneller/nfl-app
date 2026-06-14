"use client";
import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef } from "react";

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

export function WelcomeHero({
  hasLiveGames,
  weekLabel,
}: {
  hasLiveGames: boolean;
  weekLabel: string | null;
}) {
  const mediaRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const cueRef = useRef<HTMLDivElement>(null);

  // Scroll-driven parallax: photo drifts slower than the page, content fades
  // and lifts as you scroll past, the cue fades out. rAF-throttled, passive.
  useEffect(() => {
    if (prefersReducedMotion()) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const y = window.scrollY;
        if (mediaRef.current) {
          mediaRef.current.style.transform = `translate3d(0, ${y * 0.35}px, 0)`;
        }
        if (contentRef.current) {
          contentRef.current.style.opacity = String(Math.max(0, 1 - y / 520));
          contentRef.current.style.transform = `translate3d(0, ${y * 0.18}px, 0)`;
        }
        if (cueRef.current) {
          cueRef.current.style.opacity = String(Math.max(0, 1 - y / 220));
        }
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <section className="welcome-hero">
      <div className="welcome-hero__media" ref={mediaRef}>
        <Image
          src="/TC5_5287-scaled.jpg"
          alt=""
          fill
          priority
          sizes="100vw"
          className="welcome-hero__photo"
        />
      </div>

      <div className="welcome-hero__shade" aria-hidden />
      <div className="welcome-hero__grid" aria-hidden />

      <div className="welcome-hero__content" ref={contentRef}>
        <div className="welcome-hero__logo">
          <Image
            src="/brand/statletics-neon.png"
            alt="Statletics Sports"
            width={1014}
            height={403}
            priority
            className="welcome-hero__logo-img"
          />
        </div>

        {weekLabel && <p className="welcome-hero__eyebrow">{weekLabel}</p>}

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

      <div className="welcome-hero__cue" ref={cueRef} aria-hidden>
        <span className="welcome-hero__cue-mouse" />
        <span>Scroll</span>
      </div>
    </section>
  );
}
