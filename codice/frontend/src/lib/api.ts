import Cookies from 'js-cookie';

const BASE = '/api';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = Cookies.get('token');
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (res.status === 401) {
    // Auth disabilitata — ignora 401
    throw new ApiError(401, 'Unauthorized');
  }

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail ?? res.statusText);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  // Auth
  login: (email: string, password: string) => {
    const form = new URLSearchParams({ username: email, password });
    return fetch(`${BASE}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    }).then(r => r.json());
  },

  // Matches
  getMatches: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<Match[]>(`/matches${qs}`);
  },
  getMatchOpportunities: (matchId: string) =>
    request<Opportunity[]>(`/opportunities?match_id=${matchId}&status=pending&limit=20`),
  getMatch: (id: string) => request<MatchDetail>(`/matches/${id}`),
  analyseMatch: (id: string) => request<{ opportunities_found: number }>(`/matches/${id}/analyse`, { method: 'POST' }),

  // Opportunities
  getOpportunities: (status = 'pending') => request<Opportunity[]>(`/opportunities?status=${status}`),
  approveOpportunity: (id: string, stake?: number) =>
    request<{ bet_id: string; stake: number }>(`/opportunities/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ stake_override: stake ?? null }),
    }),
  rejectOpportunity: (id: string, reason: string) =>
    request(`/opportunities/${id}/reject?reason=${encodeURIComponent(reason)}`, { method: 'POST' }),

  // Bets
  getBets: (status?: string) => request<Bet[]>(`/bets${status ? `?status=${status}` : ''}`),
  getBetStats: () => request<BetStats>('/bets/stats'),

  // Competitions
  getCompetitions: () => request<Competition[]>('/competitions'),

  // Settings
  fetchOddsNow: () =>
    request('/settings/fetch-odds-now', { method: 'POST' }),

  // Intelligence
  getMatchIntelligence: (matchId: string) => request<MatchIntelligence>(`/intelligence/match/${matchId}`),
  getMarketIntelligence: (matchId: string) => request<MarketIntelligence>(`/intelligence/market/${matchId}`),
  getNews: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<NewsItem[]>(`/intelligence/news${qs}`);
  },

  // Analytics
  getPerformance: (days = 30) => request<Performance>(`/analytics/performance?days=${days}`),
  getCLV: (days = 30) => request<CLVSummary>(`/analytics/clv?days=${days}`),

  // Scalate
  getScalate: () => request<Scalata[]>('/scalate'),
  getScalata: (id: string) => request<Scalata>(`/scalate/${id}`),
  confermaStep: (id: string, startAmount: number) =>
    request<Scalata>(`/scalate/${id}/conferma`, {
      method: 'POST',
      body: JSON.stringify({ start_amount: startAmount }),
    }),
  registraRisultato: (id: string, stepNumber: number, won: boolean) =>
    request(`/scalate/${id}/step/${stepNumber}/risultato?won=${won}`, { method: 'POST' }),
  getScalataStats: () => request<ScalataStats>('/scalate/stats/riepilogo'),
};

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Match {
  id: string;
  competition_id: string;
  home_team: string | null;
  away_team: string | null;
  player_a: string | null;
  player_b: string | null;
  match_date: string;
  sport: string;
  status: string;
  display_name: string;
  has_value_bet: boolean;
}

export interface MatchDetail extends Match {
  competition: Competition;
  best_odds: Odds[];
}

export interface Odds {
  bookmaker: string;
  market: string;
  outcome: string;
  odds: number;
  fetched_at: string;
  is_live: boolean;
}

export interface Opportunity {
  id: string;
  match_id: string;
  market: string;
  outcome: string;
  bookmaker: string;
  best_odds: number;
  model_probability: number;
  expected_value: number;
  uncertainty_score: number;
  tier: 'S' | 'A' | 'B' | 'C';
  edge: number | null;
  bet_type: 'singola' | 'scalata' | 'doppia' | 'multipla';
  confidence_level: 'alta' | 'normale' | 'bassa';
  scalata_id: string | null;
  scalata_step: number | null;
  composite_bet_id: string | null;
  status: string;
  rejection_reason: string | null;
  uncertainty_blocked: boolean;
  created_at: string;
}

export interface Bet {
  id: string;
  opportunity_id: string;
  bookmaker: string;
  market: string;
  outcome: string;
  odds: number;
  stake: number;
  status: string;
  result: string | null;
  pnl: number | null;
  placed_at: string;
  settled_at: string | null;
}

export interface BetStats {
  total_bets: number;
  open_bets: number;
  won: number;
  lost: number;
  total_staked: number;
  total_pnl: number;
  roi_pct: number;
  win_rate: number;
}

export interface ScalataStep {
  id: string;
  step_number: number;
  status: string;
  odds: number;
  stake: number;
  match_name: string;
  market: string;
  outcome: string;
  bookmaker: string;
  match_date: string | null;
  placed_at: string | null;
  settled_at: string | null;
  opportunity_id: string | null;
  bet_id: string | null;
}

export interface Scalata {
  id: string;
  status: string;
  total_steps: number;
  current_step: number;
  start_amount: number;
  current_amount: number;
  potential_win: number | null;
  created_at: string;
  completed_at: string | null;
  total_pnl: number | null;
  notes: string | null;
  steps: ScalataStep[];
}

export interface ScalataStats {
  totale: number;
  vinte: number;
  perse: number;
  attive: number;
  success_rate: number;
  profitto_totale: number;
  profitto_medio: number;
}

export interface PipelineRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  matches_processed: number;
  opportunities_found: number;
  bets_placed: number;
  error: string | null;
}

export interface Competition {
  id: string;
  name: string;
  sport: string;
  tier: string;
  weight: number;
}

export interface MatchIntelligence {
  match_id: string;
  display_name: string;
  match_date: string;
  lineups: Record<string, string[]> | null;
  injuries: Record<string, object[]> | null;
  weather: Record<string, unknown> | null;
  home_form: object[] | null;
  away_form: object[] | null;
  h2h: Record<string, unknown> | null;
  market_signals: Record<string, unknown> | null;
  momentum_score: number | null;
  opportunities_count: number;
  best_tier: string | null;
}

export interface MarketIntelligence {
  match_id: string;
  display_name: string;
  odds_history: object[];
  market_signals: Record<string, unknown> | null;
  best_available: object[];
}

export interface NewsItem {
  id: string;
  title: string;
  source: string | null;
  sentiment: number | null;
  relevance: number | null;
  published_at: string | null;
  team: string | null;
  match_id: string | null;
}

export interface TierPerformance {
  tier: string;
  total_bets: number;
  won: number;
  win_rate: number;
  total_staked: number;
  total_pnl: number;
  roi_pct: number;
  avg_ev: number;
}

export interface Performance {
  period_days: number;
  total_bets: number;
  won: number;
  lost: number;
  win_rate: number;
  total_staked: number;
  total_pnl: number;
  roi_pct: number;
  avg_odds: number;
  no_bet_days: number;
  by_tier: TierPerformance[];
  by_bet_type: Record<string, { total: number; won: number; win_rate: number; pnl: number }>;
}

export interface CLVRecord {
  bet_id: string;
  match_name: string | null;
  placed_odds: number;
  closing_odds: number | null;
  clv: number | null;
  ev_at_placement: number;
  tier: string | null;
  placed_at: string;
}

export interface CLVSummary {
  avg_clv: number | null;
  positive_clv_pct: number;
  total_bets_with_clv: number;
  records: CLVRecord[];
}
