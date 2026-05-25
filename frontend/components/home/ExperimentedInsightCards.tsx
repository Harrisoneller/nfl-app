"use client";

import Link from "next/link";
import { useEffect, useMemo } from "react";

import { Card } from "@/components/Card";
import { PersonaGate } from "@/components/persona/PersonaGate";
import { INSIGHT_ORDER_EXPERIMENT, useExperiments } from "@/context/ExperimentProvider";
import { GamePrediction } from "@/lib/api";

type Props = {
  tossup?: GamePrediction;
  likelyShootout?: GamePrediction;
  highestConfidence?: GamePrediction;
};

type InsightCardDef = {
  key: "market_setup" | "fantasy_env" | "model_confidence";
  persona: "bettor" | "fantasy" | "analyst";
  title: string;
  body: React.ReactNode;
};

export function ExperimentedInsightCards({ tossup, likelyShootout, highestConfidence }: Props) {
  const { insightOrderVariant, trackEvent, markReturnIfEligible } = useExperiments();

  useEffect(() => {
    const cards = orderCardKeys(insightOrderVariant);
    cards.forEach((cardKey) => {
      trackEvent({
        experiment_key: INSIGHT_ORDER_EXPERIMENT,
        variant: insightOrderVariant,
        event_type: "impression",
        page: "home",
        card_key: cardKey,
      });
    });
    markReturnIfEligible(insightOrderVariant);
  }, [insightOrderVariant, markReturnIfEligible, trackEvent]);

  const cards = useMemo<InsightCardDef[]>(
    () => [
      {
        key: "market_setup",
        persona: "bettor",
        title: "First insight: market setup",
        body: tossup ? (
          <>
            <p className="text-sm">
              {tossup.away_team_id} @ {tossup.home_team_id} profiles as a true toss-up.
            </p>
            <p className="text-xs text-muted mt-1.5">
              Model spread: {tossup.prediction.predicted_spread.toFixed(1)} · total{" "}
              {tossup.prediction.predicted_total.toFixed(1)}
            </p>
            <Link
              href={`/h2h/${tossup.away_team_id}/${tossup.home_team_id}`}
              className="text-xs text-team-primary hover:underline mt-2 inline-block"
              onClick={() =>
                trackEvent({
                  experiment_key: INSIGHT_ORDER_EXPERIMENT,
                  variant: insightOrderVariant,
                  event_type: "click",
                  page: "home",
                  card_key: "market_setup",
                  payload: { href: `/h2h/${tossup.away_team_id}/${tossup.home_team_id}` },
                })
              }
            >
              Open quick matchup read →
            </Link>
          </>
        ) : (
          <p className="text-sm text-muted">No active matchup edge yet for this slate.</p>
        ),
      },
      {
        key: "fantasy_env",
        persona: "fantasy",
        title: "First insight: fantasy game env",
        body: likelyShootout ? (
          <>
            <p className="text-sm">
              {likelyShootout.away_team_id} @ {likelyShootout.home_team_id} carries the highest projected total.
            </p>
            <p className="text-xs text-muted mt-1.5">
              Total {likelyShootout.prediction.predicted_total.toFixed(1)} · script{" "}
              {likelyShootout.prediction.game_script ?? "neutral"}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted">Projection-driven fantasy spotlights appear when weekly games load.</p>
        ),
      },
      {
        key: "model_confidence",
        persona: "analyst",
        title: "First insight: model confidence",
        body: highestConfidence ? (
          <>
            <p className="text-sm">
              {highestConfidence.home_team_id} vs {highestConfidence.away_team_id} is currently the strongest high-confidence edge.
            </p>
            <p className="text-xs text-muted mt-1.5">
              {Math.round(highestConfidence.prediction.home_win_prob * 100)}% home win prob
              {highestConfidence.prediction.calibration_score != null
                ? ` · calibration ${Math.round(highestConfidence.prediction.calibration_score * 100)}%`
                : ""}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted">Confidence tiers update once this week&apos;s projection set finishes.</p>
        ),
      },
    ],
    [highestConfidence, insightOrderVariant, likelyShootout, tossup, trackEvent],
  );

  const cardsByKey = new Map(cards.map((c) => [c.key, c]));
  const ordered = orderCardKeys(insightOrderVariant)
    .map((k) => cardsByKey.get(k))
    .filter((c): c is InsightCardDef => Boolean(c));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {ordered.map((card) => (
        <PersonaGate key={card.key} allowed={[card.persona]}>
          <Card title={card.title}>{card.body}</Card>
        </PersonaGate>
      ))}
    </div>
  );
}

function orderCardKeys(variant: string): InsightCardDef["key"][] {
  if (variant === "confidence_first") {
    return ["model_confidence", "market_setup", "fantasy_env"];
  }
  return ["market_setup", "fantasy_env", "model_confidence"];
}
