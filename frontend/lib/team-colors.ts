// Static NFL team color map — lets any component color itself from a team id
// (or full team name) synchronously, without waiting on the API's Team object.
//
// `primary` is the team's signature color; `secondary` is the fallback we use
// when two teams in the same visual (e.g. a win-probability bar) have primaries
// too close to tell apart. See `pairColors`.

import { NFL_TEAM_NAMES } from "./team-names";

export type TeamColors = { primary: string; secondary: string };

export const TEAM_COLORS: Record<string, TeamColors> = {
  ARI: { primary: "#97233F", secondary: "#FFB612" },
  ATL: { primary: "#A71930", secondary: "#000000" },
  BAL: { primary: "#241773", secondary: "#9E7C0C" },
  BUF: { primary: "#00338D", secondary: "#C60C30" },
  CAR: { primary: "#0085CA", secondary: "#101820" },
  CHI: { primary: "#0B162A", secondary: "#C83803" },
  CIN: { primary: "#FB4F14", secondary: "#000000" },
  CLE: { primary: "#FF3C00", secondary: "#311D00" },
  DAL: { primary: "#003594", secondary: "#869397" },
  DEN: { primary: "#FB4F14", secondary: "#002244" },
  DET: { primary: "#0076B6", secondary: "#B0B7BC" },
  GB: { primary: "#203731", secondary: "#FFB612" },
  HOU: { primary: "#03202F", secondary: "#A71930" },
  IND: { primary: "#002C5F", secondary: "#A2AAAD" },
  JAX: { primary: "#006778", secondary: "#9F792C" },
  KC: { primary: "#E31837", secondary: "#FFB81C" },
  LAC: { primary: "#0080C6", secondary: "#FFC20E" },
  LAR: { primary: "#003594", secondary: "#FFA300" },
  LV: { primary: "#000000", secondary: "#A5ACAF" },
  MIA: { primary: "#008E97", secondary: "#FC4C02" },
  MIN: { primary: "#4F2683", secondary: "#FFC62F" },
  NE: { primary: "#002244", secondary: "#C60C30" },
  NO: { primary: "#D3BC8D", secondary: "#101820" },
  NYG: { primary: "#0B2265", secondary: "#A71930" },
  NYJ: { primary: "#125740", secondary: "#000000" },
  PHI: { primary: "#004C54", secondary: "#A5ACAF" },
  PIT: { primary: "#FFB612", secondary: "#101820" },
  SEA: { primary: "#002244", secondary: "#69BE28" },
  SF: { primary: "#AA0000", secondary: "#B3995D" },
  TB: { primary: "#D50A0A", secondary: "#FF7900" },
  TEN: { primary: "#0C2340", secondary: "#4B92DB" },
  WAS: { primary: "#5A1414", secondary: "#FFB612" },
};

const NEUTRAL = "#64748b"; // slate-500 — readable on either theme

/** Normalize a 3-letter id or a full team name to our canonical id. */
function normalizeId(key: string | null | undefined): string | null {
  if (!key) return null;
  const k = key.trim();
  if (TEAM_COLORS[k.toUpperCase()]) return k.toUpperCase();
  const byName = NFL_TEAM_NAMES[k.toLowerCase()];
  return byName ?? null;
}

/** Primary color for a team id/name, or a neutral fallback. */
export function teamColor(key: string | null | undefined, fallback = NEUTRAL): string {
  const id = normalizeId(key);
  return id ? TEAM_COLORS[id].primary : fallback;
}

export function teamColors(key: string | null | undefined): TeamColors | null {
  const id = normalizeId(key);
  return id ? TEAM_COLORS[id] : null;
}

// --- Contrast helpers ------------------------------------------------------ #

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const v = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(v, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Perceptual-ish distance between two hex colors (0 = identical). */
export function colorDistance(a: string, b: string): number {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  // Weighted euclidean (approx. human sensitivity), range ~0..765.
  const rMean = (r1 + r2) / 2;
  const dr = r1 - r2;
  const dg = g1 - g2;
  const db = b1 - b2;
  return Math.sqrt(
    (2 + rMean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rMean) / 256) * db * db,
  );
}

// Below this distance two colors read as "the same" on a small bar.
const CLASH_THRESHOLD = 110;

/**
 * Resolve two teams' colors so adjacent segments stay distinguishable.
 *
 * Tries primary vs primary; if they clash, swaps the *home* side to its
 * secondary, then the away side to its secondary, then finally drops one side
 * to a neutral. Always returns two colors at least CLASH_THRESHOLD apart when
 * possible.
 */
export function pairColors(
  awayKey: string | null | undefined,
  homeKey: string | null | undefined,
): { away: string; home: string } {
  const away = teamColors(awayKey);
  const home = teamColors(homeKey);
  const awayPrimary = away?.primary ?? NEUTRAL;
  const homePrimary = home?.primary ?? teamColor(homeKey);

  const candidates: Array<[string, string]> = [
    [awayPrimary, homePrimary],
    [awayPrimary, home?.secondary ?? homePrimary],
    [away?.secondary ?? awayPrimary, homePrimary],
    [awayPrimary, NEUTRAL],
    [NEUTRAL, homePrimary],
  ];

  let best: [string, string] = candidates[0];
  let bestDist = colorDistance(best[0], best[1]);
  for (const [a, h] of candidates) {
    const d = colorDistance(a, h);
    if (d >= CLASH_THRESHOLD) {
      return { away: a, home: h };
    }
    if (d > bestDist) {
      best = [a, h];
      bestDist = d;
    }
  }
  return { away: best[0], home: best[1] };
}
