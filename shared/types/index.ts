// Shared types for web frontend and desktop control panel.
// Keep in sync with backend response shapes in web/api/system.py, web/api/logs.py, etc.

// ── Auth ──────────────────────────────────────────────────────────────────
export interface User {
  email: string
  name: string
  role: 'admin' | 'user' | 'viewer'
  is_admin?: boolean
}

// ── System Metrics — GET /api/system/metrics ──────────────────────────────
export interface SystemMetrics {
  uptime_seconds: number
  cpu_percent: number
  memory: {
    total_mb: number
    used_mb: number
    percent: number
  }
  disk: {
    total_gb: number
    used_gb: number
    free_gb: number
    percent: number
  }
  process: {
    pid: number
    threads: number
    memory_mb: number
  }
}

export interface SystemMetricsResponse {
  ok: boolean
  timestamp: string
  data: SystemMetrics
}

// ── System Services — GET /api/system/services ────────────────────────────
export interface ServiceStatus {
  running: boolean
  pid?: number
  strategy?: string
  age_hours?: number
  ok?: boolean
  wf_roc?: number
  error?: string
}

export interface SystemServices {
  paper_runner: ServiceStatus
  cloudflare_tunnel: ServiceStatus
  autofix_monitor: ServiceStatus
  ml_model: ServiceStatus & { age_hours?: number; wf_roc?: number }
  retrain_running?: ServiceStatus
}

export interface SystemServicesResponse {
  ok: boolean
  timestamp: string
  data: SystemServices
}

// ── Health — GET /api/health/deep ─────────────────────────────────────────
export interface DeepHealth {
  status: string
  timestamp: string
  version?: string
  paper_trader?: { running: boolean; pid?: number }
  cloudflare_tunnel?: { running: boolean }
  autofix_monitor?: { running: boolean }
  ml_model?: { ok: boolean; age_hours?: number; wf_roc?: number }
}

// ── Logs — GET /api/logs/system ───────────────────────────────────────────
export type LogSource = 'web' | 'cloudflared' | 'autofix' | 'paper' | 'retrain'

export interface LogsSystemResponse {
  ok: boolean
  source: LogSource
  lines: number
  entries: string[]
}

export interface LogsSourcesResponse {
  ok: boolean
  sources: Array<{ name: string; path: string; size_bytes: number; exists: boolean }>
}

// ── Paper Trading — GET /api/paper/status ─────────────────────────────────
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
}

// ── ML — GET /api/ml/status ───────────────────────────────────────────────
export interface MLStatus {
  bundle_exists: boolean
  report_exists: boolean
  created_at: string | null
  days_old: number | null
  up_to_date: boolean
  status_label: string
  metrics?: {
    win_probability?: { roc_auc: number }
  }
}

// ── Settings — GET /api/settings ─────────────────────────────────────────
export interface AppSettings {
  starting_cash?: number
  risk_per_trade_pct?: number
  max_open_positions?: number
  scan_interval_minutes?: number
  [key: string]: unknown
}

// ── Admin — GET /api/admin/audit ──────────────────────────────────────────
export interface AuditEvent {
  ts: number
  event: string
  actor: string
  target: string
  detail: string
  meta: Record<string, unknown>
}

// ── Retrain — POST /api/ml/retrain ────────────────────────────────────────
export interface RetrainRequest {
  tickers: string
}

export interface RetrainResponse {
  ok: boolean
  message: string
  pid?: number
  log?: string
}

// ── Standard API envelope ─────────────────────────────────────────────────
export interface ApiOk<T = unknown> {
  ok: true
  data: T
  timestamp?: string
}

export interface ApiError {
  ok: false
  error: string
  code: number
  detail?: unknown[]
}

export type ApiResult<T> = ApiOk<T> | ApiError
