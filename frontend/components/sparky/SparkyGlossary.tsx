"use client";
import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { TERMS, PICK_TIER_DESCRIPTIONS } from "./HelpTip";

/**
 * SparkyGlossary — a collapsible, plain-English reference for every term the UI uses.
 *
 * Pulls the canonical market-signal definitions from the backend
 * (`/sparky/signals/glossary`) — single source of truth — and merges them with
 * the UI-side TERMS map (metrics, classifications) so a user only ever needs
 * to look in one place to translate any acronym Sparky throws at them.
 *
 * Why this matters for a layman dashboard
 * ---------------------------------------
 * The SOW asks for "plain-English explanation templates for the user interface".
 * Per-card text gives them one sentence at a time; this gives them the *map*.
 * It also unblocks the "Got it ✕" intro — a user can dismiss the intro
 * confidently knowing the same definitions live here permanently.
 */
export function SparkyGlossary() {
  const [open, setOpen] = useState(false);
  const { data } = useSWR(
    open ? ["sparky-glossary"] : null,
    () => api.sparkyGlossary(),
  );

  return (
    <div className="sparky-glossary">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="sparky-glossary__toggle"
        aria-expanded={open}
      >
        <span className="sparky-glossary__chev" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        Glossary — what every Sparky term means
        <span className="sparky-glossary__hint">
          {open ? "click to collapse" : "click to expand"}
        </span>
      </button>

      {open && (
        <div className="sparky-glossary__body">
          <Section title="Pick tiers">
            {Object.entries(PICK_TIER_DESCRIPTIONS).map(([key, body]) => (
              <Entry key={key} term={titlecase(key)} body={body} />
            ))}
          </Section>

          <Section title="Prediction & scoring">
            <Entry term={TERMS.confidence.label} body={TERMS.confidence.body} />
            <Entry term={TERMS.win_prob.label} body={TERMS.win_prob.body} />
            <Entry term={TERMS.model_prob.label} body={TERMS.model_prob.body} />
            <Entry term={TERMS.market_prob.label} body={TERMS.market_prob.body} />
          </Section>

          <Section title="Parlay metrics">
            <Entry term={TERMS.composite.label} body={TERMS.composite.body} />
            <Entry term={TERMS.combined_win_prob.label} body={TERMS.combined_win_prob.body} />
            <Entry term={TERMS.implied_prob.label} body={TERMS.implied_prob.body} />
            <Entry term={TERMS.edge.label} body={TERMS.edge.body} />
            <Entry term={TERMS.expected_value.label} body={TERMS.expected_value.body} />
            <Entry term={TERMS.is_value.label} body={TERMS.is_value.body} />
            <Entry term={TERMS.kelly.label} body={TERMS.kelly.body} />
            <Entry term={TERMS.signal_alignment.label} body={TERMS.signal_alignment.body} />
            <Entry term={TERMS.underdog_count.label} body={TERMS.underdog_count.body} />
          </Section>

          <Section title="Historical accuracy">
            <Entry term={TERMS.rank1_hit.label} body={TERMS.rank1_hit.body} />
            <Entry term={TERMS.top3.label} body={TERMS.top3.body} />
            <Entry term={TERMS.top4.label} body={TERMS.top4.body} />
            <Entry term={TERMS.calibration.label} body={TERMS.calibration.body} />
            <Entry term={TERMS.rolling.label} body={TERMS.rolling.body} />
          </Section>

          <Section title="Market signals">
            {data?.signals?.length ? (
              data.signals.map((s) => (
                <Entry key={s.key} term={s.label} body={s.definition} />
              ))
            ) : (
              <p className="text-xs text-muted">Loading signal definitions…</p>
            )}
          </Section>

          <p className="sparky-glossary__foot">
            Sparky surfaces probabilities and signals — not betting advice.
            Always size bets within your bankroll and check your local laws.
          </p>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="sparky-glossary__section">
      <h4 className="sparky-glossary__section-title">{title}</h4>
      <dl>{children}</dl>
    </div>
  );
}

function Entry({ term, body }: { term: string; body: string }) {
  return (
    <div className="sparky-glossary__entry">
      <dt>{term}</dt>
      <dd>{body}</dd>
    </div>
  );
}

function titlecase(s: string): string {
  return s
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
