import { useQuery } from '@tanstack/react-query'
import { fetchLogsSources, fetchMLStatus, fetchPaperStatus } from '../api/queries'
import { StatusDot } from '../components/StatusIndicator'
import { NotImplemented } from '../components/NotImplemented'
import { Loading, ErrorState } from '../components/StateViews'
import { Database, FileText, RefreshCw } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'

function fmtBytes(b: number) {
  if (b > 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`
  if (b > 1_000) return `${(b / 1_000).toFixed(1)} KB`
  return `${b} B`
}

export function DataSources() {
  const qc = useQueryClient()
  const logSources = useQuery({ queryKey: ['logs-sources'], queryFn: fetchLogsSources, staleTime: 60_000 })
  const mlStatus = useQuery({ queryKey: ['ml-status'], queryFn: fetchMLStatus, staleTime: 60_000 })
  const paperStatus = useQuery({ queryKey: ['paper-status'], queryFn: fetchPaperStatus, staleTime: 30_000 })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Data Sources</h1>
          <p className="text-sm text-gray-500 mt-1">Status and freshness for all platform data feeds</p>
        </div>
        <button
          onClick={() => qc.invalidateQueries()}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh All
        </button>
      </div>

      {/* Market data — MISSING endpoint (honest) */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Database className="h-4 w-4 text-gray-400" />
          <h2 className="text-sm font-semibold text-gray-300">Market Data Feeds</h2>
        </div>
        <NotImplemented
          feature="Live market feed status not available"
          reason="No backend endpoint for per-feed freshness / delay. The system uses yfinance + internal fetchers but no feed health endpoint exists."
          endpoint={{
            method: 'GET',
            path: '/api/market/feed/status',
            response: '{ feeds: [{ name, last_tick_at, delay_ms, ok, source }] }',
            notes: 'user auth; read-only; would show yfinance, news, sentiment feeds',
          }}
        />
      </div>

      {/* ML model data */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">ML Model Data</h2>
        {mlStatus.isLoading ? <Loading label="Loading…" /> : mlStatus.error ? (
          <ErrorState error={mlStatus.error} />
        ) : mlStatus.data ? (
          <div className="space-y-2">
            {[
              {
                label: 'Model bundle',
                ok: mlStatus.data.bundle_exists,
                detail: mlStatus.data.created_at ? `Created ${new Date(mlStatus.data.created_at).toLocaleString()}` : 'No bundle found',
              },
              {
                label: 'Training report',
                ok: mlStatus.data.report_exists,
                detail: mlStatus.data.days_old !== null ? `${mlStatus.data.days_old}d old` : 'Unknown age',
              },
              {
                label: 'Model freshness',
                ok: mlStatus.data.up_to_date,
                detail: mlStatus.data.status_label,
              },
            ].map(({ label, ok, detail }) => (
              <div key={label} className="flex items-center gap-3 py-2 border-b border-gray-800 last:border-0">
                <StatusDot status={ok} />
                <span className="text-sm text-gray-200 flex-1">{label}</span>
                <span className="text-xs text-gray-500">{detail}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Paper trader data */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Paper Trading State</h2>
        {paperStatus.isLoading ? <Loading label="Loading…" /> : paperStatus.error ? (
          <ErrorState error={paperStatus.error} />
        ) : paperStatus.data ? (
          <div className="space-y-2">
            <div className="flex items-center gap-3 py-2 border-b border-gray-800">
              <StatusDot status={paperStatus.data.process.running} />
              <span className="text-sm text-gray-200 flex-1">Paper trader process</span>
              <span className="text-xs text-gray-500">{paperStatus.data.process.running ? `PID ${paperStatus.data.process.pid}` : 'Stopped'}</span>
            </div>
            <div className="flex items-center gap-3 py-2">
              <StatusDot status={paperStatus.data.accounts.length > 0} />
              <span className="text-sm text-gray-200 flex-1">Trading accounts loaded</span>
              <span className="text-xs text-gray-500">{paperStatus.data.accounts.length} account(s)</span>
            </div>
          </div>
        ) : null}
      </div>

      {/* Log files */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center gap-2 mb-3">
          <FileText className="h-4 w-4 text-gray-400" />
          <h2 className="text-sm font-semibold text-gray-300">Log Files</h2>
        </div>
        {logSources.isLoading ? <Loading label="Loading…" /> : logSources.error ? (
          <ErrorState error={logSources.error} />
        ) : logSources.data ? (
          <div className="space-y-2">
            {logSources.data.map(src => (
              <div key={src.name} className="flex items-center gap-3 py-2 border-b border-gray-800 last:border-0">
                <StatusDot status={src.exists} />
                <span className="text-sm text-gray-200 flex-1">{src.name}</span>
                <span className="text-xs text-gray-500 truncate max-w-48" title={src.path}>{src.path}</span>
                <span className="text-xs text-gray-500 shrink-0">{src.exists ? fmtBytes(src.size_bytes) : 'Not found'}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* News/sentiment — missing */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">News / Sentiment Feeds</h2>
        <NotImplemented
          feature="News and sentiment feed status not available"
          reason="No endpoint exposes news/sentiment data freshness. Currently consumed inline during paper trading scan."
          endpoint={{
            method: 'GET',
            path: '/api/data/freshness',
            response: '{ sources: [{ name, last_updated, staleness_seconds, ok }] }',
            notes: 'user auth; include: news, sentiment, macro feeds',
          }}
        />
      </div>
    </div>
  )
}
