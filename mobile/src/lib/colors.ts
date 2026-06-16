// Palette for comparison overlays. First color is the "primary" entity (team
// or player), so additional entities get colors[1..N].

export const OVERLAY_PALETTE = [
  "#22d3ee", // cyan
  "#f97316", // orange
  "#a78bfa", // violet
  "#34d399", // emerald
  "#f43f5e", // rose
];

export function pickColor(index: number, fallback?: string): string {
  if (index === 0 && fallback) return fallback;
  return OVERLAY_PALETTE[(index - (fallback ? 1 : 0)) % OVERLAY_PALETTE.length];
}
