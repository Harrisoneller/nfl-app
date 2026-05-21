// Typed API client — every call goes through here.

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

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

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
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
    predicted_spread: number;
    predicted_total: number;
    predicted_home_score: number;
    predicted_away_score: number;
    game_script?: string;
    inputs?: PredictionInputs;
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

export type MatchupRow = {
  metric: string;
  label: string;
  off_value: number;
  def_value: number;
  expected: number;
  delta: number;
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

export type PlayerGamePrediction = {
  week: number | null;
  gameday: string;
  home: string;
  away: string;
  opponent: string;
  is_home: boolean;
  opponent_def_z: number;
  matchup_grade: "A" | "B" | "C" | "D" | "F";
  predicted: Record<string, { predicted: number; low: number; high: number; n_games_baseline: number }>;
};

export type PlayerGamePredictions = {
  player_id: string;
  name: string;
  position: string;
  team: string;
  season: number;
  baseline_window: number;
  games: PlayerGamePrediction[];
  error?: string;
};

export type PlayerSeasonProjection = {
  player_id: string;
  name: string;
  position: string;
  team: string | null;
  season: number;
  games_played: number;
  games_remaining: number;
  baseline_source_season: number;
  stats: Record<string, {
    ytd: number;
    per_game_pace: number;
    projected_remaining: number;
    projected_final: number;
    low_final: number;
    high_final: number;
  }>;
  error?: string;
};

export type SeasonInfo = {
  available: number[];
  default: number;
  current_or_upcoming: number;
  info: Record<number, { season: number; is_upcoming: boolean; is_latest_completed: boolean }>;
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

export const api = {
  // meta
  seasons: () => req<SeasonInfo>("/meta/seasons"),

  // health
  health: () => req<{ ok: boolean; env: string; llm_provider: string }>("/health"),

  // teams
  listTeams: () => req<Team[]>("/teams"),
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
  scoreboard: (limit = 32) => req<Game[]>(`/scores?limit=${limit}`),

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
  news: (limit = 30, source?: string) =>
    req<NewsItem[]>(`/news?limit=${limit}${source ? `&source=${source}` : ""}`),

  // odds
  oddsStatus: () =>
    req<{ configured: boolean; lines_in_db: number; ready: boolean }>("/odds/status"),
  odds: (market?: string, limit = 100) =>
    req<OddsLine[]>(`/odds?limit=${limit}${market ? `&market=${market}` : ""}`),

  // fantasy
  enrichRoster: (names_or_ids: string[]) =>
    req<{ rows: any[]; summary: any }>("/fantasy/roster", {
      method: "POST",
      body: JSON.stringify({ names_or_ids }),
    }),
  fantasyNews: (limit = 30) => req<NewsItem[]>(`/fantasy/news?limit=${limit}`),
  fantasyTrending: (kind: "add" | "drop" = "add", limit = 20) =>
    req<{ kind: string; items: any[] }>(`/fantasy/trending?kind=${kind}&limit=${limit}`),
  fantasyAdvise: (roster: string[], question?: string) =>
    req<{ session_id: string; content: string; transcript: any[]; widget: any }>(
      "/fantasy/advise",
      {
        method: "POST",
        body: JSON.stringify({ roster, ...(question ? { question } : {}) }),
      },
    ),

  // predictions
  predictGames: (season?: number, week?: number, includeML = true) => {
    const qs = new URLSearchParams();
    if (season) qs.set("season", String(season));
    if (week) qs.set("week", String(week));
    qs.set("include_ml", String(includeML));
    return req<{ season: number; week: number | null; games: GamePrediction[] }>(
      `/predictions/games?${qs.toString()}`,
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
  currentElo: () => req<{ ratings: EloRow[] }>(`/predictions/elo/current`),
  projectedStandings: (season?: number) =>
    req<{ season: number; divisions: ProjectedDivision[] }>(
      `/predictions/standings/projected${season ? `?season=${season}` : ""}`,
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
  awards: (season?: number) =>
    req<AwardsResponse>(`/predictions/awards${season ? `?season=${season}` : ""}`),

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
  listWidgets: () => req<Widget[]>("/widgets"),
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
};

export const NFL_BASE = BASE;
