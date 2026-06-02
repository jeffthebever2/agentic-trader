export const DEFAULT_API_BASE_URL = 'https://app.agentictrader.org'
export const DEFAULT_API_TIMEOUT_MS = 15_000

export const LOG_SOURCES = ['web', 'cloudflared', 'autofix', 'paper', 'retrain'] as const

export const ENDPOINTS = {
  health: '/api/health',
  healthDeep: '/api/health/deep',
  systemMetrics: '/api/system/metrics',
  systemServices: '/api/system/services',
  logsSystem: '/api/logs/system',
  logsSources: '/api/logs/sources',
  logsPaper: '/api/logs/paper-decisions',
  logsTrades: '/api/logs/trades',
  mlStatus: '/api/ml/status',
  mlRetrain: '/api/ml/retrain',
  paperStatus: '/api/paper/status',
  settings: '/api/settings',
  adminAudit: '/api/admin/audit',
  adminFlags: '/api/admin/flags',
  adminRuntime: '/api/admin/runtime/status',
  adminDiagnostics: '/api/admin/runtime/diagnostics',
  adminWebRestart: '/api/admin/runtime/web/restart',
  adminTunnelStart: '/api/admin/runtime/tunnel/start',
  adminTunnelStop: '/api/admin/runtime/tunnel/stop',
  adminExport: '/api/admin/export',
} as const
