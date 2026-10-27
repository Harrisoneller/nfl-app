export interface LuckComponent {
  id: string;
  label: string;
  note: string;
  residual?: number;
  z?: number | null;
  samples?: number;
  shrink_k?: number;
  shrink_weight?: number | null;
}

export interface LuckTeam {
  team: string;
  score: number | null;
  side: "Lucky" | "Unlucky" | "Even" | null;
  rank: number | null;
  league_size: number;
  components: LuckComponent[];
}

export interface LuckOmitted {
  id: string;
  reason: string;
}

export interface TeamLuck {
  team_id: string;
  season: number;
  title: string;
  disclaimer: string;
  scale: { negative: string; positive: string };
  method: string;
  source?: string;
  omitted: LuckOmitted[];
  components: Array<{ id: string; label: string; note: string }>;
  unavailable: Array<{ id: string; reason: string }>;
  team: LuckTeam | null;
  table: LuckTeam[];
  error?: string;
}

export function formatSigned(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "—";
  const shown = value.toFixed(digits);
  return value > 0 ? `+${shown}` : shown;
}
