// Typed API client — every call goes through here.

import { getStoredToken } from "./auth-storage";

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type UserProfile = {
  id: string;
  email: string;
  display_name: string;
  is_admin: boolean;
};

export type AuthTokenResponse = {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
};

export type Team = {
  id: string;
  espn_id: number | null;
  name: string;
  market: string;
  full_name: string;
  conference: string;
  division: string;
  primary_color: string;
  secondary_color: string;
  logo_url: string;
};

export type Player = {
  id: string;
  full_name: string;
  position: string;
  team_id: string | null;
  jersey_number: number | null;
  age: number | null;
  height: string | null;
  weight: number | null;
  college: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
};

export type Game = {
  id: string;
  season: number;
  week: number | null;
  season_type: number;
  start_time: string | null;
  status: string;
  status_detail: string;
  home_team_id: string | null;
  away_team_id: string | null;
  home_score: number | null;
  away_score: number | null;
  venue: string;
  broadcast: string;
};

export type NewsItem = {
  id: string;
  source: string;
  source_label: string;
  title: string;
  summary: string;
  link: string;
  author: string;
  image_url: string;
  published_at: string | null;
};

export type OddsLine = {
  id: number;
  market: string;
  event_id: string | null;
  home_team: string | null;
  away_team: string | null;
  commence_time: string | null;
  bookmaker: string;
  label: string;
  price: number | null;
  point: number | null;
};

export type Widget = {
  id: string;
  title: string;
  kind: string;
  spec: Record<string, unknown>;
  pinned: boolean;
  sort_order: number;
  last_rendered_at: string | null;
  created_at: string;
};

function buildHeaders(extra?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = getStoredToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (extra) {
    const raw = extra instanceof Headers ? Object.fromEntries(extra.entries()) : extra;
    Object.assign(headers, raw as Record<string, string>);
  }
  return headers;
}

async function parseApiError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: string | { msg?: string }[] };
    const d = body.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0] && typeof d[0] === "object" && "msg" in d[0]) {
      return String(d[0].msg);
    }
  } catch {
    /* not JSON */
  }
  return text || `${res.status} ${res.statusText}`;
}

type FetchPolicy = {
  cache?: RequestCache;
  revalidate?: number;
};

async function req<T>(path: string, init?: RequestInit, policy?: FetchPolicy): Promise<T> {
  const nextConfig = policy?.revalidate != null
    ? ({ revalidate: policy.revalidate } as RequestInit["next"])
    : undefined;
  const resolvedCache = policy
    ? (policy.cache ?? (policy.revalidate != null ? undefined : "no-store"))
    : "no-store";
  const res = await fetch(`${BASE}${path}`, {
    ...(resolvedCache ? { cache: resolvedCache } : {}),
    headers: buildHeaders(init?.headers),
    next: nextConfig,
    ...init,
  });
  if (!res.ok) throw new Error(await parseApiError(res));
  return res.json();
}

export type MetricCard = { value: number | null; percentile: number | null; higher_is_better: boolean };
export type TeamProfile = {
  team_id: string;
  season: number;
  metrics: Record<string, MetricCard>;
  record: { wins: number; losses: number; ties: number };
  error?: string;
};
export type PlayerProfile = {
  player_id: string;
  season: number;
  position: string;
  team: string | null;
  peer_count: number;
  metrics: Record<string, MetricCard>;
  error?: string;
};
export type TrendPoint = { season: number; value: number | null; percentile?: number | null };

export type GamePrediction = {
  id: string;
  season: number;
  week: number;
  gameday: string;
  gametime?: string;
  home_team_id: string;
  away_team_id: string;
  home_score: number | null;
  away_score: number | null;
  home_elo: number;
  away_elo: number;
  prediction: {
    home_win_prob: number;
    away_win_prob: number;
    home_win_prob_interval_80?: [number, number];
    away_win_prob_interval_80?: [number, number];
    predicted_spread: number;
    predicted_total: number;
    predicted_home_score: number;
    predicted_away_score: number;
    game_script?: string;
    calibration_score?: number;
    expected_calibration_error?: number | null;
    confidence_tier?: "low" | "medium" | "high";
    model_version?: string;
    inputs?: PredictionInputs;
    explainability?: PredictionExplainability;
    // ---- Market-aware layer (market-blend-v1) ----
    // "market-blend-v1" when headline numbers are blended with consensus,
    // "model_only" when no market data existed for the game.
    prediction_basis?: string;
    model_only?: {
      home_win_prob: number;
      away_win_prob: number;
      predicted_spread: number;
      predicted_total: number;
    };
    market?: {
      consensus_home_prob: number;
      spread_home: number | null;
      total: number | null;
      books: number;
      kalshi_home_prob?: number | null;
      effective_sources: number;
      movement?: {
        open_home_prob: number;
        latest_home_prob: number;
        delta_home_prob: number;
        snapshots: number;
      } | null;
      sources?: { sportsbooks: number; kalshi: boolean };
      weight: number;
    };
    // Model − market disagreement (positive win-prob edge = model likes HOME
    // more than the market does). This gap is the value signal.
    edge?: {
      home_win_prob: number;
      spread: number | null;
      total: number | null;
    };
  };
  ml_prediction?: {
    predicted_spread: number;
    predicted_home_margin: number;
  };
};

export type PredictionInputs = {
  home_elo: number;
  away_elo: number;
  home_field_advantage_elo: number;
  neutral_site: boolean;
  home_off_ppg: number;
  away_off_ppg: number;
  home_def_ppg_allowed: number;
  away_def_ppg_allowed: number;
  expected_home_pts: number;
  expected_away_pts: number;
};

export type PredictionExplainability = {
  method: string;
  summary: string;
  top_contributors: Array<{
    feature: string;
    label: string;
    impact: number;
    direction: "home" | "away";
  }>;
  confidence_context?: {
    tier?: "low" | "medium" | "high" | string;
    calibration_score?: number;
    expected_calibration_error?: number | null;
    interval_80_home_win_prob?: [number, number];
  };
};

export type BacktestRow = {
  season?: number;
  n_games: number;
  spread_mae: number;
  spread_rmse: number;
  classifier_accuracy_pct: number;
  brier_score: number;
  high_confidence_accuracy_pct: number | null;
  high_confidence_n: number;
  ats_picks_n: number;
  ats_correct_pct: number | null;
};

export type CalibrationBin = {
  bin_lo: number;
  bin_hi: number;
  n: number;
  predicted_avg: number | null;
  actual_win_rate: number | null;
};

export type EloBacktest = {
  seasons: number[];
  n_games: number;
  overall: BacktestRow;
  per_season: Array<BacktestRow & { season: number }>;
  calibration: CalibrationBin[];
};

export type MLBacktest = {
  available: boolean;
  reason?: string;
  train_seasons?: number[];
  test_season?: number;
  n_train?: number;
  n_test?: number;
  spread_mae?: number;
  spread_rmse?: number;
  classifier_accuracy_pct?: number;
  feature_importance?: Array<{ feature: string; importance: number }>;
};

export type BettingRecord = {
  games: number;
  su: { wins: number; losses: number; ties: number };
  ats: { wins: number; losses: number; pushes: number; win_pct: number };
  ou: { overs: number; unders: number; pushes: number; over_pct: number };
  as_favorite: { games: number; wins: number; losses: number; win_pct: number };
  as_underdog: { games: number; wins: number; losses: number; win_pct: number };
  home_split: { games: number; wins: number; losses: number; win_pct: number };
  away_split: { games: number; wins: number; losses: number; win_pct: number };
};

export type TeamBettingHistory = {
  team_id: string;
  seasons: number[];
  lifetime: BettingRecord;
  last20: BettingRecord;
};

export type EdgeGame = GamePrediction & {
  market?: {
    market_spread_home: number | null;
    market_total: number | null;
    market_home_win_prob?: number | null;
    books: number;
  } | null;
  edge_spread?: number | null;
  edge_total?: number | null;
  edge_win_prob?: number | null;
  recommendation?: string | null;
};

