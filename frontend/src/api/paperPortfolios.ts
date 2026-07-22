import api from './client'

// ── Types (mirror web/api/paper_portfolios.py) ───────────────────────────────

export interface PortfolioCard {
  portfolio_id: string
  name: string
  source_strategy: string
  badge: string
  ml_threshold: number | null
  current_equity: number
  initial_cash: number
  cash: number
  settled_cash: number
  unsettled_cash: number
  realized_pnl: number
  unrealized_pnl: number
  all_time_ror: number
  daily_ror: number
  weekly_ror: number
  monthly_ror: number
  max_drawdown: number
  sharpe_ratio: number
  profit_factor: number
  win_rate: number
  total_trades: number
  open_positions: number
  avg_hold_days: number
  day_trades_last_5: number
  compliance_skips: number
}

export interface LeaderboardEntry {
  rank: number
  portfolio_id: string
  name: string
  badge: string
  all_time_ror: number
  current_equity: number
  sharpe_ratio: number
  max_drawdown: number
  profit_factor: number
  win_rate: number
  open_positions: number
  total_trades: number
}

export interface RorSeries {
  portfolio_id: string
  name: string
  badge: string
  points: { t: string; ror: number }[]
}

export interface PortfolioPosition {
  ticker: string
  shares: number
  entry_price: number
  current_price: number
  stop: number
  target: number
  trailing_stop: number | null
  unrealized_pnl: number
  unrealized_pct: number
  entry_date: string
  days_held: number
  source_strategy: string
  used_ml: boolean
  used_unified_brain: boolean
  used_ai: boolean
  ml_probability: number | null
  entry_reason: string
}

export interface PortfolioTrade {
  ticker: string
  shares: number
  entry_price: number
  exit_price: number | null
  entry_date: string
  exit_date: string | null
  realized_pnl: number
  realized_pct: number
  exit_reason: string | null
  source_strategy: string
  ml_probability: number | null
}

export interface ComplianceEvent {
  timestamp: string
  portfolio_id?: string
  portfolio_name?: string
  ticker: string
  action: string
  reason: string
  details: Record<string, unknown>
}

export interface PortfolioDetail {
  portfolio_id: string
  name: string
  config: Record<string, unknown>
  card: PortfolioCard
  positions: PortfolioPosition[]
  trades: PortfolioTrade[]
  equity_curve: { t: string; equity: number; ror: number }[]
  compliance_log: ComplianceEvent[]
}

// ── Fetchers ─────────────────────────────────────────────────────────────────

export const getPortfolioAccounts = () =>
  api.get<{ accounts: PortfolioCard[] }>('/paper/accounts').then(r => r.data.accounts)

export const getPortfolioLeaderboard = (sortBy = 'all_time_ror') =>
  api.get<{ sort_by: string; entries: LeaderboardEntry[] }>(`/paper/leaderboard?sort_by=${sortBy}`).then(r => r.data)

export const getPortfolioRorChart = () =>
  api.get<{ series: RorSeries[] }>('/paper/ror-chart').then(r => r.data.series)

export const getPortfolioDetail = (id: string) =>
  api.get<PortfolioDetail>(`/paper/accounts/${id}`).then(r => r.data)

export const getComplianceLog = (portfolioId?: string, limit = 200) =>
  api.get<{ events: ComplianceEvent[]; total: number }>(
    `/paper/compliance-log?limit=${limit}${portfolioId ? `&portfolio_id=${portfolioId}` : ''}`,
  ).then(r => r.data)

export const resetAllPortfolios = () => api.post('/paper/reset').then(r => r.data)
export const resetOnePortfolio = (id: string) => api.post(`/paper/reset/${id}`).then(r => r.data)
