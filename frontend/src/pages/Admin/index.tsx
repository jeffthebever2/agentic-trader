import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { getAdminFlags, getDiagnostics, restartWeb, startTunnel, stopTunnel } from '@/api/admin'
import { LoadingState } from '@/components/shared/LoadingState'
import { useAuthStore } from '@/store/auth'
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

interface UserRecord {
  email: string
  name?: string
  role: 'admin' | 'user' | 'viewer' | string
  created_at?: string
  phone_number?: string
  sms_verified?: boolean
  onboarding_completed?: boolean
  passkeys?: unknown[]
}

function UsersTab() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { user, previewStandardUser } = useAuthStore()
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
      await api.put(`/auth/users/${encodeURIComponent(email)}/role`, { role })
      setMsgs(m => ({ ...m, [email]: 'Saved' }))
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    } catch {
      setMsgs(m => ({ ...m, [email]: 'Error' }))
    }
  }

  const deleteUser = async (email: string) => {
    if (!window.confirm(`Permanently delete user ${email}? This cannot be undone.`)) return
    try {
      await api.delete(`/auth/users/${encodeURIComponent(email)}`)
      setMsgs(m => ({ ...m, [email]: 'Deleted' }))
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    } catch {
      setMsgs(m => ({ ...m, [email]: 'Delete failed' }))
    }
  }

  const users = data ?? []
  const admins = users.filter(u => u.role === 'admin').length
  const verified = users.filter(u => u.sms_verified).length
  const onboarded = users.filter(u => u.onboarding_completed).length
  const me = user?.actual_admin_email || user?.email || ''

  if (isLoading) return <LoadingState />
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="admin-stat-grid" style={{ flex: '1 1 520px', marginBottom: 0 }}>
          {[
            ['Total Users', users.length],
            ['Admins', admins],
            ['SMS Verified', verified],
            ['Onboarded', onboarded],
          ].map(([label, value]) => (
            <div key={label} className="admin-stat">
              <div className="admin-stat-label">{label}</div>
              <div className="admin-stat-value">{value}</div>
            </div>
          ))}
        </div>
        <button
          className="btn-secondary"
          style={{ fontSize: 11, padding: '6px 10px', alignSelf: 'flex-start' }}
          onClick={() => { previewStandardUser(); navigate('/') }}
        >
          Preview standard-user view
        </button>
      </div>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Role</th>
            <th style={thStyle}>Phone</th>
            <th style={thStyle}>SMS</th>
            <th style={thStyle}>Onboarded</th>
            <th style={thStyle}>Passkeys</th>
            <th style={thStyle}>Created</th>
            <th style={thStyle}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => {
            const isMe = u.email.toLowerCase() === me.toLowerCase()
            return (
            <tr key={u.email}>
              <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                {u.email}{isMe && <span style={{ color: 'var(--accent)', fontSize: 10, marginLeft: 4 }}>(you)</span>}
              </td>
              <td style={tdStyle}>{u.name || '—'}</td>
              <td style={tdStyle}>
                <span style={{
                  display: 'inline-flex',
                  padding: '2px 8px',
                  borderRadius: 999,
                  fontSize: 11,
                  fontWeight: 800,
                  background: u.role === 'admin' ? 'rgba(124,58,237,.16)' : 'var(--surface-soft)',
                  color: u.role === 'admin' ? '#8b5cf6' : 'var(--ink-muted)',
                }}>
                  {u.role || 'user'}
                </span>
              </td>
              <td style={tdStyle}>{u.phone_number || '—'}</td>
              <td style={tdStyle}>{u.sms_verified ? <span style={{ color: '#059669' }}>✓</span> : '—'}</td>
              <td style={tdStyle}>{u.onboarding_completed ? <span style={{ color: '#059669' }}>✓</span> : '—'}</td>
              <td style={tdStyle}>{u.passkeys?.length ?? 0}</td>
              <td style={tdStyle}>{u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
              <td style={{ ...tdStyle, display: 'flex', gap: 8, alignItems: 'center' }}>
                <select
                  value={pendingRoles[u.email] ?? u.role}
                  onChange={e => setPendingRoles(p => ({ ...p, [u.email]: e.target.value }))}
                  style={{ fontSize: 12, padding: '2px 6px', borderRadius: 4,
                           background: 'var(--surface-raised)', color: 'var(--ink)',
                           border: '1px solid var(--surface-rule)' }}
                  disabled={isMe}
                >
                  {['user', 'admin'].map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <button className="btn-secondary" style={{ fontSize: 11, padding: '3px 10px' }}
                        onClick={() => saveRole(u.email)} disabled={isMe}>Save</button>
                {!isMe && (
                  <button className="btn-danger" style={{ fontSize: 11, padding: '3px 10px' }}
                          onClick={() => deleteUser(u.email)}>Delete</button>
                )}
                {msgs[u.email] && <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{msgs[u.email]}</span>}
              </td>
            </tr>
          )})}
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

