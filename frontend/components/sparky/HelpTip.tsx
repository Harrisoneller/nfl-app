"use client";
import { useEffect, useRef, useState } from "react";

/**
 * HelpTip — an accessible "?" icon that explains a term in plain English.
 *
 * Why this exists
 * ---------------
 * Sparky uses real betting jargon (EV, Kelly, Edge, Composite, Top-3 containment,
 * Calibration). Power users want the precision; new users need the translation.
 * A HelpTip lets us keep the precise label visible *and* attach a one-sentence
 * plain-English explanation that's reachable on desktop (hover) and mobile (tap).
 *
 * Behavior
 * --------
 * - Click toggles the bubble (mobile-friendly).
 * - Hover/focus shows it too (desktop niceness).
 * - Click anywhere outside, or Escape, closes it.
 * - `title` is also set as a fallback so screen-reader users get the same text.
 */
export function HelpTip({
  label,
  body,
  className = "",
  inline = true,
}: {
  /** Short title shown bold at the top of the bubble (e.g. "Expected Value"). */
  label: string;
  /** One- or two-sentence plain-English explanation. */
  body: string;
  className?: string;
  /** When false, renders as a block-level affordance instead of inline-flex. */
  inline?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);

  // Click-outside + Escape to close — same pattern as a tiny popover.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span
      ref={ref}
      className={`sparky-help ${inline ? "" : "sparky-help--block"} ${open ? "is-open" : ""} ${className}`}
    >
      <button
        type="button"
        aria-label={`Explain: ${label}`}
        aria-expanded={open}
        title={`${label} — ${body}`}
        className="sparky-help__btn"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onMouseEnter={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onMouseLeave={() => setOpen(false)}
      >
        ?
      </button>
      <span role="tooltip" className="sparky-help__bubble">
        <span className="sparky-help__title">{label}</span>
        <span className="sparky-help__body">{body}</span>
      </span>
    </span>
  );
}

/**
 * One source of truth for plain-English definitions of every Sparky term that
 * appears on the dashboard, parlay results, and accuracy panel. Keeping these
 * here (instead of inlined per-component) means a single edit improves the
 * experience everywhere, and the same wording shows up in the Glossary panel.
 */
export const TERMS = {
  // Predictions
  confidence: {
    label: "Confidence Score",
    body: "0–100 score combining Sparky's model probability, the market price, and any active signals. 45–55 is near a coin flip; 80+ is a high-conviction call.",
  },
  win_prob: {
    label: "Win Probability",
    body: "Sparky's blended estimate of how often the picked team wins this exact game. 60% means 'wins 6 times out of 10'.",
  },
  model_prob: {
    label: "Model Probability",
    body: "What Sparky's own NFL model (Elo + team form + situational context) gives the picked team — before considering the market.",
  },
  market_prob: {
    label: "Market Probability",
    body: "What the sportsbooks' price implies after stripping out the house's cut (a.k.a. 'no-vig' probability). Sharper than a single book's price.",
  },
  classification: {
    label: "Pick Tier",
    body: "How Sparky rates the strength of the call. Anchor = strongest, then Strong Lean, Lean, Coin Flip (avoid), and Upset Watch (live longshot).",
  },

  // Parlay metrics
  combined_win_prob: {
    label: "Sparky Hit Rate",
    body: "Sparky's odds that ALL legs win, multiplying each leg's win probability. 'Model hit %' on parlay rows.",
  },
  implied_prob: {
    label: "Price-Implied Hit Rate",
    body: "How often the sportsbook's parlay price says it should cash (vig included). Compare against Sparky's Hit Rate to spot value.",
  },
  edge: {
    label: "Edge",
    body: "Sparky's win probability minus the market's. Positive = Sparky thinks you'll win more often than the price suggests.",
  },
  expected_value: {
    label: "Expected Value (EV)",
    body: "Average profit (or loss) per $1 bet over the long run. +5% EV means $0.05 of expected profit per dollar staked; negative is a long-run loss.",
  },
  is_value: {
    label: "+EV (Value Pick)",
    body: "Marked when expected value is positive — i.e. the price is paying you more than Sparky thinks the real odds justify.",
  },
  kelly: {
    label: "Kelly Stake",
    body: "Suggested bet size as a fraction of bankroll using the Kelly criterion, capped at 25% for safety. 0% means Sparky says don't bet it.",
  },
  composite: {
    label: "Composite Score",
    body: "0–100 ranking score that combines confidence, signal support, a sensible favorite/dog mix, and value (+EV). Higher = better all-around parlay, not just longer odds.",
  },
  signal_alignment: {
    label: "Signal Alignment",
    body: "0–1 measure of how many market signals point the same way as Sparky's pick. 1.0 = all signals confirm; 0.5 = mixed; below 0.5 = signals lean against.",
  },
  underdog_count: {
    label: "Underdog Count",
    body: "How many legs of the parlay are on the underdog (the team the book thinks is less likely to win). Mixing dogs and favorites is usually healthier than all-chalk or all-dog.",
  },

  // Accuracy / dashboard
  rank1_hit: {
    label: "Rank #1 Hit Rate",
    body: "How often Sparky's TOP-ranked parlay combo turned out to be the correct one. The headline measure of parlay-ranking quality.",
  },
  top3: {
    label: "Top-3 Containment",
    body: "How often the actual winning combo was somewhere in Sparky's top 3 ranked options. A softer measure that rewards being 'close to right'.",
  },
  top4: {
    label: "Top-4 Containment",
    body: "Same idea as Top-3, but more forgiving — the winner just has to be in Sparky's top 4 ranked options.",
  },
  calibration: {
    label: "Calibration",
    body: "Does an 80% confidence pick actually win ~80% of the time? Bars in this chart should rise from left to right if Sparky's confidence numbers are honest.",
  },
  rolling: {
    label: "Rolling Windows",
    body: "Accuracy measured over the last N days. The 7-day window reacts fast to a hot/cold streak; the 30-day window smooths noise and shows the underlying trend.",
  },
} as const;

export const PICK_TIER_DESCRIPTIONS: Record<string, string> = {
  anchor:
    "Anchor — Sparky's strongest call. Heavy favorite with multi-book agreement; the kind of leg you'd build a parlay around.",
  strong_lean:
    "Strong Lean — Above-average confidence; market and model agree but it isn't a slam dunk.",
  lean:
    "Lean — Modest edge. Worth a look but not a featured play.",
  coin_flip:
    "Coin Flip — Near pick'em. Sparky has no real edge either way; usually best to skip.",
  upset_watch:
    "Upset Watch — Sparky's model rates the underdog meaningfully better than the market does. A longshot with live value.",
};
