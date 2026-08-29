import { Suspense } from "react";
import type { Metadata } from "next";
import { api, type WeekSlateResponse } from "@/lib/api";
import { WeekSlateView } from "@/components/WeekSlateView";
import { Card } from "@/components/Card";

export const revalidate = 60;
export const maxDuration = 60;

export const metadata: Metadata = {
  title: "Week slate",
  description:
    "Model projections vs market lines for every NFL matchup this week. Defaults to the next regular-season week.",
};

/**
 * Server-render the slate so the week page is not a blank "Loading slate…"
 * spinner waiting on a browser → Railway fetch.
 */
export default async function WeekSlatePage() {
  const fallbackData = await api
    .weekSlate(undefined, undefined, { revalidate: 60 })
    .catch((): WeekSlateResponse | null => null);
  return (
    <Suspense
      fallback={
        <Card>
          <p className="text-sm text-muted">Loading slate…</p>
        </Card>
      }
    >
      <WeekSlateView fallbackData={fallbackData} />
    </Suspense>
  );
}