export type TeamEdgeResponse = {
  team_id: string;
  season: number;
  week: number | null;
  opponent?: string;
  games: EdgeGame[];
  empty_reason?: string;
};

export type AwardCandidate = {
  player_id: string;
  name: string;
  position: string;
  team: string | null;
  composite_score: number;
  odds_pct: number;
};

export type AwardsResponse = {
  season: number;
  mvp: AwardCandidate[];
  opoy: AwardCandidate[];
};

export type H2HMatchup = {
  team_a: string;
  team_b: string;
  season: number;
  elo: { a: number; b: number };
  grade: { a: string; b: string };
  record: { a: { wins: number; losses: number; ties: number }; b: { wins: number; losses: number; ties: number }; season: number };
  predicted_matchup: {
    home_team: string;
    away_team: string;
    week: number | null;
    gameday: string | null;
    neutral_site?: boolean;
    hypothetical?: boolean;
    played?: boolean;
    home_score?: number | null;
    away_score?: number | null;
    prediction: GamePrediction["prediction"];
  } | null;
  market_context?: {
    market?: {
      market_spread_home?: number | null;
      market_total?: number | null;
      market_home_win_prob?: number | null;
      books?: number;
    };
    market_delta?: {
      spread?: number | null;
      total?: number | null;
      home_win_prob?: number | null;
    };
  } | null;
  decision_metrics?: Array<{
    key: string;
    label: string;
    value: number | null;
    favored?: string | null;
    detail?: string;
  }>;
  profile: {
    a: any;
    b: any;
    deltas: Array<{
      metric: string;
      a_value: number; b_value: number;
      a_percentile: number | null; b_percentile: number | null;
      higher_is_better: boolean;
      winner: "a" | "b" | null;
      delta: number;
    }>;
  };
  history: {
    a_wins: number; b_wins: number; ties: number;
    games: Array<{
      season: number; week: number | null; gameday: string;
      home_team: string; away_team: string;
      home_score: number; away_score: number;
      winner: string | null;
      spread_line: number | null; total_line: number | null;
    }>;
  };
  elo_history: { a: EloHistoryPoint[]; b: EloHistoryPoint[] };
  matchup_breakdown: {
    when_a_has_ball: MatchupSide;
    when_b_has_ball: MatchupSide;
  };
  error?: string;
};

export type MatchupSide = {
  offense: string;
  defense: string;
  rows: MatchupRow[];
  advantage_count: number;
  metrics_count: number;
};

export type MatchupLean =
  | "even"
  | "slight_off" | "clear_off" | "strong_off" | "off"
  | "slight_def" | "clear_def" | "strong_def" | "def";

export type MatchupRow = {
  metric: string;
  label: string;
  off_value: number;
  def_value: number;
  league_avg: number | null;
  expected: number;
  delta: number;
  edge: number;
  edge_z: number | null;
  lean: MatchupLean;
  offense_has_edge: boolean;
  off_percentile: number | null;
  def_percentile_for_offense: number | null;
};

export type TeamSeasonOutlook = {
  team_id: string;
  season: number;
  mean_wins?: number;
  p5_wins?: number;
  median_wins?: number;
  p95_wins?: number;
  division_winner_pct?: number;
  playoff_pct?: number;
  sb_appearance_pct?: number;
  current_elo: number;
  grade: string;
};

export type EloRow = { team_id: string; rating: number; grade: string };

export type EloHistoryPoint = { season: number; week: number; rating: number };

export type ProjectedDivision = {
  conference: string;
  division: string;
  teams: Array<{
    team_id: string;
    mean_wins: number;
    playoff_pct: number;
    division_winner_pct: number;
    sb_appearance_pct: number;
  }>;
};

export type TeamRemainingGame = {
  id: string;
  week: number | null;
  gameday: string;
  opponent: string;
  is_home: boolean;
  played: boolean;
  outcome: "W" | "L" | "T" | null;
  my_score: number | null;
  opp_score: number | null;
  win_prob: number;
  predicted_spread_for_team: number;
  predicted_total: number;
  cumulative_projected_wins: number;
  opp_elo: number;
};

export type TeamRemainingSchedule = {
  team_id: string;
  season: number;
  games: TeamRemainingGame[];
  banked_wins: number;
  projected_remaining_wins: number;
  projected_total_wins: number;
};

export type StatDistribution = {
  predicted: number;
  low: number;
  high: number;
  mean: number;
  sd: number;
  interval_80: [number, number];
  env_multiplier: number;
  anytime_prob?: number;
  // Present when the mean was blended toward the market-implied value
  // (price-aware: target_mean uses the de-vigged over price, not just the line).
  market_anchor?: {
    line: number;
    target_mean?: number;
    over_prob?: number | null;
    books: number;
    weight: number;
    raw_mean: number;
  };
};

export type PlayerRole = {
  depth_chart_order: number | null;
  multiplier: number;
};

export type PlayerGamePrediction = {
  week: number | null;
  gameday: string;
  opponent: string;
  is_home: boolean;
  matchup_grade: "A" | "B" | "C" | "D" | "F";
  defense_factor: number;
  game_env: {
    team_implied_pts: number;
    opp_implied_pts: number;
    game_script: string;
    predicted_total: number;
  };
  weather: { summary: string | null; is_indoor: boolean; available: boolean };
  injury_status: string | null;
  injury_multiplier: number;
  predicted: Record<string, StatDistribution>;
  fantasy: Record<string, { mean: number; sd: number }>;
};

export type ProjectionEvidence = {
  prior_seasons: number[];
  prior_games: number;
  games_observed: number;
  rookie_prior: boolean;
  age: number | null;
};

export type PlayerGamePredictions = {
  player_id: string;
  name: string;
  position: string;
  team: string;
  season: number;
  model_version?: string;
  evidence?: ProjectionEvidence;
  role?: PlayerRole;
  games: PlayerGamePrediction[];
  error?: string;
};

export type SeasonStatProjection = {
  ytd: number;
  per_game_pace: number;
  projected_remaining: number;
  projected_final: number;
  low_final: number;
  high_final: number;
  sd_final?: number;
  quantiles?: Record<string, number>;
};

export type FantasyProjection = {
  mean: number;
  sd: number;
  quantiles: Record<string, number>;
  per_game: number;
};

export type PlayerSeasonProjection = {
  player_id: string;
  name: string;
  position: string;
  team: string | null;
  season: number;
  games_played: number;
  games_remaining: number;
  model_version?: string;
  evidence?: ProjectionEvidence;
  role?: PlayerRole;
  stats: Record<string, SeasonStatProjection>;
  fantasy?: Record<string, FantasyProjection>;
  error?: string;
};

export type LeaderboardPlayer = {
  rank: number;
  player_id: string | null;
  gsis_id: string;
  name: string;
  position: string;
  team: string | null;
  status: string | null;
  injury_status: string | null;
  role?: PlayerRole;
  rookie?: boolean;
  games_remaining: number;
  next_game: { week: number | null; opponent: string; is_home: boolean; game_script: string } | null;
  stats: Record<string, { mean: number; p10: number; p90: number }>;
  fantasy_ppr: { mean: number; p10: number; p90: number; per_game: number };
  fantasy_half_ppr: { mean: number; p10: number; p90: number; per_game: number };
  fantasy_standard: { mean: number; p10: number; p90: number; per_game: number };
  // ---- Fantasy market layer (ADP + trending) ----
  market?: {
    adp: number | null;
    adp_overall_rank: number | null;
    adp_pos_rank: number | null;
    trending_adds: number | null;
    // ADP rank − model rank. Positive = drafters take this player LATER than
    // our model ranks him (model sees value); negative = market is higher.
    value_vs_adp: number | null;
    adp_weight: number;
  };
  consensus_rank_score?: number;
  consensus_rank?: number;
};

export type PositionCoverage = {
  teams: number;
  total_teams: number;
  missing: string[];
};

