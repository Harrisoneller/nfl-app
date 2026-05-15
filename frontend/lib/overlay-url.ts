// Serialize/deserialize overlay state to URL query params for shareable
// comparison views.
//
// Format: ?compare=PHI,SF,KC&season=2024&trend=off_epa_per_play
// Player pages use player IDs: ?compare=1234,5678
import type { Pickable } from "@/components/ComparisonPicker";

export function readOverlayParams(search: URLSearchParams): {
  compareIds: string[];
  season: number | undefined;
  trendMetric: string | undefined;
} {
  const compare = (search.get("compare") || "").split(",").map((s) => s.trim()).filter(Boolean);
  const seasonRaw = search.get("season");
  const season = seasonRaw ? Number(seasonRaw) : undefined;
  const trendMetric = search.get("trend") || undefined;
  return { compareIds: compare, season, trendMetric };
}

export function writeOverlayParams(args: {
  compareIds: string[];
  season?: number;
  trendMetric?: string;
}): string {
  const params = new URLSearchParams();
  if (args.compareIds.length) params.set("compare", args.compareIds.join(","));
  if (args.season) params.set("season", String(args.season));
  if (args.trendMetric) params.set("trend", args.trendMetric);
  const s = params.toString();
  return s ? `?${s}` : "";
}

export function syncUrlOverlays(
  overlays: Pickable[],
  season: number | undefined,
  trendMetric: string,
) {
  if (typeof window === "undefined") return;
  const qs = writeOverlayParams({
    compareIds: overlays.map((o) => o.id),
    season,
    trendMetric,
  });
  const newUrl = window.location.pathname + qs;
  if (newUrl !== window.location.pathname + window.location.search) {
    window.history.replaceState({}, "", newUrl);
  }
}
