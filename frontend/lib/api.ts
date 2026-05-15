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
  };
  ml_prediction?: {
    predicted_spread: number;
    predicted_home_margin: number;
  };
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
