/**
 * Centralized management API queries.
 * All requests go through getClient() which reads apiBaseUrl + token from Tauri store.
 * Missing endpoints are documented inline with MISSING_ENDPOINT markers.
 */
import { getClient } from './client'

const c = () => getClient()

// ── Auth ──────────────────────────────────────────────────────────────────
export interface AuthMe {
  email: string
  name: string
  role: string
  is_admin?: boolean
}
export const fetchMe = () => c().get<AuthMe>('/api/auth/me').then(r => r.data)
export const fetchAuthFeatures = () =>
  c().get<Record<string, boolean>>('/api/auth/features').then(r => r.data)

// ── Health ─────────────────────────────────────────────────────────────────
export interface DeepHealth {
  status: string
  timestamp: string
  version?: string
  paper_trader?: { running: boolean; pid?: number }
  cloudflare_tunnel?: { running: boolean }
  autofix_monitor?: { running: boolean }
  ml_model?: { ok: boolean; age_hours?: number; wf_roc?: number }
}
export const fetchHealth = () =>
  c().get<DeepHealth>('/api/health/deep').then(r => r.data)

// ── System metrics ─────────────────────────────────────────────────────────
export interface SystemMetrics {
  uptime_seconds: number
  cpu_percent: number
  memory: { total_mb: number; used_mb: number; percent: number }
  disk: { total_gb: number; used_gb: number; free_gb: number; percent: number }
  process: { pid: number; threads: number; memory_mb: number }
}
export const fetchSystemMetrics = () =>
  c().get<{ ok: boolean; data: SystemMetrics }>('/api/system/metrics').then(r => r.data.data)

// ── Services ───────────────────────────────────────────────────────────────
export interface ServiceInfo {
  running: boolean
  pid?: number
  strategy?: string
  ok?: boolean
  age_hours?: number
  wf_roc?: number
  error?: string
}
export interface SystemServices {
  paper_runner: ServiceInfo
  cloudflare_tunnel: ServiceInfo
  autofix_monitor: ServiceInfo
  ml_model: ServiceInfo
  retrain_running?: ServiceInfo
}
export const fetchSystemServices = () =>
  c().get<{ ok: boolean; data: SystemServices }>('/api/system/services').then(r => r.data.data)

// ── Paper trading ──────────────────────────────────────────────────────────
export interface PaperStatus {
  process: { running: boolean; pid?: number; started_at?: string }
  accounts: unknown[]
  log_lines: string[]
}
export const fetchPaperStatus = () =>
  c().get<PaperStatus>('/api/paper/status').then(r => r.data)

export interface EquityPoint { t: string; v: number; strategy: string }
export const fetchPaperEquity = () =>
  c().get<EquityPoint[]>('/api/paper/equity').then(r => r.data)

export const fetchPaperAnalytics = () =>
  c().get<Record<string, unknown>>('/api/paper/analytics').then(r => r.data)

export const startPaperTrader = () =>
  c().post<{ success: boolean; message?: string }>('/api/paper/start', {}).then(r => r.data)

export const stopPaperTrader = () =>
  c().post<{ success: boolean; message?: string }>('/api/paper/stop', {}).then(r => r.data)

// ── ML / Model ────────────────────────────────────────────────────────────
export interface MLStatus {
  bundle_exists: boolean
  report_exists: boolean
  created_at: string | null
  days_old: number | null
  up_to_date: boolean
  status_label: string
  metrics?: { win_probability?: { roc_auc: number } }
  feature_importance?: Array<{ feature: string; importance: number }>
  settings?: Record<string, unknown>
}
export const fetchMLStatus = () =>
  c().get<MLStatus>('/api/ml/status').then(r => r.data)

export interface RetrainRecord {
  retrain_date?: string
  win_roc_wf?: number
  csv_rows?: number
  outcome?: string
  notes?: string
}
export const fetchMLHistory = () =>
  c().get<RetrainRecord[]>('/api/ml/history').then(r => r.data)

export const triggerRetrain = (tickers: string) =>
  c().post<{ ok: boolean; message: string; pid?: number; log?: string }>(
    '/api/ml/retrain', { tickers }
  ).then(r => r.data)

