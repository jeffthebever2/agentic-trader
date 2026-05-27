import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { getAdminFlags, getDiagnostics, restartWeb, startTunnel, stopTunnel } from '@/api/admin'
import { LoadingState } from '@/components/shared/LoadingState'
import type { AdminFlags } from '@/types'

const ADMIN_TABS = [
  { id: 'overview',     label: 'Overview' },
  { id: 'users',        label: 'Users' },
  { id: 'security',     label: 'Security' },
  { id: 'approvals',    label: 'Approvals' },
  { id: 'runner',       label: 'Paper Runner' },
  { id: 'analytics',    label: 'Performance' },
  { id: 'models',       label: 'Models' },
  { id: 'providers',    label: 'Providers' },
  { id: 'runtime',      label: 'Runtime' },
  { id: 'health',       label: 'System Health' },
  { id: 'logs',         label: 'Logs & Activity' },
  { id: 'history',      label: 'Backtests' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'cloudflare',   label: 'Cloudflare' },
  { id: 'flags',        label: 'Flags' },
  { id: 'backup',       label: 'Backup' },
]

const cardStyle: React.CSSProperties = {
  background: 'var(--surface-raised)', borderRadius: 8, padding: 14,
}
const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--ink-faint)' }
const valueStyle: React.CSSProperties = { fontSize: 22, fontWeight: 700, color: 'var(--ink)' }
const preStyle: React.CSSProperties = {
  fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--ink-muted)',
  background: 'var(--canvas)', borderRadius: 6, padding: 14,
  whiteSpace: 'pre-wrap', overflow: 'auto',
}
const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 13 }
const thStyle: React.CSSProperties = {
  textAlign: 'left', fontSize: 11, color: 'var(--ink-faint)',
  borderBottom: '1px solid var(--surface-rule)', padding: '6px 10px',
}
const tdStyle: React.CSSProperties = {
  padding: '8px 10px', borderBottom: '1px solid var(--surface-rule)', color: 'var(--ink)',
}

// ── Overview ─────────────────────────────────────────────────────────────────

function OverviewTab() {
  const diagQ   = useQuery({ queryKey: ['admin', 'diagnostics'], queryFn: getDiagnostics, staleTime: 30_000 })
  const statusQ = useQuery({
    queryKey: ['admin', 'runtime', 'status'],
    queryFn: () => api.get('/admin/runtime/status').then(r => r.data),
    staleTime: 30_000,
  })

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
        {[
          { label: 'Memory (MB)', value: diagQ.data?.memory_mb?.toFixed(0) ?? '—' },
          { label: 'CPU %',       value: diagQ.data?.cpu_pct?.toFixed(1)   ?? '—' },
          { label: 'Web PIDs',    value: diagQ.data?.runtime?.web_pids?.join(', ') || '—' },
          { label: 'Python',      value: diagQ.data?.runtime?.python ?? '—' },
        ].map(m => (
          <div key={m.label} style={cardStyle}>
            <div style={labelStyle}>{m.label}</div>
            <div style={valueStyle}>{m.value}</div>
          </div>
        ))}
      </div>

      {statusQ.data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 14 }}>
          {[
            { label: 'Web Process',  value: statusQ.data.web_running    ? 'Running' : 'Stopped' },
            { label: 'Tunnel',       value: statusQ.data.tunnel_running ? 'Running' : 'Stopped' },
            { label: 'Process ID',   value: statusQ.data.process_id ?? '—' },
          ].map(m => (
            <div key={m.label} style={cardStyle}>
              <div style={labelStyle}>{m.label}</div>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>{String(m.value)}</div>
            </div>
          ))}
        </div>
      )}

      <button className="btn-secondary" style={{ width: 'fit-content' }}
              onClick={() => restartWeb()}>
        Restart Web Server
      </button>
    </div>
  )
}

// ── Users ─────────────────────────────────────────────────────────────────────

interface UserRecord { email: string; role: string; created_at: string }

