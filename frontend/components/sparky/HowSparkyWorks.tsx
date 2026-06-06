"use client";
import { useEffect, useState } from "react";

/**
 * HowSparkyWorks — a short, plain-English orientation panel for first-time users.
 *
 * Dismissible (persisted in localStorage) so power users don't see it forever,
 * but recoverable via the "Show again" link inside the Glossary panel.
 *
 * The four steps mirror Sparky's pipeline:
 *   1. Pull odds  →  2. Detect signals  →  3. Score & rank  →  4. Track accuracy
 * which is exactly the SOW data flow translated out of jargon.
 */
const DISMISS_KEY = "sparky_intro_dismissed_v1";

export function HowSparkyWorks() {
  const [hidden, setHidden] = useState<boolean | null>(null); // null = pre-hydration

  useEffect(() => {
    try {
      setHidden(localStorage.getItem(DISMISS_KEY) === "1");
    } catch {
      setHidden(false);
    }
  }, []);

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore quota / privacy mode */
    }
    setHidden(true);
  };

  // Avoid layout flash before hydration tells us whether to show.
  if (hidden !== false) return null;

  return (
    <div className="sparky-intro">
      <div className="sparky-intro__head">
        <div>
          <div className="sparky-tagline text-emerald-300">New here?</div>
          <h2 className="sparky-intro__title">How Sparky picks games</h2>
        </div>
        <button
          onClick={dismiss}
          className="sparky-intro__dismiss"
          aria-label="Hide the introduction"
        >
          Got it ✕
        </button>
      </div>

      <p className="sparky-intro__lead">
        Sparky is an analytics tool, not a tipster. It reads the live sportsbook
        market, compares it to its own NFL model, and tells you where the two
        agree, where they disagree, and which parlays the combination actually
        favors.
      </p>

      <ol className="sparky-intro__steps">
        <Step
          n={1}
          title="Pull the lines"
          body="Twice a day Sparky captures every sportsbook's price for every upcoming game. Those snapshots are labeled T1 (early), T2 (mid-week), and T3 (close to kickoff)."
        />
        <Step
          n={2}
          title="Detect market signals"
          body="Sparky watches how the lines move and where books disagree. That produces named signals — Steam Move, Anchor Favorite, Trap Risk, Upset Watch — each with a one-sentence explanation."
        />
        <Step
          n={3}
          title="Score the pick"
          body="Sparky's NFL model + the no-vig market price are blended into a win probability and a 0–100 confidence score. Signals nudge the score up or down."
        />
        <Step
          n={4}
          title="Rank the parlays"
          body="Pick 2–8 games and Sparky ranks every winner combination by confidence, signal support, a sensible favorite/dog mix, and value (+EV) — not just longest payout."
        />
      </ol>

      <p className="sparky-intro__foot">
        Hover any{" "}
        <span
          className="sparky-help__btn"
          aria-hidden
          style={{ cursor: "default", verticalAlign: "middle" }}
        >
          ?
        </span>{" "}
        icon to translate a term into plain English. The full glossary lives at the
        bottom of this page.
      </p>
    </div>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="sparky-intro__step">
      <span className="sparky-intro__num">{n}</span>
      <div>
        <div className="sparky-intro__step-title">{title}</div>
        <div className="sparky-intro__step-body">{body}</div>
      </div>
    </li>
  );
}
