"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";

const SESSION_KEY = "nfl-app.session-id";
const RETURN_KEY = "nfl-app.last-seen.insight-order";

export const INSIGHT_ORDER_EXPERIMENT = "insight_card_order_v1";

type ExperimentContextValue = {
  sessionId: string | null;
  insightOrderVariant: string;
  trackEvent: (event: {
    experiment_key: string;
    variant: string;
    event_type: string;
    page: string;
    card_key?: string;
    payload?: Record<string, unknown>;
  }) => void;
  markReturnIfEligible: (variant: string) => void;
};

const ExperimentContext = createContext<ExperimentContextValue | null>(null);

export function ExperimentProvider({ children }: { children: React.ReactNode }) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [insightOrderVariant, setInsightOrderVariant] = useState<string>("control");

  useEffect(() => {
    const existing = window.localStorage.getItem(SESSION_KEY);
    if (existing) {
      setSessionId(existing);
      return;
    }
    const created = `sess_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    window.localStorage.setItem(SESSION_KEY, created);
    setSessionId(created);
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    api.experimentAssign(INSIGHT_ORDER_EXPERIMENT, sessionId)
      .then((a) => {
        if (!cancelled) setInsightOrderVariant(a.variant || "control");
      })
      .catch(() => {
        if (!cancelled) setInsightOrderVariant(stableLocalBucket(sessionId));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const trackEvent: ExperimentContextValue["trackEvent"] = useCallback(
    (event) => {
      if (!sessionId) return;
      api.trackExperimentEvents([{ ...event, session_id: sessionId }]).catch(() => {
        // best-effort analytics; user experience should not depend on this call.
      });
    },
    [sessionId],
  );

  const markReturnIfEligible = useCallback(
    (variant: string) => {
      if (!sessionId) return;
      const now = Date.now();
      const raw = window.localStorage.getItem(RETURN_KEY);
      const prev = raw ? Number(raw) : null;
      window.localStorage.setItem(RETURN_KEY, String(now));
      if (!prev || Number.isNaN(prev)) return;
      const minutesAway = (now - prev) / 60_000;
      if (minutesAway < 10) return;
      trackEvent({
        experiment_key: INSIGHT_ORDER_EXPERIMENT,
        variant,
        event_type: "return",
        page: "home",
        payload: { minutes_away: Math.round(minutesAway) },
      });
    },
    [sessionId, trackEvent],
  );

  const value = useMemo(
    () => ({
      sessionId,
      insightOrderVariant,
      trackEvent,
      markReturnIfEligible,
    }),
    [sessionId, insightOrderVariant, trackEvent, markReturnIfEligible],
  );

  return <ExperimentContext.Provider value={value}>{children}</ExperimentContext.Provider>;
}

export function useExperiments() {
  const ctx = useContext(ExperimentContext);
  if (!ctx) throw new Error("useExperiments must be used within ExperimentProvider");
  return ctx;
}

function stableLocalBucket(sessionId: string): string {
  let hash = 0;
  const input = `${INSIGHT_ORDER_EXPERIMENT}:${sessionId}`;
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
  }
  return hash % 100 < 50 ? "control" : "confidence_first";
}