export type ProjectionLeaderboard = {
  season: number;
  scoring: string;
  sort?: string;
  position: string | null;
  model_version: string;
  count: number;
  coverage?: Record<string, PositionCoverage>;
  players: LeaderboardPlayer[];
};

export type PlayerProp = {
  event_id: string;
  player_id?: string | null;
  market: string;
  market_label: string;
  player_name: string;
  line: number | null;
  market_over_prob: number | null;
  books: number;
  commence_time: string | null;
  model_over_prob?: number;
  model_mean?: number;
  model_sd?: number;
  edge?: number;
  side?: "over" | "under";
  week?: number | null;
  opponent?: string;
};

export type PlayerProps = {
  player_id: string;
  name?: string;
  count: number;
  props: PlayerProp[];
  model_version?: string;
  error?: string;
};

export type PropEdges = {
  count: number;
  min_edge: number;
  min_books: number;
  model_version: string;
  edges: PlayerProp[];
  note: string;
};

// ---- Weekly board / Prop Finder / Fantasy insights / Compare ---------------- #

export type WeeklyFantasyBand = { mean: number; sd: number; p10: number; p90: number };

export type WeeklyBoardPlayer = {
  player_id: string;
  name: string;
  position: string;
  team: string | null;
  injury_status: string | null;
  role?: PlayerRole;
  rookie?: boolean;
  bye: boolean;
  tier: string;
  pos_rank: number;
  week?: number | null;
  opponent?: string;
  is_home?: boolean;
  gameday?: string;
  matchup_grade?: string;
  defense_factor?: number;
  game_env?: {
    team_implied_pts: number;
    opp_implied_pts: number;
    game_script: string;
    predicted_total: number;
  };
  weather?: { summary: string | null; is_indoor: boolean; available: boolean };
  injury_multiplier?: number;
  predicted?: Record<string, { predicted: number; low: number; high: number; mean: number; sd: number; anytime_prob?: number }>;
  fantasy?: Record<string, WeeklyFantasyBand>;
  // Fantasy market momentum (waiver-wire signal).
  market?: {
    adp: number | null;
    adp_pos_rank: number | null;
    trending_adds: number | null;
    adp_weight: number;
  };
};

export type WeeklyBoard = {
  season: number;
  week: number | null;
  scoring: string;
  position: string | null;
  model_version: string;
  tier_note?: string;
  count: number;
  players: WeeklyBoardPlayer[];
};

export type PropBoardBook = {
  book: string;
  line: number | null;
  over_price: number | null;
  under_price: number | null;
  over_implied: number | null;
  under_implied: number | null;
  model_over_prob: number | null;
  edge_over: number | null;
  edge_under: number | null;
};

export type PropBoardBest = {
  book: string;
  line: number | null;
  price: number;
  edge: number;
  model_prob: number | null;
};

export type PropBoardRow = {
  event_id: string;
  market: string;
  market_label: string;
  player_name: string;
  player_id: string | null;
  position: string | null;
  team: string | null;
  commence_time: string | null;
  home_team_id: string | null;
  away_team_id: string | null;
  consensus_line: number | null;
  market_over_prob: number | null;
  books_count: number;
  model_mean: number | null;
  model_sd: number | null;
  model_over_prob: number | null;
  best_over: PropBoardBest | null;
  best_under: PropBoardBest | null;
  best_edge: number | null;
  books: PropBoardBook[];
};

export type PropBoard = {
  count: number;
  total: number;
  markets: string[];
  market_labels: Record<string, string>;
  games: { event_id: string; home_team_id: string | null; away_team_id: string | null; commence_time: string | null }[];
  model_version: string;
  note: string;
  props: PropBoardRow[];
};

export type RosPlayer = {
  player_id: string | null;
  name: string;
  position: string;
  team: string | null;
  injury_status: string | null;
  rookie?: boolean;
  role?: PlayerRole;
  pos_rank: number;
  overall_rank: number;
  tier: number;
  games_remaining: number;
  per_game: number;
  ros_points: number;
  ros_sd: number;
  replacement_per_game: number;
  vorp_per_game: number;
  vorp_ros: number;
  next_game: LeaderboardPlayer["next_game"];
  // ADP + trending context; value_vs_adp is vs this board's VORP rank.
  market?: LeaderboardPlayer["market"];
};

export type RosBoard = {
  season: number;
  scoring: string;
  league_size: number;
  replacement_levels: Record<string, number>;
  model_version: string;
  note: string;
  count: number;
  players: RosPlayer[];
};

export type WaiverTarget = RosPlayer & {
  trend_count: number;
  schedule_ease_next3: number | null;
  waiver_score: number;
  reasons: string[];
};

export type WaiverBoard = {
  season: number;
  scoring: string;
  model_version?: string;
  note: string;
  count: number;
  targets: WaiverTarget[];
};

export type TradeSide = {
  players: (RosPlayer & { note?: string })[];
  missing: string[];
  vorp_ros: number;
  sd: number;
};

export type TradeResult = {
  season: number;
  scoring: string;
  league_size: number;
  model_version?: string;
  side_a: TradeSide;
  side_b: TradeSide;
  difference_vorp: number;
  uncertainty_sd: number;
  verdict: "side_a" | "side_b" | "toss-up";
  detail: string;
  note: string;
};

export type UsageWeek = {
  week: number;
  opponent?: string;
  target_share?: number;
  carry_share?: number;
} & Partial<Record<string, number>>;

export type UsageProfile = {
  season: number;
  games: number;
  weekly: UsageWeek[];
  shares: { target_share?: number; carry_share?: number };
  consistency: {
    ppg_ppr?: number;
    sd?: number;
    cv?: number | null;
    floor_p25?: number;
    ceiling_p75?: number;
    best?: number;
    worst?: number;
  };
};

export type ComparePlayerEntry = {
  player_id: string;
  name?: string;
  position?: string;
  team?: string | null;
  injury_status?: string | null;
  error?: string;
  season_projection?: {
    season: number;
    games_remaining: number;
    stats: Record<string, SeasonStatProjection>;
    fantasy: Record<string, FantasyProjection>;
    role?: PlayerRole;
    error?: string;
  };
  next_game?: PlayerGamePredictions["games"][number] | null;
  usage?: UsageProfile;
};

export type PlayerComparison = {
  season: number | null;
  usage_season: number;
  model_version: string;
  players: ComparePlayerEntry[];
};

export type OverProbResult = {
  player_id: string;
  stat: string;
  line?: number;
  week?: number | null;
  opponent?: string;
  mean?: number;
  sd?: number;
  over_prob?: number;
  under_prob?: number;
  prob?: number; // anytime TD
  expected_tds?: number;
  error?: string;
};

export type SeasonInfo = {
  available: number[];
  default: number;
  current_or_upcoming: number;
  info: Record<number, { season: number; is_upcoming: boolean; is_latest_completed: boolean }>;
};

export type FreshnessModule = {
  module: string;
  domains: string[];
  last_updated_at: string | null;
  age_seconds: number | null;
  sla_seconds: number;
  status: "ok" | "warn" | "stale";
};

export type FreshnessSnapshot = {
  generated_at: string;
  modules: FreshnessModule[];
};

export type ExperimentAssignment = {
  experiment_key: string;
  variant: string;
  enabled: boolean;
  bucket?: number;
};

export type UpcomingSeason = {
  team_id: string;
  season: number;
  is_upcoming: boolean;
  previous_season: number;
  schedule: {
    id: string;
    season: number;
    week: number | null;
    home_team_id: string | null;
    away_team_id: string | null;
    gameday: string;
    venue: string;
    network: string;
    opponent: string | null;
    opponent_prev_off_epa: number | null;
    opponent_prev_def_epa: number | null;
    opponent_prev_points_per_game: number | null;
  }[];
  strength_of_schedule: {
    avg_opponent_off_epa: number | null;
    avg_opponent_def_epa: number | null;
    n_games: number;
  };
};