// ── Logs ──────────────────────────────────────────────────────────────────
export type LogSource = 'web' | 'cloudflared' | 'autofix' | 'paper' | 'retrain'
export const LOG_SOURCES: LogSource[] = ['web', 'cloudflared', 'autofix', 'paper', 'retrain']
export const LOG_SOURCE_LABELS: Record<LogSource, string> = {
  web: 'Web Server', cloudflared: 'CF Tunnel', autofix: 'Autofix', paper: 'Paper Trader', retrain: 'Retrain',
}
export interface LogsResponse {
  ok: boolean
  source: string
  lines: number
  entries: string[]
}
export const fetchLogsSystem = (source: LogSource, lines = 100) =>
  c().get<LogsResponse>('/api/logs/system', { params: { source, lines } }).then(r => r.data)

export interface LogSource2 { name: string; path: string; size_bytes: number; exists: boolean }
export const fetchLogsSources = () =>
  c().get<{ ok: boolean; sources: LogSource2[] }>('/api/logs/sources').then(r => r.data.sources)

export const fetchLogsTrades = (limit = 50) =>
  c().get<unknown>('/api/logs/trades', { params: { limit } }).then(r => r.data)

// ── Admin ─────────────────────────────────────────────────────────────────
export interface RuntimeStatus {
  generated_at: string
  root: string
  python: string
  platform: string
  port: number
  web_pids: string[]
  screen_sessions: string[]
  cloudflared_pids: string[]
  commands: Record<string, string | null>
}
export const fetchRuntimeStatus = () =>
  c().get<RuntimeStatus>('/api/admin/runtime/status').then(r => r.data)

export interface Diagnostics {
  runtime: RuntimeStatus
  git: { branch: string; status_short: string[]; error: string }
  env: Record<string, boolean>
  log_tail: Record<string, string[]>
}
export const fetchDiagnostics = () =>
  c().get<Diagnostics>('/api/admin/runtime/diagnostics').then(r => r.data)

export interface AuditEvent {
  ts: number; event: string; actor: string; target: string; detail: string; meta: Record<string, unknown>
}
export const fetchAudit = (limit = 100) =>
  c().get<{ events: AuditEvent[] }>('/api/admin/audit').then(r => r.data.events.slice(0, limit))

export interface AdminFlags { [key: string]: boolean }
export const fetchAdminFlags = () =>
  c().get<{ flags: AdminFlags; defaults: AdminFlags }>('/api/admin/flags').then(r => r.data)

export const saveAdminFlags = (flags: AdminFlags) =>
  c().post<{ flags: AdminFlags }>('/api/admin/flags', { flags }).then(r => r.data)

export const fetchCloudflareConfig = () =>
  c().get<Record<string, unknown>>('/api/admin/cloudflare').then(r => r.data)

export const restartWebServer = (port = 8001) =>
  c().post<{ success: boolean; message: string }>('/api/admin/runtime/web/restart', { port }).then(r => r.data)

export const startTunnel = (port = 8001) =>
  c().post<{ success: boolean }>('/api/admin/runtime/tunnel/start', { port }).then(r => r.data)

export const stopTunnel = () =>
  c().post<{ success: boolean }>('/api/admin/runtime/tunnel/stop', {}).then(r => r.data)

// ── Settings ──────────────────────────────────────────────────────────────
export const fetchSettings = () =>
  c().get<Record<string, unknown>>('/api/settings').then(r => r.data)

export const saveSettings = (patch: Record<string, unknown>) =>
  c().post<Record<string, unknown>>('/api/settings', patch).then(r => r.data)

// ── MISSING ENDPOINTS — documented for future backend implementation ───────
//
// GET  /api/data/freshness
//   → { sources: [{ name, last_updated, staleness_seconds, ok }] }
//   Safety: read-only; user auth
//   Needed for: Data Sources page
//
// GET  /api/admin/errors/recent
//   → { errors: [{ ts, level, message, source, traceback? }] }
//   Safety: read-only; admin auth
//   Needed for: Management Home "Recent Errors" panel
//
// GET  /api/admin/deploy/history
//   → { deploys: [{ ts, commit, branch, triggered_by, outcome }] }
//   Safety: read-only; admin auth
//   Needed for: Deployments history table
//
// POST /api/admin/deploy
//   body: { branch?: string, build?: boolean }
//   → { ok, job_id, message }
//   Safety: admin only; confirmation required; audit logged; dry-run flag
//   Needed for: Deployments "Deploy" action
//
// GET  /api/admin/scheduler/status
//   → { jobs: [{ name, next_run, last_run, enabled }] }
//   Safety: read-only; admin auth
//   Needed for: App Health "Schedulers" section
//
// GET  /api/market/feed/status
//   → { feeds: [{ name, last_tick, delay_ms, ok }] }
//   Safety: read-only; any auth
//   Needed for: Data Sources "Market Data Feeds" section