function UsersTab() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => api.get<{ users: UserRecord[] }>('/auth/users').then(r => r.data.users),
  })
  const [pendingRoles, setPendingRoles] = useState<Record<string, string>>({})
  const [msgs, setMsgs] = useState<Record<string, string>>({})

  const saveRole = async (email: string) => {
    const role = pendingRoles[email]
    if (!role) return
    try {
      await api.patch(`/auth/users/${encodeURIComponent(email)}/role`, { role })
      setMsgs(m => ({ ...m, [email]: 'Saved' }))
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    } catch {
      setMsgs(m => ({ ...m, [email]: 'Error' }))
    }
  }

  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>Role</th>
            <th style={thStyle}>Created</th>
            <th style={thStyle}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map(u => (
            <tr key={u.email}>
              <td style={tdStyle}>{u.email}</td>
              <td style={tdStyle}>{u.role}</td>
              <td style={tdStyle}>{u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
              <td style={{ ...tdStyle, display: 'flex', gap: 8, alignItems: 'center' }}>
                <select
                  value={pendingRoles[u.email] ?? u.role}
                  onChange={e => setPendingRoles(p => ({ ...p, [u.email]: e.target.value }))}
                  style={{ fontSize: 12, padding: '2px 6px', borderRadius: 4,
                           background: 'var(--surface-raised)', color: 'var(--ink)',
                           border: '1px solid var(--surface-rule)' }}
                >
                  {['user', 'admin', 'viewer'].map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <button className="btn-secondary" style={{ fontSize: 11, padding: '3px 10px' }}
                        onClick={() => saveRole(u.email)}>Save</button>
                {msgs[u.email] && <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{msgs[u.email]}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Runtime ───────────────────────────────────────────────────────────────────

function RuntimeTab() {
  const [msg, setMsg] = useState('')
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['admin', 'runtime', 'status'],
    queryFn: () => api.get('/admin/runtime/status').then(r => r.data),
  })

  const act = async (fn: () => Promise<unknown>, label: string) => {
    try { await fn(); setMsg(`${label} OK`); refetch() }
    catch { setMsg(`${label} failed`) }
  }

  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 14 }}>
        {[
          { label: 'Web Server',  value: data?.web_running    ? 'Running' : 'Stopped' },
          { label: 'Tunnel',      value: data?.tunnel_running ? 'Running' : 'Stopped' },
          { label: 'Process ID',  value: data?.process_id ?? '—' },
        ].map(m => (
          <div key={m.label} style={cardStyle}>
            <div style={labelStyle}>{m.label}</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>{String(m.value)}</div>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button className="btn-secondary" onClick={() => act(restartWeb,    'Restart Web')}>Restart Web</button>
        <button className="btn-secondary" onClick={() => act(startTunnel,   'Start Tunnel')}>Start Tunnel</button>
        <button className="btn-secondary" onClick={() => act(stopTunnel,    'Stop Tunnel')}>Stop Tunnel</button>
      </div>
      {msg && <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{msg}</div>}
    </div>
  )
}

// ── System Health ─────────────────────────────────────────────────────────────

function HealthTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'health'],
    queryFn: () => api.get('/paper/system-health').then(r => r.data),
  })
  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <pre style={preStyle}>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}

// ── Logs ──────────────────────────────────────────────────────────────────────

interface AuditEntry { ts: string; user: string; action: string; detail: string }

function LogsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'audit'],
    queryFn: () => api.get<{ entries: AuditEntry[] }>('/admin/audit').then(r => r.data.entries?.slice(0, 50) ?? []),
  })
  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Timestamp</th>
            <th style={thStyle}>User</th>
            <th style={thStyle}>Action</th>
            <th style={thStyle}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((e, i) => (
            <tr key={i}>
              <td style={{ ...tdStyle, whiteSpace: 'nowrap', fontSize: 11 }}>
                {e.ts ? new Date(e.ts).toLocaleString() : '—'}
              </td>
              <td style={tdStyle}>{e.user}</td>
              <td style={tdStyle}>{e.action}</td>
              <td style={{ ...tdStyle, fontSize: 11, color: 'var(--ink-muted)' }}>{e.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Flags ─────────────────────────────────────────────────────────────────────

function FlagsTab() {
  const { data, isLoading } = useQuery({ queryKey: ['admin', 'flags'], queryFn: getAdminFlags })
  const [text, setText] = useState<string | null>(null)
  const [msg,  setMsg]  = useState('')
  const qc = useQueryClient()

  const displayed = text ?? (data ? JSON.stringify(data, null, 2) : '')

  const save = async () => {
    try {
      const parsed = JSON.parse(displayed) as AdminFlags
      await api.post('/admin/flags', parsed)
      setMsg('Saved')
      qc.invalidateQueries({ queryKey: ['admin', 'flags'] })
    } catch (e) {
      setMsg(e instanceof SyntaxError ? 'Invalid JSON' : 'Save failed')
    }
  }

  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <textarea
        value={displayed}
        onChange={e => { setText(e.target.value); setMsg('') }}
        rows={24}
        style={{ ...preStyle, resize: 'vertical', width: '100%', boxSizing: 'border-box',
                 border: '1px solid var(--surface-rule)', outline: 'none' }}
      />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn-primary" style={{ width: 'fit-content' }} onClick={save}>Save Flags</button>
        {msg && <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{msg}</span>}
      </div>
    </div>
  )
}

// ── Cloudflare ────────────────────────────────────────────────────────────────

function CloudflareTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'cloudflare'],
    queryFn: () => api.get('/admin/cloudflare').then(r => r.data),
  })
  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <pre style={preStyle}>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}

// ── Backup ────────────────────────────────────────────────────────────────────

function BackupTab() {
  const [msg, setMsg] = useState('')
  const exportData = async () => {
    try {
      const res = await api.get('/admin/export', { responseType: 'blob' })
      const url = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `tradingagents-export-${Date.now()}.json`
      a.click()
      URL.revokeObjectURL(url)
      setMsg('Download started')
    } catch {
      setMsg('Export failed')
    }
  }
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13, color: 'var(--ink-muted)' }}>
        Download a full data export from the server.
      </div>
      <button className="btn-secondary" style={{ width: 'fit-content' }} onClick={exportData}>
        Export Data
      </button>
      {msg && <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{msg}</div>}
    </div>
  )
}

