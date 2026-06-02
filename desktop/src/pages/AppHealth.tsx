import { useQuery } from '@tanstack/react-query'
import { fetchHealth, fetchSystemMetrics, fetchDiagnostics, fetchAuthFeatures } from '../api/queries'
import { StatusBadge } from '../components/StatusIndicator'
import { NotImplemented } from '../components/NotImplemented'
import { Loading, ErrorState } from '../components/StateViews'
import { CheckCircle, XCircle, HelpCircle } from 'lucide-react'

interface HealthRowProps {
  label: string
  ok: boolean | null | undefined
  detail?: string
}
function HealthRow({ label, ok, detail }: HealthRowProps) {
  const icon = ok === null || ok === undefined
    ? <HelpCircle className="h-4 w-4 text-gray-600" />
    : ok
      ? <CheckCircle className="h-4 w-4 text-green-400" />
      : <XCircle className="h-4 w-4 text-red-400" />

  return (
    <div className="flex items-center justify-between py-2.5 border-b border-gray-800 last:border-0">
      <div>
        <span className="text-sm text-gray-200">{label}</span>
        {detail && <p className="text-xs text-gray-500 mt-0.5">{detail}</p>}
      </div>
      <div className="flex items-center gap-2">
        {icon}
        <StatusBadge status={ok ?? null} label={ok === null || ok === undefined ? 'Unknown' : ok ? 'OK' : 'Failed'} />
      </div>
    </div>
  )
}

function SectionBox({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
      <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">{title}</h2>
      {children}
    </div>
  )
}

export function AppHealth() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, refetchInterval: 30_000 })
  const metrics = useQuery({ queryKey: ['system-metrics'], queryFn: fetchSystemMetrics, refetchInterval: 15_000 })
  const diag = useQuery({ queryKey: ['diagnostics'], queryFn: fetchDiagnostics, staleTime: 60_000 })
  const features = useQuery({ queryKey: ['auth-features'], queryFn: fetchAuthFeatures, staleTime: 300_000 })

  if (health.isLoading) return <Loading label="Running health checks…" />
  if (health.error) return <ErrorState error={health.error} retry={health.refetch} />

  const h = health.data
  const m = metrics.data
  const d = diag.data
  const f = features.data

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-white">App Health</h1>
        <p className="text-sm text-gray-500 mt-1">Health checks for all platform layers</p>
      </div>

      {/* Core services */}
      <SectionBox title="Core Services">
        <HealthRow label="Backend API" ok={h?.status === 'ok'} detail="GET /api/health/deep" />
        <HealthRow label="Paper Trader Process" ok={h?.paper_trader?.running} detail={h?.paper_trader?.pid ? `PID ${h.paper_trader.pid}` : 'Not running'} />
        <HealthRow label="Cloudflare Tunnel" ok={h?.cloudflare_tunnel?.running} detail="Exposes app to internet" />
        <HealthRow label="Autofix Monitor" ok={h?.autofix_monitor?.running} detail="Auto-restarts paper trader" />
      </SectionBox>

      {/* ML / Model */}
      <SectionBox title="ML / Model">
        <HealthRow label="Model Bundle" ok={h?.ml_model?.ok} detail={h?.ml_model?.wf_roc ? `WF ROC ${h.ml_model.wf_roc.toFixed(4)}` : undefined} />
        <HealthRow label="Model Freshness" ok={h?.ml_model?.age_hours !== undefined ? h.ml_model.age_hours < 200 : null} detail={h?.ml_model?.age_hours !== undefined ? `${h.ml_model.age_hours.toFixed(1)}h old` : 'Age unknown'} />
      </SectionBox>

      {/* Server resources */}
      <SectionBox title="Server Resources">
        {metrics.isLoading ? <Loading label="Loading…" /> : metrics.error ? (
          <ErrorState error={metrics.error} />
        ) : m ? (
          <>
            <HealthRow label="CPU" ok={(m.cpu_percent ?? 0) < 85} detail={`${(m.cpu_percent ?? 0).toFixed(1)}% used`} />
            <HealthRow label="Memory" ok={(m.memory?.percent ?? 0) < 85} detail={m.memory ? `${m.memory.percent.toFixed(0)}% used (${(m.memory.used_mb / 1024).toFixed(1)} GB)` : undefined} />
            <HealthRow label="Disk" ok={(m.disk?.percent ?? 0) < 90} detail={m.disk ? `${m.disk.percent}% used — ${m.disk.free_gb.toFixed(1)} GB free` : undefined} />
          </>
        ) : null}
      </SectionBox>

      {/* Environment */}
      <SectionBox title="Environment Configuration">
        {diag.isLoading ? <Loading label="Loading…" /> : diag.error ? (
          <p className="text-xs text-gray-500">Diagnostics unavailable (admin access required)</p>
        ) : d?.env ? (
          <>
            <HealthRow label="Cloudflare Access" ok={d.env.cloudflare_access} detail="CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD" />
            <HealthRow label="Cloudflare AI" ok={d.env.cloudflare_ai} detail="CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID" />
            <HealthRow label="SMTP" ok={d.env.smtp} detail="SMTP_HOST + SMTP_USERNAME" />
            <HealthRow label="SendBlue SMS" ok={d.env.sendblue} detail="SENDBLUE_API_KEY_ID + SENDBLUE_API_SECRET" />
            <HealthRow label=".env file present" ok={(d.env as Record<string, boolean>).env_file_present} />
          </>
        ) : null}
      </SectionBox>

      {/* Feature flags */}
      <SectionBox title="Feature Flags (Auth)">
        {features.isLoading ? <Loading label="Loading…" /> : features.error ? (
          <p className="text-xs text-gray-500">Features unavailable</p>
        ) : f ? (
          Object.entries(f).map(([k, v]) => (
            <HealthRow key={k} label={k.replace(/_/g, ' ')} ok={v} />
          ))
        ) : null}
      </SectionBox>

      {/* Schedulers — missing endpoint */}
      <SectionBox title="Schedulers / Workers">
        <NotImplemented
          feature="Scheduler status not available"
          reason="No backend endpoint for job scheduler state."
          endpoint={{
            method: 'GET',
            path: '/api/admin/scheduler/status',
            response: '{ jobs: [{ name, next_run, last_run, enabled }] }',
            notes: 'admin auth required',
          }}
        />
      </SectionBox>

      {/* External data feeds — missing endpoint */}
      <SectionBox title="External Data Feeds">
        <NotImplemented
          feature="Market feed health not available"
          reason="No backend endpoint for data feed status."
          endpoint={{
            method: 'GET',
            path: '/api/market/feed/status',
            response: '{ feeds: [{ name, last_tick, delay_ms, ok }] }',
            notes: 'user auth; read-only',
          }}
        />
      </SectionBox>
    </div>
  )
}
