// League-agnostic view model for the team efficiency profile.
//
// The component reads labels and tooltips from TeamProfileConfig. The NFL
// copy lives here; a college build can pass a different config against the
// same JSON shape. Numbers come from the feature contract, not from this file.

export type RankSense = "better" | "tougher";

export type ProfileMetric = {
  value: number | null;
  percentile: number | null;
  higher_is_better: boolean;
  plays: number | null;
  rank_sense: RankSense;
};

export type UnitProfile = {
  epa: ProfileMetric;
  success_rate: ProfileMetric;
  schedule_strength: ProfileMetric;
};

export type SplitProfile = {
  id: string;
  label: string;
  approximated?: boolean;
  approximation_note?: string | null;
  offense: UnitProfile;
  defense: UnitProfile;
};

export type RollingGame = {
  week: number | null;
  game_id?: string;
  opponent: string | null;
  location?: "home" | "away" | null;
  label: string;
  plays: number;
  value: number | null;
  rolling4: number | null;
  season_average: number | null;
};

export type RollingUnit = {
  metric: string;
  season_average: number | null;
  last4_average: number | null;
  games: RollingGame[];
  empty_reason?: string | null;
};

export type EfficiencyFilters = {
  garbage_time_excluded: boolean;
  wp_low: number;
  wp_high: number;
  description: string;
  plays_kept?: number;
  plays_excluded?: number;
  preseason_excluded?: boolean;
};

export type EfficiencyProfile = {
  team_id: string;
  season: number;
  league: string;
  source?: string;
  filters: EfficiencyFilters;
  notes?: string[];
  unavailable?: { id: string; reason: string }[];
  splits: SplitProfile[];
  rolling: {
    offense: RollingUnit;
    defense: RollingUnit;
  };
  error?: string;
};

export type PlainTip = { label: string; body: string };

export type TeamProfileConfig = {
  league: string;
  title: string;
  garbageTimeNote: string;
  methodNote: string;
  rollingNote: string;
  splitTooltips: Record<string, PlainTip>;
  metrics: {
    offenseEpa: PlainTip;
    offenseSuccess: PlainTip;
    offenseSchedule: PlainTip;
    defenseEpa: PlainTip;
    defenseSuccess: PlainTip;
    defenseSchedule: PlainTip;
  };
  rolling: {
    offense: PlainTip;
    defense: PlainTip;
  };
};

export const NFL_TEAM_PROFILE_CONFIG: TeamProfileConfig = {
  league: "NFL",
  title: "Efficiency profile",
  garbageTimeNote:
    "Garbage time is excluded. Only plays with win probability between 10% and 90% are counted.",
  methodNote:
    "EPA per play and success rate are schedule-adjusted. They estimate how this team would look against an average opponent, and small samples are pulled toward the league average. Home field is included in that adjustment.",
  rollingNote:
    "Each bar is one game from the feature table. The solid line is a play-weighted average of the last four games. The dashed line is the play-weighted season average of those same games.",
  splitTooltips: {
    overall: {
      label: "All plays",
      body: "Every pass and run in a competitive game. Kneel-downs and spikes are left out.",
    },
    pass: {
      label: "Pass",
      body: "Dropbacks only: throws, sacks, and scrambles tagged as pass plays.",
    },
    rush: {
      label: "Rush",
      body: "Designed runs. Quarterback kneels are not counted as rushes.",
    },
    early_down: {
      label: "Early downs",
      body: "Downs where the offense can still stay on schedule: 1st down, 2nd and 7 or fewer, and 3rd or 4th and 4 or fewer.",
    },
    passing_down: {
      label: "Passing downs",
      body: "Obvious passing situations: 2nd and 8 or more, and 3rd or 4th and 5 or more.",
    },
  },
  metrics: {
    offenseEpa: {
      label: "EPA per play",
      body: "Expected points added per play versus an average opponent. Positive means the offense creates more value than a typical team after the schedule is taken into account. Zero is league average.",
    },
    offenseSuccess: {
      label: "Success rate",
      body: "Share of plays that stay on schedule, adjusted for opponent. A play is a success when it gains about 40% of the yards to go on 1st down, 60% on 2nd, or the full line to gain on 3rd and 4th.",
    },
    offenseSchedule: {
      label: "Schedule strength",
      body: "How much better or worse than average the defenses on the schedule were. Positive means a tougher slate. The EPA and success rate beside it are already adjusted for this.",
    },
    defenseEpa: {
      label: "EPA per play allowed",
      body: "Expected points the opposing offense added per play, versus an average offense. Lower is a better defense. Already adjusted for who this team faced.",
    },
    defenseSuccess: {
      label: "Success rate allowed",
      body: "How often opponents stay on schedule, adjusted for who this team played. Lower is better.",
    },
    defenseSchedule: {
      label: "Schedule strength",
      body: "How much better or worse than average the offenses on the schedule were. Positive means this defense faced a tougher slate. The EPA and success rate beside it are already adjusted for this.",
    },
  },
  rolling: {
    offense: {
      label: "Offensive EPA per play",
      body: "Points the offense added or cost on a typical competitive play. Above the season line means the last four games were better than the year as a whole.",
    },
    defense: {
      label: "EPA per play allowed",
      body: "Points opponents added on a typical competitive play. Below the season line means the last four games were stingier than the year as a whole.",
    },
  },
};

export function formatEpa(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (Math.abs(value) < 0.0005) return "0.000";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(3)}`;
}

export function formatSuccess(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatSigned(value: number | null): string {
  return formatEpa(value);
}

export function ordinal(percentile: number): string {
  const value = Math.round(percentile);
  const mod100 = value % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${value}th`;
  switch (value % 10) {
    case 1: return `${value}st`;
    case 2: return `${value}nd`;
    case 3: return `${value}rd`;
    default: return `${value}th`;
  }
}

export function rankCaption(metric: ProfileMetric): string {
  if (metric.percentile == null) {
    if (metric.plays && metric.plays > 0) return `${metric.plays} plays`;
    return "—";
  }
  if (metric.rank_sense === "tougher") {
    return `tougher than ${Math.round(metric.percentile)}%`;
  }
  return `${ordinal(metric.percentile)} percentile`;
}
