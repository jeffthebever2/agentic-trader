import { useQuery } from '@tanstack/react-query'
import { Clock, Server, TrendingUp, AlertTriangle, Activity } from 'lucide-react'
import {
  fetchHealth, fetchSystemMetrics, fetchSystemServices, fetchMLStatus, fetchAudit,
} from '../api/queries'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge, StatusDot } from '../components/StatusIndicator'
import { SafeModeBar } from '../components/SafeModeBar'
import { Loading, ErrorState } from '../components/StateViews'
import { useAuthStore } from '../store/auth'

function fmtUptime(sec: number) {
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return d > 0 ? `${d}d ${h}h ${m}m` : `${h}h ${m}m`
}

function fmtTs(ts: number) {
  return new Date(ts * 1000).toLocaleString()
}

export function Home() {
  const { user } = useAuthStore()
  const health  = useQuery({ queryKey: ['health'], queryFn: fetchHealth, refetchInterval: 30_000 })
  const metrics = useQuery({ queryKey: ['system-metrics'], queryFn: fetchSystemMetrics, refetchInterval: 15_000 })
  const svc     = useQuery({ queryKey: ['system-services'], queryFn: fetchSystemServices, refetchInterval: 15_000 })
  const ml      = useQuery({ queryKey: ['ml-status'], queryFn: fetchMLStatus, staleTime: 60_000 })
  const audit   = useQuery({ queryKey: ['audit'], queryFn: () => fetchAudit(20), staleTime: 60_000 })

  const h = health.data
  const m = metrics.data
  const s = svc.data
  const isOnline = h?.status === 'ok'

  if (metrics.isLoading && health.isLoading) return <Loading label="Connecting to server…" />

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Management Home</h1>
          <p className="text-sm text-gray-500 mt-0.5">Signed in as <span className="text-gray-300">{user?.email}</span></p>
        </div>
        <div className="flex items-center gap-2">
          <StatusDot status={isOnline ? 'ok' : 'error'} size="md" />
          <span className="text-sm font-medium text-white">{isOnline ? 'Server Online' : 'Server Offline'}</span>
        </div>
      </div>

      <SafeModeBar />

      {/* Metric cards */}
      {metrics.error ? (
        <ErrorState error={metrics.error} retry={metrics.refetch} />
      ) : (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          <MetricCard
            title="Uptime"
            value={m ? fmtUptime(m.uptime_seconds) : '—'}
            sub={m ? `PID ${m.process?.pid ?? '—'}` : 'Unavailable'}
            icon={<Clock className="h-3.5 w-3.5" />}
            accent="blue"
          />
          <MetricCard
            title="CPU"
            value={m ? `${(m.cpu_percent ?? 0).toFixed(1)}%` : '—'}
            sub={m ? `${m.process?.threads ?? 0} threads` : undefined}
            icon={<Activity className="h-3.5 w-3.5" />}
            accent={!m ? 'gray' : (m.cpu_percent ?? 0) > 80 ? 'red' : (m.cpu_percent ?? 0) > 50 ? 'yellow' : 'green'}
          />
          <MetricCard
            title="RAM"
            value={m ? `${(m.memory?.percent ?? 0).toFixed(0)}%` : '—'}
            sub={m?.memory ? `${(m.memory.used_mb / 1024).toFixed(1)} / ${(m.memory.total_mb / 1024).toFixed(1)} GB` : undefined}
            icon={<Server className="h-3.5 w-3.5" />}
            accent={!m ? 'gray' : (m.memory?.percent ?? 0) > 80 ? 'red' : (m.memory?.percent ?? 0) > 60 ? 'yellow' : 'green'}
          />
          <MetricCard
            title="ML Model"
            value={ml.data?.metrics?.win_probability?.roc_auc?.toFixed(4) ?? '—'}
            sub={ml.data?.status_label ?? 'Unknown'}
            icon={<TrendingUp className="h-3.5 w-3.5" />}
            accent={ml.data?.up_to_date ? 'green' : ml.data?.bundle_exists ? 'yellow' : 'red'}
          />
        </div>
      )}

      {/* Services snapshot */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="rounded-xl bg-gray-900 p-4 ring-1 ring-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Active Services</h2>
          {svc.isLoading ? <Loading label="Loading…" /> : svc.error ? (
            <ErrorState error={svc.error} />
          ) : s ? (
            <div className="space-y-2">
              {[
                { label: 'Paper Trader', running: s.paper_runner?.running, detail: s.paper_runner?.pid ? `PID ${s.paper_runner.pid}` : undefined },
                { label: 'CF Tunnel', running: s.cloudflare_tunnel?.running },
                { label: 'Autofix', running: s.autofix_monitor?.running },
                { label: 'ML Model', running: s.ml_model?.ok, detail: s.ml_model?.wf_roc ? `WF ROC ${s.ml_model.wf_roc.toFixed(4)}` : undefined },
              ].map(({ label, running, detail }) => (
                <div key={label} className="flex items-center justify-between text-sm">
                  <div>
                    <span className="text-gray-200">{label}</span>
                    {detail && <span className="ml-2 text-xs text-gray-500">{detail}</span>}
                  </div>
                  <StatusBadge status={running ?? null} label={running ? 'Running' : running === false ? 'Stopped' : undefined} />
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {/* Recent audit events */}
        <div className="rounded-xl bg-gray-900 p-4 ring-1 ring-gray-700">
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Recent Activity</h2>
          {audit.isLoading ? <Loading label="Loading…" /> : audit.error ? (
            <p className="text-xs text-gray-500">Activity log unavailable (admin access required)</p>
          ) : !audit.data?.length ? (
            <p className="text-xs text-gray-500">No recent activity</p>
          ) : (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {audit.data.slice(0, 10).map((ev, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <span className="text-gray-600 shrink-0">{fmtTs(ev.ts)}</span>
                  <span className="text-gray-300">{ev.event}</span>
                  <span className="text-gray-500 ml-auto shrink-0">{ev.actor}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* API health quick matrix */}
      <div className="rounded-xl bg-gray-900 p-4 ring-1 ring-gray-700">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">API / Backend Health</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Backend API', ok: h?.status === 'ok' },
            { label: 'Paper Trader', ok: h?.paper_trader?.running },
            { label: 'CF Tunnel', ok: h?.cloudflare_tunnel?.running },
            { label: 'ML Model', ok: h?.ml_model?.ok },
          ].map(({ label, ok }) => (
            <div key={label} className="flex items-center gap-2 rounded-lg bg-gray-800 px-3 py-2">
              <StatusDot status={ok ?? null} />
              <span className="text-xs text-gray-300">{label}</span>
            </div>
          ))}
        </div>

        {/* Data freshness — documented as MISSING but shown honestly */}
        <div className="mt-3 rounded-lg bg-gray-800/60 px-3 py-2 flex items-center gap-2">
          <AlertTriangle className="h-3.5 w-3.5 text-yellow-500 shrink-0" />
          <span className="text-xs text-gray-500">
            Market data feed freshness — requires <code className="text-indigo-400">GET /api/market/feed/status</code> (not yet implemented)
          </span>
        </div>
      </div>
    </div>
  )
}
