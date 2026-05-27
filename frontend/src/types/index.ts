// ── Auth ──────────────────────────────────────────────────────────────────
export interface User {
  email: string
  name: string
  role: 'admin' | 'user' | 'viewer'
  hil_disclosure_accepted?: boolean
  phone?: string
  onboarding_completed?: boolean
  legal_accepted?: boolean
  sms_verified?: boolean
}

export interface AuthFeatures {
  hil_enabled: boolean
  live_trading_enabled: boolean
  admin_enabled: boolean
  [key: string]: boolean
}

export interface TwoFAStatus {
  method: string | null
  enabled: boolean
  verified: boolean
}

// ── Paper Trading ─────────────────────────────────────────────────────────
export interface PaperPosition {
  ticker: string
  shares: number
  avg_price: number
  entry_price?: number
  entry_time?: string
  stop?: number
  target?: number
}

export interface PaperSummary {
  strategy: string
  strategy_label: string
  starting_cash: number
  cash: number
  total_value: number
  realized_pnl: number
  open_positions: PaperPosition[]
  trades_closed: number
  candidates: number
  not_started?: boolean
}

export interface PaperAccount {
  strategy: string
  label: string
  summary: PaperSummary | null
  state: Record<string, unknown> | null
  candidates: { count: number; rows: CandidateRow[] }
  events: unknown[]
}

export interface CandidateRow {
  ticker: string
  entry: string
  target: string
  stop: string
  atr: string
  score: string
  ml_probability: string
  large_loss_probability: string | null
  expected_return: string
  target_before_stop_probability: string | null
  gate_status: string
  decision_reason?: string
  ai_reason?: string
  account?: string
  _strategy?: string
  _stratLabel?: string
}

export interface PaperStatus {
  process: {
    running: boolean
    pid?: number
    started_at?: string
    command?: string
    log_path?: string
  }
  date: string
  data_dir: string
  accounts: PaperAccount[]
  openrouter: { set: boolean; masked: string }
  log_lines: string[]
  end_of_day?: Record<string, unknown> | null
}

export interface PaperEquityPoint {
  t: string   // ISO timestamp from equity_curve.jsonl
  v: number
  strategy: string
}

export interface PaperAnalytics {
  win_rate: number
  avg_win: number
  avg_loss: number
  total_trades: number
  expectancy: number
  by_strategy: Record<string, unknown>
}

// ── Market ────────────────────────────────────────────────────────────────
export interface MarketChart {
  symbol: string
  interval: string
  dates: string[]
  open: number[]
  high: number[]
  low: number[]
  close: number[]
  volume: number[]
}

export interface Quote {
  ticker: string
  price: number
  change: number
  change_pct: number
  volume?: number
}

// ── Portfolio ─────────────────────────────────────────────────────────────
export interface PortfolioPosition {
  ticker: string
  shares: number
  market_value: number
  cost_basis: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  sector?: string
}

export interface Portfolio {
  total_value: number
  cash: number
  positions: PortfolioPosition[]
  sector_exposure: Record<string, number>
  decisions: Record<string, number>
}

// ── ML ────────────────────────────────────────────────────────────────────
export interface MLMetric {
  name: string
  value: number
  label?: string
}

export interface MLStatus {
  // new API shape
  bundle_exists: boolean
  report_exists: boolean
  created_at: string | null
  days_old: number | null
  up_to_date: boolean
  status_label: string
  settings?: {
    ml_probability_threshold: number
    feature_count: number
    train_rows: number
    test_rows: number
    test_period: number | string
    hold: number
    rows_used: number
    [key: string]: unknown
  }
  metrics?: {
    win_probability?: { roc_auc: number; [key: string]: unknown }
    [key: string]: unknown
  }
  feature_importance: Array<{ feature: string; importance: number; abs_importance?: number }>
  feature_names?: string[]
  thresholds?: Record<string, unknown>
}

// ── Backtest ──────────────────────────────────────────────────────────────
export interface BacktestResult {
  id: string
  date: string
  strategy: string
  trades: number
  win_rate: number
  total_return: number
  sharpe: number | null
}

// ── Settings ──────────────────────────────────────────────────────────────
export interface AppSettings {
  starting_cash: number
  risk_per_trade_pct: number
  max_open_positions: number
  scan_interval_minutes: number
  openrouter_api_key?: string
  webhook_url?: string
  sms_number?: string
  theme?: 'light' | 'dark'
  [key: string]: unknown
}

// ── HIL ───────────────────────────────────────────────────────────────────
export interface HilPending {
  id: string
  ticker: string
  action: 'BUY' | 'SELL'
  shares: number
  price: number
  reason: string
  expires_at: string
  strategy: string
}

// ── Admin ─────────────────────────────────────────────────────────────────
export interface AdminFlags {
  [key: string]: boolean | string | number
}

export interface RuntimeDiagnostics {
  runtime?: {
    generated_at?: string
    root?: string
    python?: string
    platform?: string
    port?: number
    web_pids?: number[]
    cloudflared_pids?: number[]
    commands?: unknown[]
    logs?: Record<string, unknown>
  }
  git?: Record<string, unknown>
  env?: Record<string, unknown>
  log_tail?: string
  // legacy fields (from psutil if installed)
  uptime?: number
  memory_mb?: number
  cpu_pct?: number
}