// ── Integrations ──────────────────────────────────────────────────────────────

function IntegrationsTab() {
  const [phone, setPhone]     = useState('')
  const [smsMsg, setSmsMsg]   = useState('')
  const [emailMsg, setEmailMsg] = useState('')

  const { data: smsStatus } = useQuery({
    queryKey: ['admin', 'sms', 'status'],
    queryFn: () => api.get('/paper/sms/status').then(r => r.data),
  })

  const sendSms = async () => {
    try {
      await api.post('/paper/sms/test', { number: phone })
      setSmsMsg('SMS sent')
    } catch { setSmsMsg('SMS failed') }
  }

  const sendEmail = async () => {
    try {
      await api.post('/paper/email/test')
      setEmailMsg('Email sent')
    } catch { setEmailMsg('Email failed') }
  }

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>SMS</div>
        {smsStatus && (
          <pre style={{ ...preStyle, marginBottom: 10 }}>{JSON.stringify(smsStatus, null, 2)}</pre>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="tel"
            placeholder="+1234567890"
            value={phone}
            onChange={e => setPhone(e.target.value)}
            style={{ fontSize: 13, padding: '5px 10px', borderRadius: 5,
                     background: 'var(--surface-raised)', color: 'var(--ink)',
                     border: '1px solid var(--surface-rule)', outline: 'none' }}
          />
          <button className="btn-secondary" onClick={sendSms}>Send Test SMS</button>
          {smsMsg && <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{smsMsg}</span>}
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>Email</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn-secondary" onClick={sendEmail}>Send Test Email</button>
          {emailMsg && <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{emailMsg}</span>}
        </div>
      </div>
    </div>
  )
}

// ── Generic JSON tab ──────────────────────────────────────────────────────────

function JsonTab({ queryKey, url }: { queryKey: unknown[]; url: string }) {
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get(url).then(r => r.data),
  })
  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <pre style={preStyle}>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}

// ── Models ────────────────────────────────────────────────────────────────────

function ModelsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'ml', 'status'],
    queryFn: () => api.get('/ml/status').then(r => r.data),
  })
  if (isLoading) return <LoadingState />
  if (!data) return <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 13 }}>No data</div>
  const entries = typeof data === 'object' && data !== null ? Object.entries(data) : []
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {entries.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
          {entries.map(([k, v]) => (
            <div key={k} style={cardStyle}>
              <div style={labelStyle}>{k}</div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginTop: 4 }}>
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <pre style={preStyle}>{JSON.stringify(data, null, 2)}</pre>
      )}
    </div>
  )
}

// ── Providers ─────────────────────────────────────────────────────────────────

function ProvidersTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'settings'],
    queryFn: () => api.get('/settings').then(r => r.data),
  })
  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20 }}>
      <pre style={preStyle}>{JSON.stringify(data, null, 2)}</pre>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState('overview')

  return (
    <div id="panel-admin" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>Admin</div>
      </div>

      {/* Tab bar */}
      <div style={{ borderBottom: '1px solid var(--surface-rule)', overflowX: 'auto' }}>
        <div style={{ display: 'flex', gap: 0 }}>
          {ADMIN_TABS.map(tab => (
            <button
              key={tab.id}
              id={`admin-tab-${tab.id}`}
              className={`admin-tab ${activeTab === tab.id ? 'admin-tab-active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
              style={{
                background: 'none',
                border: 'none',
                borderBottom: activeTab === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
                color: activeTab === tab.id ? 'var(--ink)' : 'var(--ink-faint)',
                fontWeight: activeTab === tab.id ? 600 : 400,
                fontSize: 12, padding: '8px 12px', cursor: 'pointer', whiteSpace: 'nowrap',
                transition: 'color .15s, border-color .15s',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'overview'     && <OverviewTab />}
        {activeTab === 'users'        && <UsersTab />}
        {activeTab === 'runtime'      && <RuntimeTab />}
        {activeTab === 'health'       && <HealthTab />}
        {activeTab === 'logs'         && <LogsTab />}
        {activeTab === 'flags'        && <FlagsTab />}
        {activeTab === 'cloudflare'   && <CloudflareTab />}
        {activeTab === 'backup'       && <BackupTab />}
        {activeTab === 'integrations' && <IntegrationsTab />}
        {activeTab === 'models'       && <ModelsTab />}
        {activeTab === 'providers'    && <ProvidersTab />}
        {activeTab === 'analytics'    && (
          <JsonTab queryKey={['admin', 'analytics']} url="/paper/analytics" />
        )}
        {activeTab === 'approvals'    && (
          <JsonTab queryKey={['admin', 'approvals']} url="/paper/hil/pending" />
        )}
        {activeTab === 'security'     && (
          <JsonTab queryKey={['admin', 'security']} url="/auth/2fa/status" />
        )}
        {activeTab === 'history'      && (
          <JsonTab queryKey={['admin', 'history']} url="/backtest/history" />
        )}
        {activeTab === 'runner'       && (
          <JsonTab queryKey={['admin', 'runner']} url="/paper/status" />
        )}
      </div>
    </div>
  )
}
