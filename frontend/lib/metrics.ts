// Pretty labels + formatting for metric keys returned by the backend analytics engine.

export const TEAM_METRIC_LABELS: Record<string, { label: string; fmt: (v: number | null) => string; group: "offense" | "defense" | "team" }> = {
  off_epa_per_play: { label: "EPA / play", fmt: rate3, group: "offense" },
  off_success_rate: { label: "Success rate", fmt: pct, group: "offense" },
  off_pass_epa_per_play: { label: "Pass EPA / play", fmt: rate3, group: "offense" },
  off_rush_epa_per_play: { label: "Rush EPA / play", fmt: rate3, group: "offense" },
  off_explosive_play_rate: { label: "Explosive play rate (20+)", fmt: pct, group: "offense" },
  off_red_zone_td_pct: { label: "Red-zone TD%", fmt: pct, group: "offense" },
  off_third_down_pct: { label: "3rd-down conv%", fmt: pct, group: "offense" },
  off_plays_per_game: { label: "Plays / game", fmt: int1, group: "offense" },
  off_yards_per_play: { label: "Yards / play", fmt: rate2, group: "offense" },
  points_per_game: { label: "Points / game", fmt: rate1, group: "offense" },

  def_epa_per_play: { label: "EPA / play allowed", fmt: rate3, group: "defense" },
  def_success_rate: { label: "Success rate allowed", fmt: pct, group: "defense" },
  def_explosive_play_rate: { label: "Explosive plays allowed", fmt: pct, group: "defense" },
  def_red_zone_td_pct: { label: "Red-zone TD% allowed", fmt: pct, group: "defense" },
  def_third_down_pct: { label: "3rd-down conv% allowed", fmt: pct, group: "defense" },
  def_yards_per_play: { label: "Yards / play allowed", fmt: rate2, group: "defense" },
  points_allowed_per_game: { label: "Points allowed / game", fmt: rate1, group: "defense" },
  sacks_per_game: { label: "Sacks / game", fmt: rate2, group: "defense" },

  turnover_margin_per_game: { label: "Turnover margin / game", fmt: rate2, group: "team" },
  pass_rate_neutral: { label: "Pass rate (neutral)", fmt: pct, group: "team" },
};

export const PLAYER_METRIC_LABELS: Record<string, { label: string; fmt: (v: number | null) => string }> = {
  games: { label: "Games", fmt: int0 },
  attempts: { label: "Attempts", fmt: int0 },
  completions: { label: "Completions", fmt: int0 },
  completion_pct: { label: "Completion %", fmt: pct },
  passing_yards: { label: "Passing yards", fmt: int0 },
  yards_per_attempt: { label: "Yards / attempt", fmt: rate2 },
  passing_tds: { label: "Pass TDs", fmt: int0 },
  interceptions: { label: "INTs", fmt: int0 },
  sack_rate: { label: "Sack rate", fmt: pct },
  passer_rating: { label: "Passer rating", fmt: rate1 },
  epa_per_play: { label: "EPA / play", fmt: rate3 },
  success_rate: { label: "Success rate", fmt: pct },
  adot: { label: "Avg depth of target", fmt: rate2 },
  cpoe: { label: "Comp % over expected", fmt: rate2 },
  rushing_yards: { label: "Rushing yards", fmt: int0 },
  rushing_tds: { label: "Rush TDs", fmt: int0 },
  carries: { label: "Carries", fmt: int0 },
  yards_per_carry: { label: "Yards / carry", fmt: rate2 },
  targets: { label: "Targets", fmt: int0 },
  receptions: { label: "Receptions", fmt: int0 },
  receiving_yards: { label: "Receiving yards", fmt: int0 },
  receiving_tds: { label: "Receiving TDs", fmt: int0 },
  yards_per_reception: { label: "Yards / reception", fmt: rate2 },
  yards_per_target: { label: "Yards / target", fmt: rate2 },
  catch_rate: { label: "Catch rate", fmt: pct },
  target_share: { label: "Target share", fmt: pct },
  air_yards_share: { label: "Air-yards share", fmt: pct },
  yac: { label: "YAC", fmt: int0 },
  racr: { label: "RACR", fmt: rate2 },
  wopr: { label: "WOPR", fmt: rate2 },
  snap_share: { label: "Snap share", fmt: pct },
  red_zone_carries: { label: "Red-zone carries", fmt: int0 },
  epa_per_touch: { label: "EPA / touch", fmt: rate3 },
  fantasy_points_ppr: { label: "PPR fantasy pts", fmt: rate1 },
};

function int0(v: number | null): string { return v == null ? "—" : Math.round(v).toLocaleString(); }
function int1(v: number | null): string { return v == null ? "—" : v.toFixed(1); }
function rate1(v: number | null): string { return v == null ? "—" : v.toFixed(1); }
function rate2(v: number | null): string { return v == null ? "—" : v.toFixed(2); }
function rate3(v: number | null): string { return v == null ? "—" : v.toFixed(3); }
function pct(v: number | null): string {
  if (v == null) return "—";
  // Detect 0..1 vs 0..100 ranges automatically
  const n = Math.abs(v) <= 1 ? v * 100 : v;
  return `${n.toFixed(1)}%`;
}

export function teamMetricLabel(k: string): string {
  return TEAM_METRIC_LABELS[k]?.label ?? k;
}
export function teamMetricFmt(k: string, v: number | null): string {
  return TEAM_METRIC_LABELS[k]?.fmt(v) ?? String(v);
}
export function playerMetricLabel(k: string): string {
  return PLAYER_METRIC_LABELS[k]?.label ?? k;
}
export function playerMetricFmt(k: string, v: number | null): string {
  return PLAYER_METRIC_LABELS[k]?.fmt(v) ?? String(v);
}

export function pctColor(percentile: number | null): string {
  if (percentile == null) return "#374151";
  if (percentile >= 90) return "#10b981"; // emerald
  if (percentile >= 70) return "#22c55e"; // green
  if (percentile >= 40) return "#eab308"; // yellow
  if (percentile >= 20) return "#f97316"; // orange
  return "#ef4444";                       // red
}