// ---- Sparky (betting prediction & parlay intelligence) -------------------- #

export type SparkySignal = {
  key: string;
  label: string;
  side: "home" | "away" | "game";
  severity: "bullish" | "warning" | "info";
  magnitude: number;
  weight: number;
  explanation: string;
};

export type SparkyMovementPoint = {
  label: string;
  minutes_to_kickoff: number | null;
  home_prob: number;
  home_ml: number | null;
  away_ml: number | null;
};

export type SparkyMarket = {
  home_market_prob: number;
  away_market_prob: number;
  home_ml: number | null;
  away_ml: number | null;
  spread_home: number | null;
  total: number | null;
  book_count: number;
  favorite: "home" | "away";
  home_win_prob_ensemble?: number;
};

export type SparkyGame = {
  event_id: string;
  home_team_id: string | null;
  away_team_id: string | null;
  home_team: string | null;
  away_team: string | null;
  commence_time: string | null;
  predicted_winner: string | null;
  win_prob: number;
  home_win_prob?: number;
  model_prob: number | null;
  market_prob: number | null;
  confidence_score: number;
  base_confidence?: number;
  signal_delta?: number;
  classification: string | null;
  signals: SparkySignal[];
  explanation: string;
  market: SparkyMarket;
  movement?: SparkyMovementPoint[];
};

export type SparkyParlayLeg = {
  event_id: string;
  side: "home" | "away";
  team_id: string | null;
  opponent_id: string | null;
  price_american: number;
  win_prob: number;
  confidence: number;
  is_underdog: boolean;
  // Per-leg value transparency (added with variable-N parlays).
  market_implied?: number;     // vig-included implied prob at this price
  edge?: number;               // win_prob - market_implied
  expected_value?: number;     // EV per 1 unit staked at this price
  is_value?: boolean;          // expected_value > 0
};

export type SparkyParlay = {
  rank: number;
  legs: SparkyParlayLeg[];
  n_legs?: number;             // 2..8 (back-compat: pre-0010 rows imply 3)
  parlay_odds_american: number;
  parlay_odds_decimal: number;
  implied_prob: number;
  combined_win_prob: number;
  underdog_count: number;
  confidence_score: number;
  signal_alignment: number;
  composite_score: number;
  edge?: number;
  expected_value?: number;     // EV per 1 unit staked on the full parlay
  is_value?: boolean;
  kelly_fraction?: number;     // capped Kelly stake fraction (0 when -EV)
  explanation: string;
};

export type SparkySlate = {
  slate_date: string | null;
  count: number;
  games: SparkyGame[];
  recommended_parlays: SparkyParlay[];
  // Set by the backend's get_slate when there are upcoming odds snapshots but
  // no Sparky predictions are built yet — UI uses it to surface the "Build
  // real Week 1 slate" CTA on empty states.
  real_data_available?: boolean;
  // Set only by /admin/build_real so the admin UI can surface the upstream
  // Odds API result alongside the rebuilt slate.
  odds_refresh?: {
    status?: string;
    message?: string | null;
    upstream_events?: number;
    lines_in_db?: number;
  } | null;
};

export type SparkyGameDetail = {
  event_id: string;
  prediction: SparkyGame | null;
  movement: SparkyMovementPoint[];
  books: {
    book: string;
    home_ml: number | null;
    away_ml: number | null;
    home_spread: number | null;
    total: number | null;
    home_implied: number | null;
    captured_at: string | null;
  }[];
  book_count: number;
};

export type SparkyParlayResponse = {
  slate_id: string;
  slate_date: string;
  games: {
    event_id: string;
    home_team_id: string | null;
    away_team_id: string | null;
    home_ml: number | null;
    away_ml: number | null;
    favorite: string;
    home_prob: number;
  }[];
  parlays: SparkyParlay[];
};

export type AccuracyWindow = { n: number; correct?: number; accuracy_pct: number | null };
export type ParlayWindow = {
  n: number;
  rank_1_hit_rate: number | null;
  top_3_containment: number | null;
  top_4_containment: number | null;
};

export type SparkyAccuracy = {
  sport: string;
  as_of: string;
  individual_picks: {
    rolling: Record<string, AccuracyWindow>;
    by_confidence_band: { band: string; n: number; correct: number; accuracy_pct: number | null }[];
    by_signal: { signal: string; n: number; correct: number; accuracy_pct: number | null }[];
    overall: AccuracyWindow;
  };
  parlays: {
    rolling: Record<string, ParlayWindow>;
    overall: ParlayWindow;
  };
  trends: {
    overall_pick_accuracy_pct: number | null;
    overall_parlay_rank1_pct: number | null;
    overall_parlay_top3_pct: number | null;
    best_signal: { signal: string; accuracy_pct: number | null; n: number } | null;
    worst_signal: { signal: string; accuracy_pct: number | null; n: number } | null;
    n_picks_settled: number;
    n_parlays_settled: number;
  };
};

export type SparkyAdminStatus = {
  snapshots: number;
  snapshot_events: number;
  last_snapshot_at: string | null;
  predictions: number;
  last_slate_date: string | null;
  settled_results: number;
  parlay_rankings: number;
  pipeline_ready: boolean;
  has_history_for_movement: boolean;
};

export type SparkyGlossaryEntry = { key: string; label: string; definition: string };

// ---- Bet tracker + CLV profile ------------------------------------------- #

export type BetMarket = "spread" | "total" | "moneyline" | "player_prop";

export type BetLegInput = {
  market: BetMarket;
  selection: string;            // team_id, or "over"/"under" for totals/props
  selection_label?: string;
  line?: number | null;
  odds_american: number;
  event_id?: string | null;
  game_id?: string | null;
  home_team_id?: string | null;
  away_team_id?: string | null;
  commence_time?: string | null;
  // player_prop legs only
  player_name?: string | null;
  prop_market?: string | null;
};

export type BetInput = {
  bet_type: "straight" | "parlay";
  stake_units: number;
  stake_dollars?: number | null;
  source?: "manual" | "odds" | "sparky";
  note?: string;
  placed_at?: string | null;
  legs: BetLegInput[];
};

export type BetLeg = {
  id: number;
  market: BetMarket;
  selection: string;
  selection_label: string;
  line: number | null;
  odds_american: number;
  odds_decimal: number;
  event_id: string | null;
  home_team_id: string | null;
  away_team_id: string | null;
  commence_time: string | null;
  closing_line: number | null;
  closing_odds_american: number | null;
  clv_pct: number | null;
  clv_line: number | null;
  beat_close: boolean | null;
  leg_result: string;
};

export type Bet = {
  id: string;
  bet_type: "straight" | "parlay";
  status: "pending" | "won" | "lost" | "push" | "void";
  source: string;
  note: string;
  stake_units: number;
  stake_dollars: number | null;
  odds_american: number;
  odds_decimal: number;
  placed_at: string;
  settled_at: string | null;
  payout_units: number | null;
  result_units: number | null;
  result_dollars: number | null;
  clv_pct: number | null;
  beat_close: boolean | null;
  legs: BetLeg[];
};

export type MarketRecord = { won: number; lost: number; push: number };

export type BetProfile = {
  total_bets: number;
  pending: number;
  settled: number;
  won: number;
  lost: number;
  push: number;
  win_rate: number | null;
  staked_units: number;
  profit_units: number;
  roi_pct: number | null;
  open_risk_units: number;
  staked_dollars: number | null;
  profit_dollars: number | null;
  roi_dollars_pct: number | null;
  avg_clv_pct: number | null;
  beat_close_pct: number | null;
  legs_with_clv: number;
  record_by_market: Record<string, MarketRecord>;
  record_by_type: Record<string, MarketRecord>;
  current_streak: number;
};

async function reqVoid(path: string, init?: RequestInit): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    headers: buildHeaders(init?.headers),
    ...init,
  });
  if (!res.ok) throw new Error(await parseApiError(res));
}