// ── Analytics ─────────────────────────────────────────────────────────────────

interface StrategyStats { win_rate: number; total_pnl: number; trades: number }
interface AnalyticsData {
  win_rate: number
  total_pnl: number
  total_trades: number
  by_strategy?: Record<string, StrategyStats>
}

function AnalyticsTab() {
  const { data, isLoading } = useQuery<AnalyticsData>({
    queryKey: ['admin', 'analytics'],
    queryFn: () => api.get('/paper/analytics').then(r => r.data),
  })
  if (isLoading) return <LoadingState />

  const winRateColor = (wr: number) =>
    wr >= 0.65 ? '#059669' : wr >= 0.5 ? '#d97706' : '#dc2626'

  const pnlColor = (pnl: number) => (pnl >= 0 ? '#059669' : '#dc2626')

  const strategies = Object.entries(data?.by_strategy ?? {})

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
        <div style={cardStyle}>
          <div style={labelStyle}>Win Rate</div>
          <div style={{ ...valueStyle, color: winRateColor(data?.win_rate ?? 0) }}>
            {data ? `${(data.win_rate * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>Total P&L</div>
          <div style={{ ...valueStyle, color: pnlColor(data?.total_pnl ?? 0) }}>
            {data ? `$${data.total_pnl.toFixed(2)}` : '—'}
          </div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>Total Trades</div>
          <div style={valueStyle}>{data?.total_trades ?? '—'}</div>
        </div>
      </div>

      {strategies.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-muted)', marginBottom: 8 }}>
            Per-Strategy Breakdown
          </div>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Strategy</th>
                <th style={thStyle}>Trades</th>
                <th style={thStyle}>Win Rate</th>
                <th style={thStyle}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map(([name, s]) => (
                <tr key={name}>
                  <td style={tdStyle}>{name}</td>
                  <td style={tdStyle}>{s.trades}</td>
                  <td style={{ ...tdStyle, color: winRateColor(s.win_rate ?? 0) }}>
                    {s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td style={{ ...tdStyle, color: pnlColor(s.total_pnl ?? 0) }}>
                    {s.total_pnl != null ? `$${s.total_pnl.toFixed(2)}` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Approvals ─────────────────────────────────────────────────────────────────

interface HilApproval {
  id: string
  ticker: string
  action: string
  shares: number
  price: number
  strategy: string
  created_at: string
  expires_at: string
}

function ApprovalsTab() {
  const qc = useQueryClient()
  const [msgs, setMsgs] = useState<Record<string, string>>({})

  const { data, isLoading } = useQuery<HilApproval[]>({
    queryKey: ['admin', 'approvals'],
    queryFn: () => api.get('/paper/hil/pending').then(r => r.data),
    refetchInterval: 30_000,
  })

  const act = async (id: string, endpoint: string, label: string) => {
    try {
      await api.post(endpoint, { id })
      setMsgs(m => ({ ...m, [id]: label }))
      qc.invalidateQueries({ queryKey: ['admin', 'approvals'] })
    } catch {
      setMsgs(m => ({ ...m, [id]: 'Error' }))
    }
  }

  if (isLoading) return <LoadingState />

  const items = data ?? []

  if (items.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13 }}>
        No pending approvals
      </div>
    )
  }

  return (
    <div style={{ padding: 20 }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Ticker</th>
            <th style={thStyle}>Action</th>
            <th style={thStyle}>Shares</th>
            <th style={thStyle}>Price</th>
            <th style={thStyle}>Strategy</th>
            <th style={thStyle}>Created</th>
            <th style={thStyle}>Expires</th>
            <th style={thStyle}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.id}>
              <td style={{ ...tdStyle, fontWeight: 600 }}>{item.ticker}</td>
              <td style={{ ...tdStyle, color: item.action === 'BUY' ? '#059669' : '#dc2626', fontWeight: 600 }}>
                {item.action}
              </td>
              <td style={tdStyle}>{item.shares}</td>
              <td style={tdStyle}>{item.price != null ? `$${item.price.toFixed(2)}` : '—'}</td>
              <td style={tdStyle}>{item.strategy}</td>
              <td style={{ ...tdStyle, fontSize: 11 }}>
                {item.created_at ? new Date(item.created_at).toLocaleString() : '—'}
              </td>
              <td style={{ ...tdStyle, fontSize: 11 }}>
                {item.expires_at ? new Date(item.expires_at).toLocaleString() : '—'}
              </td>
              <td style={{ ...tdStyle }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <button
                    className="btn-primary"
                    style={{ fontSize: 11, padding: '3px 10px', background: '#059669', borderColor: '#059669' }}
                    onClick={() => act(item.id, '/paper/hil/approve', 'Approved')}
                  >
                    Approve
                  </button>
                  <button
                    className="btn-danger"
                    style={{ fontSize: 11, padding: '3px 10px' }}
                    onClick={() => act(item.id, '/paper/hil/reject', 'Rejected')}
                  >
                    Reject
                  </button>
                  {msgs[item.id] && (
                    <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{msgs[item.id]}</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Security ──────────────────────────────────────────────────────────────────

interface UserWithSecurity {
  email: string
  totp_enabled?: boolean
  passkeys?: unknown[]
  step_up_method?: string
}

function SecurityTab() {
  const { data, isLoading } = useQuery<UserWithSecurity[]>({
    queryKey: ['admin', 'users'],
    queryFn: () => api.get<{ users: UserWithSecurity[] }>('/auth/users').then(r => r.data.users),
  })

  if (isLoading) return <LoadingState />

  const users = data ?? []
  const totpCount = users.filter(u => u.totp_enabled).length
  const passkeysCount = users.filter(u => (u.passkeys?.length ?? 0) > 0).length

  const stepUpColor = (method?: string) => {
    if (method === 'passkey') return { background: 'rgba(5,150,105,.15)', color: '#059669' }
    if (method === 'totp') return { background: 'rgba(37,99,235,.15)', color: '#2563eb' }
    return { background: 'var(--surface-soft)', color: 'var(--ink-faint)' }
  }

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
        <div style={cardStyle}>
          <div style={labelStyle}>TOTP Enabled</div>
          <div style={valueStyle}>{totpCount}</div>
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>Has Passkeys</div>
          <div style={valueStyle}>{passkeysCount}</div>
        </div>
      </div>

      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>2FA Method</th>
            <th style={thStyle}>TOTP</th>
            <th style={thStyle}>Passkeys</th>
            <th style={thStyle}>Step-Up</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => {
            const method = u.totp_enabled ? 'TOTP' : (u.passkeys?.length ?? 0) > 0 ? 'Passkey' : 'None'
            const sc = stepUpColor(u.step_up_method)
            return (
              <tr key={u.email}>
                <td style={{ ...tdStyle, fontFamily: 'var(--font-mono)', fontSize: 12 }}>{u.email}</td>
                <td style={tdStyle}>{method}</td>
                <td style={tdStyle}>
                  {u.totp_enabled ? <span style={{ color: '#059669' }}>✓</span> : '—'}
                </td>
                <td style={tdStyle}>
                  {(u.passkeys?.length ?? 0) > 0 ? (
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      minWidth: 20, height: 20, borderRadius: 999, fontSize: 11, fontWeight: 700,
                      background: 'rgba(37,99,235,.15)', color: '#2563eb', padding: '0 6px',
                    }}>
                      {u.passkeys?.length}
                    </span>
                  ) : '—'}
                </td>
                <td style={tdStyle}>
                  <span style={{
                    display: 'inline-flex', padding: '2px 8px', borderRadius: 999,
                    fontSize: 11, fontWeight: 600, ...sc,
                  }}>
                    {u.step_up_method || 'none'}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Paper Runner ──────────────────────────────────────────────────────────────

interface PaperStatus {
  process?: { running: boolean; pid?: number }
  date?: string
  log_lines?: string[]
}

function RunnerTab() {
  const [msg, setMsg] = useState('')

  const { data, isLoading, refetch } = useQuery<PaperStatus>({
    queryKey: ['admin', 'runner'],
    queryFn: () => api.get('/paper/status').then(r => r.data),
    refetchInterval: 15_000,
  })

  const doAction = async (endpoint: string, label: string) => {
    setMsg('')
    try {
      await api.post(endpoint, {})
      setMsg(`${label} OK`)
      refetch()
    } catch {
      setMsg(`${label} failed`)
    }
  }

  if (isLoading) return <LoadingState />

  const running = data?.process?.running ?? false
  const pid = data?.process?.pid
  const logLines = (data?.log_lines ?? []).slice(-20)

  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={cardStyle}>
          <div style={labelStyle}>Status</div>
          <div style={{ marginTop: 4 }}>
            <span style={{
              display: 'inline-flex', padding: '3px 10px', borderRadius: 999,
              fontSize: 13, fontWeight: 700,
              background: running ? 'rgba(5,150,105,.15)' : 'var(--surface-soft)',
              color: running ? '#059669' : 'var(--ink-faint)',
            }}>
              {running ? 'RUNNING' : 'STOPPED'}
            </span>
          </div>
        </div>
        {pid && (
          <div style={cardStyle}>
            <div style={labelStyle}>PID</div>
            <div style={{ ...valueStyle, fontSize: 18 }}>{pid}</div>
          </div>
        )}
        {data?.date && (
          <div style={cardStyle}>
            <div style={labelStyle}>Date</div>
            <div style={{ ...valueStyle, fontSize: 16 }}>{data.date}</div>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <button
          className="btn-primary"
          style={{ background: '#059669', borderColor: '#059669' }}
          onClick={() => doAction('/paper/start', 'Start')}
          disabled={running}
        >
          Start
        </button>
        <button
          className="btn-danger"
          onClick={() => doAction('/paper/stop', 'Stop')}
          disabled={!running}
        >
          Stop
        </button>
        {msg && <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{msg}</span>}
      </div>

      {logLines.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-muted)', marginBottom: 6 }}>
            Last {logLines.length} log lines
          </div>
          <pre style={preStyle}>{logLines.join('\n')}</pre>
        </div>
      )}
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
        {activeTab === 'analytics'    && <AnalyticsTab />}
        {activeTab === 'approvals'    && <ApprovalsTab />}
        {activeTab === 'security'     && <SecurityTab />}
        {activeTab === 'history'      && (
          <JsonTab queryKey={['admin', 'history']} url="/paper/backtest-index" />
        )}
        {activeTab === 'runner'       && <RunnerTab />}
      </div>
    </div>
  )
}
