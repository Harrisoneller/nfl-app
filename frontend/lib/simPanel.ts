/**
 * Display copy for the frozen drive-sim panel.
 * The headline stays the v5 projection. This text never mentions a line,
 * a price, an edge, or a pick.
 */

export const SIM_PANEL_LABEL = "Sim (research, drive-level simulation)";
export const SIM_PANEL_NOTE = "Experimental. Not the official projection.";
export const SIM_TOTAL_LABEL = "Sim projected total";
export const SIM_MARGIN_LABEL = "Sim projected margin (home)";
export const SIM_WP_LABEL = "Sim home win probability";

export const SIM_PUBLIC_FIELDS = [
  "game_id",
  "season",
  "week",
  "sim_total",
  "sim_margin_home",
  "sim_home_wp",
  "frozen_at",
  "source_commit",
  "sha256",
] as const;

export function formatSimTotal(value: number): string {
  return Number(value).toFixed(1);
}

/** Positive means the sim has the home team ahead. */
export function formatSimMarginHome(value: number): string {
  const number = Number(value);
  const body = Math.abs(number).toFixed(1);
  if (number > 0) return `+${body}`;
  if (number < 0) return `-${body}`;
  return body;
}

export function formatSimHomeWp(value: number): string {
  return `${(Number(value) * 100).toFixed(1)}%`;
}

export function formatFrozenAt(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const yyyy = parsed.getUTCFullYear();
  const mm = String(parsed.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(parsed.getUTCDate()).padStart(2, "0");
  const hh = String(parsed.getUTCHours()).padStart(2, "0");
  const min = String(parsed.getUTCMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min} UTC`;
}

export function simPanelCopy(): string {
  return [
    SIM_PANEL_LABEL,
    SIM_TOTAL_LABEL,
    SIM_MARGIN_LABEL,
    SIM_WP_LABEL,
    "Frozen",
    SIM_PANEL_NOTE,
  ].join(" ");
}