export const api = {
  // auth
  authRegister: (email: string, password: string, display_name?: string) =>
    req<AuthTokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name: display_name ?? "" }),
    }),
  authLogin: (email: string, password: string) =>
    req<AuthTokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  authMe: () => req<UserProfile>("/auth/me"),
  authUpdateMe: (body: { display_name?: string }) =>
    req<UserProfile>("/auth/me", { method: "PATCH", body: JSON.stringify(body) }),
  authChangePassword: (current_password: string, new_password: string) =>
    req<{ ok: boolean }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  // meta
  seasons: () => req<SeasonInfo>("/meta/seasons"),
  freshness: (policy?: FetchPolicy) => req<FreshnessSnapshot>("/meta/freshness", undefined, policy),
  experimentAssign: (experimentKey: string, sessionId: string) =>
    req<ExperimentAssignment>(
      `/meta/experiments/assign?experiment_key=${encodeURIComponent(experimentKey)}&session_id=${encodeURIComponent(sessionId)}`,
    ),
  trackExperimentEvents: (events: Array<Record<string, unknown>>) =>
    req<{ inserted: number }>("/meta/experiments/events", {
      method: "POST",
      body: JSON.stringify({ events }),
    }),

  // health
  health: () => req<{ ok: boolean; env: string; llm_provider: string }>("/health"),

  // teams
  listTeams: (policy?: FetchPolicy) => req<Team[]>("/teams", undefined, policy),
  getTeam: (id: string) => req<Team>(`/teams/${id}`),
  getTeamRoster: (id: string) => req<Player[]>(`/teams/${id}/roster`),
  getTeamSchedule: (id: string, season?: number) =>
    req<Game[]>(`/teams/${id}/schedule${season ? `?season=${season}` : ""}`),
  getTeamStats: (id: string, season = 2024) =>
    req<Record<string, unknown>>(`/teams/${id}/stats?season=${season}`),
  getTeamProfile: (id: string, season?: number) =>
    req<TeamProfile>(`/teams/${id}/profile${season ? `?season=${season}` : ""}`),
  getTeamTrend: (id: string, metric: string, start?: number, end?: number) => {
    const qs = new URLSearchParams({ metric });
    if (start) qs.set("start", String(start));
    if (end) qs.set("end", String(end));
    return req<{ team_id: string; metric: string; points: TrendPoint[] }>(
      `/teams/${id}/trend?${qs.toString()}`,
    );
  },
  getTeamNews: (id: string, limit = 25) =>
    req<any[]>(`/teams/${id}/news?limit=${limit}`),
  getTeamUpcoming: (id: string) =>
    req<UpcomingSeason>(`/teams/${id}/upcoming`),

  // players
  listPlayers: (q: { query?: string; team_id?: string; position?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q.query) p.set("q", q.query);
    if (q.team_id) p.set("team_id", q.team_id);
    if (q.position) p.set("position", q.position);
    if (q.limit) p.set("limit", String(q.limit));
    return req<Player[]>(`/players?${p.toString()}`);
  },
  getPlayer: (id: string) => req<Player>(`/players/${id}`),
  getPlayerStats: (id: string, season = 2024) =>
    req<Record<string, unknown>>(`/players/${id}/stats?season=${season}`),
  getPlayerProfile: (id: string, season?: number) =>
    req<PlayerProfile>(`/players/${id}/profile${season ? `?season=${season}` : ""}`),
  getPlayerGamelog: (id: string, season?: number) =>
    req<any[]>(`/players/${id}/gamelog${season ? `?season=${season}` : ""}`),
  getPlayerTrend: (id: string, metric: string, start?: number, end?: number) => {
    const qs = new URLSearchParams({ metric });
    if (start) qs.set("start", String(start));
    if (end) qs.set("end", String(end));
    return req<{ player_id: string; metric: string; points: TrendPoint[] }>(
      `/players/${id}/trend?${qs.toString()}`,
    );
  },
  getPlayerNews: (id: string, limit = 20) =>
    req<NewsItem[]>(`/players/${id}/news?limit=${limit}`),

  // scores
  scoreboard: (limit = 32, policy?: FetchPolicy) =>
    req<Game[]>(`/scores?limit=${limit}`, undefined, policy),

  // stats / comparison
  compareTeams: (teams: string[], season = 2024) =>
    req<Record<string, unknown>>(`/stats/compare/teams?teams=${teams.join(",")}&season=${season}`),
  compareTeamVsLeague: (team: string, season = 2024) =>
    req<Record<string, unknown>>(`/stats/compare/team-vs-league?team=${team}&season=${season}`),
  comparePlayers: (names: string[], season = 2024) =>
    req<Record<string, unknown>>(
      `/stats/compare/players?names=${encodeURIComponent(names.join(","))}&season=${season}`,
    ),

  // news
  news: (limit = 30, source?: string, policy?: FetchPolicy) =>
    req<NewsItem[]>(`/news?limit=${limit}${source ? `&source=${source}` : ""}`, undefined, policy),

  // odds
  oddsStatus: (policy?: FetchPolicy) =>
    req<{
      configured: boolean;
      lines_in_db: number;
      ready: boolean;
      last_updated: string | null;
      // Cron attempt metadata — present once the worker has fired at least once.
      // `status` is one of "ok" | "skipped_fresh" | "skipped_offseason" | "error"
      // | "disabled". In offseason the cron fires every 12h but skips, so
      // `last_attempt.at` advances while `last_updated` does not.
      last_attempt?: {
        at: string | null;
        status: string | null;
        lines_in_db: number | null;
      } | null;
      next_refresh_at?: string | null;
      refresh_hours_utc?: string | null;
      lookahead_days?: number | null;
    }>(
      "/odds/status",
      undefined,
      policy,
    ),
  odds: (market?: string, limit = 100, policy?: FetchPolicy) =>
    req<OddsLine[]>(`/odds?limit=${limit}${market ? `&market=${market}` : ""}`, undefined, policy),

  // fantasy
  enrichRoster: (names_or_ids: string[]) =>
    req<{ rows: any[]; summary: any }>("/fantasy/roster", {
      method: "POST",
      body: JSON.stringify({ names_or_ids }),
    }),
  fantasyNews: (limit = 30) => req<NewsItem[]>(`/fantasy/news?limit=${limit}`),
  fantasyTrending: (kind: "add" | "drop" = "add", limit = 20, policy?: FetchPolicy) =>
    req<{ kind: string; items: any[] }>(
      `/fantasy/trending?kind=${kind}&limit=${limit}`,
      undefined,
      policy,
    ),
  fantasyAdvise: (roster: string[], question?: string) =>
    req<{ session_id: string; content: string; transcript: any[]; widget: any }>(
      "/fantasy/advise",
      {
        method: "POST",
        body: JSON.stringify({ roster, ...(question ? { question } : {}) }),
      },
    ),

  // predictions
  predictGames: (
    season?: number,
    week?: number,
    includeML = true,
    policy?: FetchPolicy,
  ) => {
    const qs = new URLSearchParams();
    if (season) qs.set("season", String(season));
    if (week) qs.set("week", String(week));
    qs.set("include_ml", String(includeML));
    return req<{ season: number; week: number | null; games: GamePrediction[] }>(
      `/predictions/games?${qs.toString()}`,
      undefined,
      policy,
    );
  },
  teamSeasonOutlook: (teamId: string, season?: number) =>
    req<TeamSeasonOutlook>(
      `/predictions/teams/${teamId}/season${season ? `?season=${season}` : ""}`,
    ),
  teamEloHistory: (teamId: string, seasons?: number[]) =>
    req<{ team_id: string; history: EloHistoryPoint[] }>(
      `/predictions/teams/${teamId}/elo-history${seasons?.length ? `?seasons=${seasons.join(",")}` : ""}`,
    ),
  currentElo: (policy?: FetchPolicy) =>
    req<{ ratings: EloRow[] }>(`/predictions/elo/current`, undefined, policy),
  projectedStandings: (season?: number, policy?: FetchPolicy) =>
    req<{ season: number; divisions: ProjectedDivision[] }>(
      `/predictions/standings/projected${season ? `?season=${season}` : ""}`,
      undefined,
      policy,
    ),
  teamRemainingSchedule: (teamId: string, season?: number) =>
    req<TeamRemainingSchedule>(
      `/predictions/teams/${teamId}/remaining-schedule${season ? `?season=${season}` : ""}`,
    ),
  playerGamePredictions: (playerId: string, season?: number) =>
    req<PlayerGamePredictions>(
      `/predictions/players/${playerId}/games${season ? `?season=${season}` : ""}`,
    ),
  playerSeasonProjection: (playerId: string, season?: number) =>
    req<PlayerSeasonProjection>(
      `/predictions/players/${playerId}/season${season ? `?season=${season}` : ""}`,
    ),
  projectionLeaderboard: (q: { season?: number; position?: string; scoring?: string; sort?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q.season) p.set("season", String(q.season));
    if (q.position) p.set("position", q.position);
    if (q.scoring) p.set("scoring", q.scoring);
    if (q.sort) p.set("sort", q.sort);
    if (q.limit) p.set("limit", String(q.limit));
    return req<ProjectionLeaderboard>(`/players/projections/leaderboard?${p.toString()}`);
  },
  playerProps: (playerId: string) =>
    req<PlayerProps>(`/players/${playerId}/props`),
  propEdges: (q?: { min_edge?: number; min_books?: number; limit?: number }) => {
    const p = new URLSearchParams();
    if (q?.min_edge != null) p.set("min_edge", String(q.min_edge));
    if (q?.min_books != null) p.set("min_books", String(q.min_books));
    if (q?.limit != null) p.set("limit", String(q.limit));
    return req<PropEdges>(`/players/props/edges?${p.toString()}`);
  },
  weeklyBoard: (q: { season?: number; week?: number; position?: string; scoring?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q.season) p.set("season", String(q.season));
    if (q.week != null) p.set("week", String(q.week));
    if (q.position) p.set("position", q.position);
    if (q.scoring) p.set("scoring", q.scoring);
    if (q.limit) p.set("limit", String(q.limit));
    return req<WeeklyBoard>(`/players/projections/weekly?${p.toString()}`);
  },
  propBoard: (q?: { market?: string; event_id?: string; position?: string; q?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q?.market) p.set("market", q.market);
    if (q?.event_id) p.set("event_id", q.event_id);
    if (q?.position) p.set("position", q.position);
    if (q?.q) p.set("q", q.q);
    if (q?.limit) p.set("limit", String(q.limit));
    return req<PropBoard>(`/players/props/board?${p.toString()}`);
  },
  comparePlayerProjections: (ids: string[], season?: number) =>
    req<PlayerComparison>(
      `/players/compare/projections?ids=${encodeURIComponent(ids.join(","))}${season ? `&season=${season}` : ""}`,
    ),
  playerUsage: (playerId: string, season?: number) =>
    req<UsageProfile>(`/players/${playerId}/usage${season ? `?season=${season}` : ""}`),
  fantasyRos: (q?: { season?: number; scoring?: string; league_size?: number; position?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q?.season) p.set("season", String(q.season));
    if (q?.scoring) p.set("scoring", q.scoring);
    if (q?.league_size) p.set("league_size", String(q.league_size));
    if (q?.position) p.set("position", q.position);
    if (q?.limit) p.set("limit", String(q.limit));
    return req<RosBoard>(`/fantasy/ros?${p.toString()}`);
  },
  fantasyWaivers: (q?: { season?: number; scoring?: string; limit?: number }) => {
    const p = new URLSearchParams();
    if (q?.season) p.set("season", String(q.season));
    if (q?.scoring) p.set("scoring", q.scoring);
    if (q?.limit) p.set("limit", String(q.limit));
    return req<WaiverBoard>(`/fantasy/waivers?${p.toString()}`);
  },
  fantasyTrade: (side_a: string[], side_b: string[], scoring = "ppr", league_size = 12) =>
    req<TradeResult>("/fantasy/trade", {
      method: "POST",
      body: JSON.stringify({ side_a, side_b, scoring, league_size }),
    }),
  playerOverProb: (playerId: string, stat: string, line: number, season?: number) =>
    req<OverProbResult>(
      `/players/${playerId}/over-prob?stat=${encodeURIComponent(stat)}&line=${line}${season ? `&season=${season}` : ""}`,
    ),
  awards: (season?: number, policy?: FetchPolicy) =>
    req<AwardsResponse>(
      `/predictions/awards${season ? `?season=${season}` : ""}`,
      undefined,
      policy,
    ),

  // betting
  teamBettingHistory: (teamId: string, seasons?: number[]) =>
    req<TeamBettingHistory>(
      `/betting/teams/${teamId}/history${seasons?.length ? `?seasons=${seasons.join(",")}` : ""}`,
    ),
  bettingEdge: (season?: number, week?: number) => {
    const qs = new URLSearchParams();
    if (season) qs.set("season", String(season));
    if (week) qs.set("week", String(week));
    return req<{ season: number; week: number | null; games: EdgeGame[] }>(
      `/betting/edge?${qs.toString()}`,
    );
  },
  teamBettingEdge: (teamId: string, season?: number) => {
    const qs = season ? `?season=${season}` : "";
    return req<TeamEdgeResponse>(`/betting/teams/${teamId}/edge${qs}`);
  },
  bestBets: (season?: number) =>
    req<{ week: number | null; best_bets: EdgeGame[] }>(
      `/betting/best-bets${season ? `?season=${season}` : ""}`,
    ),

  // head-to-head
  h2h: (a: string, b: string, season?: number) =>
    req<H2HMatchup>(`/h2h/${a}/${b}${season ? `?season=${season}` : ""}`),

  // backtest
  backtest: () =>
    req<{ elo: EloBacktest; ml: MLBacktest }>(`/predictions/backtest`),

  // ai
  chat: (body: { message: string; session_id?: string; enable_tools?: boolean }) =>
    req<{ session_id: string; content: string; transcript: any[]; widget: any }>("/ai/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  buildWidget: (prompt: string, save = true) =>
    req<Record<string, unknown>>("/ai/widgets", {
      method: "POST",
      body: JSON.stringify({ prompt, save }),
    }),

  // widgets
  listWidgets: (policy?: FetchPolicy) => req<Widget[]>("/widgets", undefined, policy),
  renderWidget: (id: string) =>
    req<{ widget: Record<string, unknown>; data: Record<string, unknown> }>(
      `/widgets/${id}/render`,
    ),
  renderInline: (spec: Record<string, unknown>) =>
    req<{ widget: Record<string, unknown>; data: Record<string, unknown> }>(`/widgets/render`, {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  deleteWidget: (id: string) => req<{ ok: boolean }>(`/widgets/${id}`, { method: "DELETE" }),

  // sparky — betting prediction & parlay intelligence
  sparkySlate: (date?: string, preferReal: boolean = false, policy?: FetchPolicy) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (preferReal) params.set("prefer_real", "true");
    const qs = params.toString() ? `?${params}` : "";
    return req<SparkySlate>(`/sparky/slate${qs}`, undefined, policy);
  },
  sparkyGame: (eventId: string) =>
    req<SparkyGameDetail>(`/sparky/games/${encodeURIComponent(eventId)}`),
  sparkyParlay: (event_ids: string[], persist = false) =>
    req<SparkyParlayResponse>("/sparky/parlay", {
      method: "POST",
      body: JSON.stringify({ event_ids, persist }),
    }),
  sparkyAccuracy: (asOf?: string, policy?: FetchPolicy) =>
    req<SparkyAccuracy>(`/sparky/accuracy${asOf ? `?as_of=${asOf}` : ""}`, undefined, policy),
  sparkyGlossary: () => req<{ signals: SparkyGlossaryEntry[] }>("/sparky/signals/glossary"),
  sparkyAdminStatus: () => req<SparkyAdminStatus>("/sparky/admin/status"),
  sparkyAdminRefresh: (date?: string) =>
    req<SparkySlate>(`/sparky/admin/refresh${date ? `?date=${date}` : ""}`, { method: "POST" }),
  sparkyAdminBuildReal: () =>
    req<SparkySlate>("/sparky/admin/build_real", { method: "POST" }),
  sparkyAdminBackfill: (days = 30) =>
    req<Record<string, unknown>>(`/sparky/admin/backfill?days=${days}`, { method: "POST" }),
  sparkyAdminSettle: (days = 14) =>
    req<{ ok: boolean; settled_picks: number; settled_parlays: number; skipped: number; lookback_days: number }>(
      `/sparky/admin/settle?days=${days}`,
      { method: "POST" }
    ),
  sparkyAdminBacktest: (start: string, end: string, mode = "replay", hoursCutoff?: number) => {
    const params = new URLSearchParams({ start, end, mode });
    if (hoursCutoff != null) params.set("hours_cutoff", String(hoursCutoff));
    return req<any>(`/sparky/admin/backtest?${params.toString()}`, { method: "POST" });
  },

  // bet tracker + CLV profile
  createBet: (bet: BetInput) =>
    req<Bet>("/bets", { method: "POST", body: JSON.stringify(bet) }),
  listBets: (status?: string) =>
    req<Bet[]>(`/bets${status ? `?status_filter=${encodeURIComponent(status)}` : ""}`),
  settleBets: () =>
    req<{ settled_bets: number; graded_legs: number; pending_scanned: number }>(
      "/bets/settle",
      { method: "POST" },
    ),
  betProfile: () => req<BetProfile>("/bets/profile"),
  deleteBet: (id: string) => reqVoid(`/bets/${id}`, { method: "DELETE" }),

  // admin projection overrides (admin-only routes; 403 for everyone else)
  adminListOverrides: (q?: {
    entity_type?: string;
    entity_id?: string;
    season?: number;
    week?: number;
  }) => {
    const p = new URLSearchParams();
    if (q?.entity_type) p.set("entity_type", q.entity_type);
    if (q?.entity_id) p.set("entity_id", q.entity_id);
    if (q?.season != null) p.set("season", String(q.season));
    if (q?.week != null) p.set("week", String(q.week));
    const qs = p.toString();
    return req<{ overrides: AdminOverride[] }>(`/admin/overrides${qs ? `?${qs}` : ""}`);
  },
  adminUpsertOverride: (body: AdminOverrideInput) =>
    req<AdminOverride>("/admin/overrides", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  adminDeleteOverride: (id: number) =>
    req<{ deleted: number }>(`/admin/overrides/${id}`, { method: "DELETE" }),
  adminTeamModelInputs: (season?: number) =>
    req<TeamModelInputs>(
      `/admin/overrides/model-inputs/teams${season ? `?season=${season}` : ""}`,
    ),
  adminPlayerModelInputs: (playerId: string, season?: number) =>
    req<PlayerModelInputs>(
      `/admin/overrides/model-inputs/players/${playerId}${season ? `?season=${season}` : ""}`,
    ),

  // custom fantasy ranking sets (admin authoring — draft → publish)
  adminListRankingSets: (season?: number) =>
    req<{ formats: string[]; sets: RankingSetMeta[] }>(
      `/admin/rankings${season != null ? `?season=${season}` : ""}`,
    ),
  adminCreateRankingSet: (body: {
    name: string;
    season?: number;
    format?: string;
    description?: string;
  }) =>
    req<RankingSetDetail>("/admin/rankings", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  adminGetRankingSet: (id: number) =>
    req<RankingSetDetail>(`/admin/rankings/${id}`),
  adminUpdateRankingSet: (
    id: number,
    body: { name?: string; format?: string; description?: string },
  ) =>
    req<RankingSetDetail>(`/admin/rankings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  adminDeleteRankingSet: (id: number) =>
    req<{ deleted: number }>(`/admin/rankings/${id}`, { method: "DELETE" }),
  adminReplaceRankingEntries: (
    id: number,
    entries: { player_id: string; tier?: number; note?: string }[],
  ) =>
    req<RankingSetDetail>(`/admin/rankings/${id}/entries`, {
      method: "PUT",
      body: JSON.stringify({ entries }),
    }),
  adminSeedRankingSet: (
    id: number,
    body?: { source?: string; scoring?: string; position?: string; limit?: number },
  ) =>
    req<RankingSetDetail>(`/admin/rankings/${id}/seed`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  adminPublishRankingSet: (id: number) =>
    req<RankingSetMeta>(`/admin/rankings/${id}/publish`, { method: "POST" }),
  adminUnpublishRankingSet: (id: number) =>
    req<RankingSetMeta>(`/admin/rankings/${id}/unpublish`, { method: "POST" }),

  // published ranking boards (public fantasy page)
  fantasyRankingSets: (season?: number) =>
    req<{ sets: RankingSetMeta[] }>(
      `/fantasy/rankings${season != null ? `?season=${season}` : ""}`,
    ),
  fantasyRankings: (setId: number) =>
    req<PublicRankingBoard>(`/fantasy/rankings/${setId}`),

  // global model-parameter registry (admin-only)
  adminListParams: () => req<ParamRegistry>("/admin/params"),
  adminSetParam: (key: string, value: number, note = "") =>
    req<{ key: string; value: number; is_overridden: boolean }>(
      `/admin/params/values/${key}`,
      { method: "PUT", body: JSON.stringify({ value, note }) },
    ),
  adminBulkSetParams: (changes: Record<string, number>, note = "") =>
    req<{ applied: Record<string, number> }>("/admin/params/bulk", {
      method: "POST",
      body: JSON.stringify({ changes, note }),
    }),
  adminRevertParam: (key: string) =>
    req<{ key: string; value: number; is_overridden: boolean }>(
      `/admin/params/values/${key}`,
      { method: "DELETE" },
    ),
  adminRevertAllParams: (note = "") =>
    req<{ reverted: string[] }>("/admin/params/revert-all", {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  adminTuningStatus: (season?: number) =>
    req<TuningStatus>(
      `/admin/params/status${season != null ? `?season=${season}` : ""}`,
    ),
  adminExportSnapshot: (season?: number) =>
    req<ConfigSnapshot>(
      `/admin/params/snapshot${season != null ? `?season=${season}` : ""}`,
    ),
  adminImportSnapshot: (
    snapshot: ConfigSnapshot | Record<string, unknown>,
    opts?: {
      note?: string;
      include_params?: boolean;
      include_overrides?: boolean;
      replace_params?: boolean;
    },
  ) =>
    req<ConfigImportResult>("/admin/params/import", {
      method: "POST",
      body: JSON.stringify({
        snapshot,
        note: opts?.note ?? "",
        include_params: opts?.include_params ?? true,
        include_overrides: opts?.include_overrides ?? true,
        replace_params: opts?.replace_params ?? false,
      }),
    }),
  adminListPresets: () => req<{ presets: ParamPreset[] }>("/admin/params/presets"),
  adminSavePreset: (name: string, description = "", params?: Record<string, number>) =>
    req<ParamPreset>("/admin/params/presets", {
      method: "POST",
      body: JSON.stringify({ name, description, params }),
    }),
  adminApplyPreset: (name: string, note = "") =>
    req<{ applied: string; changed: Record<string, number> }>(
      `/admin/params/presets/${encodeURIComponent(name)}/apply`,
      { method: "POST", body: JSON.stringify({ note }) },
    ),
  adminDeletePreset: (name: string) =>
    req<{ deleted: string }>(`/admin/params/presets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  adminParamAudit: (q?: {
    target_type?: string;
    action?: string;
    actor?: string;
    search?: string;
    limit?: number;
    before_id?: number;
  }) => {
    const p = new URLSearchParams();
    if (q?.target_type) p.set("target_type", q.target_type);
    if (q?.action) p.set("action", q.action);
    if (q?.actor) p.set("actor", q.actor);
    if (q?.search) p.set("search", q.search);
    if (q?.limit != null) p.set("limit", String(q.limit));
    if (q?.before_id != null) p.set("before_id", String(q.before_id));
    const qs = p.toString();
    return req<ParamAuditPage>(`/admin/params/audit${qs ? `?${qs}` : ""}`);
  },
  adminPreviewParams: (changes: Record<string, number>, opts?: {
    season?: number; week?: number; scoring?: string; player_limit?: number;
  }) =>
    req<ParamPreview>("/admin/params/preview", {
      method: "POST",
      body: JSON.stringify({ changes, ...opts }),
    }),
  adminProjectionsBoard: (q?: { season?: number; week?: number; scoring?: string }) => {
    const p = new URLSearchParams();
    if (q?.season != null) p.set("season", String(q.season));
    if (q?.week != null) p.set("week", String(q.week));
    if (q?.scoring) p.set("scoring", q.scoring);
    const qs = p.toString();
    return req<ProjectionsBoard>(`/admin/overrides/projections-board${qs ? `?${qs}` : ""}`);
  },
};

export type BoardWeekCell = {
  week?: number | null;
  bye?: boolean;
  opponent?: string | null;
  is_home?: boolean | null;
  tier?: string | null;
  pos_rank?: number | null;
  matchup_grade?: string | null;
  defense_factor?: number | null;
  injury_multiplier?: number | null;
  fantasy?: number | null;
  fantasy_p10?: number | null;
  fantasy_p90?: number | null;
  stats?: Record<string, number | null>;
  game_env?: { game_script?: string | null; predicted_total?: number | null } | null;
  market?: { adp?: number | null; adp_pos_rank?: number | null; trending_adds?: number | null } | null;
};

export type BoardSeasonCell = {
  rank?: number | null;
  pos_rank?: number | null;
  fantasy?: number | null;
  fantasy_per_game?: number | null;
  fantasy_p10?: number | null;
  fantasy_p90?: number | null;
  availability?: number | null;
  games_remaining?: number | null;
  role_multiplier?: number | null;
  stats?: Record<string, number | null>;
};

export type ProjectionsBoardRow = {
  player_id: string;
  name: string | null;
  position: string | null;
  team: string | null;
  injury_status: string | null;
  rookie: boolean;
  week: BoardWeekCell;
  season: BoardSeasonCell;
  inputs: {
    baselines: Record<string, number | null>;
    levers: Record<string, number>;
  };
  overrides: AdminOverride[];
  override_count: number;
};

export type ProjectionsBoard = {
  season: number;
  week: number | null;
  scoring: string;
  baseline_season: number;
  count: number;
  lever_fields: string[];
  players: ProjectionsBoardRow[];
};

// ---- custom fantasy ranking sets ------------------------------------------

export type RankingSetMeta = {
  id: number;
  name: string;
  season: number;
  format: string;
  description: string;
  status: "draft" | "published";
  version: number;
  published_at: string | null;
  created_by?: string;
  created_at?: string | null;
  updated_at?: string | null;
  entry_count?: number;
  has_unpublished_changes?: boolean;
};

export type RankingEntryRow = {
  player_id: string;
  rank: number;
  tier: number;
  note: string;
  name?: string | null;
  position?: string | null;
  team?: string | null;
  injury_status?: string | null;
  model_rank?: number | null;
  vs_model?: number | null;
  model_points?: number | null;
};

export type RankingSetDetail = RankingSetMeta & {
  entries: RankingEntryRow[];
};

export type PublicRankingBoard = {
  id: number;
  name: string;
  season: number;
  format: string;
  description: string;
  version: number;
  published_at: string | null;
  scoring_for_comparison?: string;
  count: number;
  players: RankingEntryRow[];
};

export type ModelParamEntry = {
  key: string;
  label: string;
  description: string;
  default: number;
  min: number;
  max: number;
  step: number;
  kind: "float" | "int";
  unit: string;
  category: string;
  affects: string[];
  value: number;
  is_overridden: boolean;
  note: string;
  updated_by: string;
  updated_at: string | null;
};

export type ParamCategory = {
  id: string;
  label: string;
  description: string;
  params: ModelParamEntry[];
};

export type ParamRegistry = {
  categories: ParamCategory[];
  overridden_count: number;
  total_count: number;
};

export type ParamPreset = {
  id: number;
  name: string;
  description: string;
  params: Record<string, number>;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
};

export type ParamAuditEntry = {
  id: number;
  actor: string;
  action: string;
  target_type: "param" | "override" | "preset";
  target_key: string;
  old_value: number | null;
  new_value: number | null;
  note: string;
  context: Record<string, unknown>;
  created_at: string | null;
};

export type ParamAuditPage = {
  entries: ParamAuditEntry[];
  has_more: boolean;
  next_before_id: number | null;
};

export type ParamPreviewGame = {
  game_id: string;
  home_team: string;
  away_team: string;
  before: { spread: number | null; total: number | null; home_win_prob: number | null };
  after: { spread: number | null; total: number | null; home_win_prob: number | null };
  delta: { spread: number | null; total: number | null; home_win_prob: number | null };
};

export type ParamPreviewPlayer = {
  player_id: string;
  name: string | null;
  position: string | null;
  team: string | null;
  before: number;
  after: number;
  delta: number;
};

export type ParamPreview = {
  season: number;
  week: number | null;
  changes: Record<string, number>;
  summary: {
    games_evaluated: number;
    games_moved: number;
    max_spread_delta: number;
    max_total_delta: number;
    players_moved: number;
    max_player_delta: number;
  };
  games: ParamPreviewGame[];
  players: ParamPreviewPlayer[];
  notes: string[];
};

export type ConfigSnapshot = {
  snapshot_version: number;
  exported_at: string;
  season_filter: number | null;
  params: Record<string, number>;
  params_detail?: Record<
    string,
    { value: number; default: number; label: string; category: string }
  >;
  team_input_levers: AdminOverride[];
  player_input_levers: AdminOverride[];
  game_output_overrides: AdminOverride[];
  player_output_overrides: AdminOverride[];
  counts: {
    params: number;
    team_input_levers: number;
    player_input_levers: number;
    game_output_overrides: number;
    player_output_overrides: number;
    total_overrides: number;
  };
};

export type ConfigImportResult = {
  params_applied: Record<string, number>;
  params_reverted: string[];
  overrides_upserted: number;
  errors: string[];
};

export type TuningStatus = {
  season_filter: number | null;
  version_token: string;
  counts: ConfigSnapshot["counts"];
  params_by_category: {
    id: string;
    label: string;
    params: {
      key: string;
      label: string;
      value: number;
      default: number;
      delta: number;
    }[];
  }[];
  recent_overrides: AdminOverride[];
  team_input_levers: AdminOverride[];
  player_input_levers: AdminOverride[];
  registry_total: number;
  categories: { id: string; label: string; description: string }[];
};

export type AdminOverride = {
  id: number;
  entity_type: "game" | "player" | "team";
  entity_id: string;
  season: number | null;
  week: number | null;
  field: string;
  value: number;
  original_value: number | null;
  note: string;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
};

export type AdminOverrideInput = {
  entity_type: "game" | "player" | "team";
  entity_id: string;
  field: string;
  value: number;
  season?: number | null;
  week?: number | null;
  original_value?: number | null;
  note?: string;
};

// ---- Model-input levers (admin) ---------------------------------------------

export type TeamModelInputs = {
  season: number;
  baseline_season: number;
  fields: Record<string, string>;
  teams: {
    team_id: string;
    baselines: Record<string, number | null>;
    overrides: Record<string, number>;
  }[];
};

export type PlayerModelInputs = {
  player_id: string;
  name: string;
  position: string | null;
  team: string | null;
  season: number;
  baseline_season: number;
  fields: Record<string, string>;
  baselines: Record<string, number | null>;
  overrides: Record<string, number>;
};

export const NFL_BASE = BASE;
