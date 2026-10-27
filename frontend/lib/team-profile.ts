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
  /** epa: points per play. signed: a rating centered near zero. share: a 0–1 rate. */
  display?: "epa" | "signed" | "share";
  /** What the percentile ranks, when it is not the value in the cell. */
  percentile_of?: string | null;
  /** 1-based place from the bottom of this rating. Ties share a place. */
  rank?: number | null;
  league_size?: number | null;
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
  adjusted?: boolean;
  note?: string | null;
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
  /** Team-game points are the feature state entering that week. */
  as_of_week?: boolean;
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
    "The headline EPA and success figures are opponent-adjusted ratings from the feature contract. Zero is an average team. A percentile ranks that adjusted rating among the teams in the table. Higher is a better team. Defense is lower-is-better, so a smaller EPA allowed still ranks higher. Early-down and passing-down percentiles rank the season rate.",
  rollingNote:
    "The solid line is the last four team-games. The dashed line is the season-to-date rate. Both are the feature state entering that week, so the game itself is not included yet.",
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
      body: "1st down, or 2nd and 7 or fewer. This row is the season rate. It is not a separate opponent-adjusted rating.",
    },
    passing_down: {
      label: "Passing downs",
      body: "2nd and 8 or more, or 3rd or 4th and 5 or more. This row is the season rate. It is not a separate opponent-adjusted rating.",
    },
  },
  metrics: {
    offenseEpa: {
      label: "EPA per play",
      body: "Opponent-adjusted offensive EPA per play. Positive means the offense creates more value than an average team. On early downs and passing downs this cell is the season rate instead, because those splits are not separately adjusted.",
    },
    offenseSuccess: {
      label: "Success rate",
      body: "On all plays, the opponent-adjusted success rating. Zero is average. On early downs and passing downs, the share of plays that succeeded.",
    },
    offenseSchedule: {
      label: "Team strength",
      body: "Schedule-adjusted team strength: opponent-adjusted offensive EPA minus 0.55 times opponent-adjusted EPA allowed. Higher is a stronger team. The same number is shown on both sides.",
    },
    defenseEpa: {
      label: "EPA per play allowed",
      body: "Opponent-adjusted EPA allowed per play. Positive means opponents gained more than they would against an average defense. Lower is better. The percentile ranks this rating, so a higher percentile is a better defense.",
    },
    defenseSuccess: {
      label: "Success rate allowed",
      body: "Opponent-adjusted success rate allowed. Positive means opponents stayed on schedule more often than they would against an average defense. Lower is better.",
    },
    defenseSchedule: {
      label: "Team strength",
      body: "The same schedule-adjusted team strength as the offense column: offensive EPA minus 0.55 times EPA allowed, after opponent adjustment. Higher is a stronger team.",
    },
  },
  rolling: {
    offense: {
      label: "Offensive EPA per play",
      body: "Season-to-date offensive EPA per play and the last four team-games, as published for the start of each week. Above the season line means the recent games were better than the year to that point.",
    },
    defense: {
      label: "EPA per play allowed",
      body: "Season-to-date EPA allowed and the last four team-games, as of the start of each week. Below the season line means the recent games were stingier.",
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
  const rounded = Math.round(percentile * 10) / 10;
  const whole = Math.trunc(Math.abs(rounded));
  const mod100 = whole % 100;
  let suffix = "th";
  if (mod100 < 11 || mod100 > 13) {
    if (whole % 10 === 1) suffix = "st";
    else if (whole % 10 === 2) suffix = "nd";
    else if (whole % 10 === 3) suffix = "rd";
  }
  const body = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${body}${suffix}`;
}

export function rankCaption(metric: ProfileMetric): string {
  if (metric.percentile == null) {
    if (metric.plays && metric.plays > 0) return `${metric.plays} plays`;
    return "—";
  }
  if (metric.rank_sense === "tougher") {
    return `tougher than ${Math.round(metric.percentile)}%`;
  }
  const ofWhat = metric.percentile_of ? ` of ${metric.percentile_of}` : "";
  const label = `${ordinal(metric.percentile)} percentile${ofWhat}`;
  if (metric.rank != null && metric.league_size != null) {
    return `${label} · ${metric.rank} of ${metric.league_size}`;
  }
  return label;
}
