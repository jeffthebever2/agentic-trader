import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { Button, Badge, AsyncBoundary, EmptyState, Spinner } from '@/components/ui'
import PortfolioDashboard from './PortfolioDashboard'

// ── Types ───────────────────────────────────────────────────────────────────

interface BrokerCap {
  broker: string
  place_equity_order: boolean
  data_delay: string
  label: string
  execution: string
}
interface BrokerStatus {
  snaptrade_enabled: boolean
  keys_configured: boolean
  credentials_valid: boolean
  credentials_reason: string
  linked: boolean
  brokers: Record<string, BrokerCap>
}
interface Account {
  account_id: string
  name: string
  institution: string
  account_mask: string
}
interface Position {
  symbol: string
  quantity: number
  price: number
  market_value: number
  stale: boolean
}
interface Balance {
  cash: number
  buying_power: number
  total_value: number
  currency: string
  stale: boolean
}

// Quick-connect shortcuts (the generic button covers everything else).
const QUICK = [
  { key: 'fidelity', name: 'Fidelity', badge: 'Data · trades local', trade: false },
  { key: 'webull', name: 'Webull', badge: 'Data + live trading', trade: true },
]

// ── Styles (design-system tokens) ─────────────────────────────────────────────
const card: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--surface-rule)',
  borderRadius: 12, padding: 18, display: 'flex', flexDirection: 'column', gap: 12,
}
const title: React.CSSProperties = { fontSize: 15, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 8 }

export default function SnapTradeConnect() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const [manage, setManage] = useState(false)   // show connect screen even when linked

  const statusQ = useQuery<BrokerStatus>({
    queryKey: ['broker-status'],
    queryFn: () => api.get<BrokerStatus>('/broker/status').then(r => r.data),
    refetchInterval: 30_000,
  })

  const connectMut = useMutation({
    // broker null → SnapTrade portal lists EVERY supported brokerage.
    mutationFn: (broker: string | null) =>
      api.post<{ redirect_uri: string }>('/broker/connect-url', broker ? { broker } : {}).then(r => r.data),
    onMutate: (b) => { setBusy(b ?? 'any'); setMsg('') },
    onSuccess: (d) => {
      setBusy(null)
      window.open(d.redirect_uri, '_blank', 'noopener,noreferrer')
      setMsg('Opened SnapTrade — sign in to your broker there, then come back and press Refresh.')
    },
    onError: (e: unknown) => { setBusy(null); setMsg((e as Error)?.message || 'Connect failed.') },
  })

  const disconnectMut = useMutation({
    mutationFn: () => api.delete('/broker/disconnect').then(r => r.data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['broker-status'] }); setMsg('Disconnected.') },
  })

  const s = statusQ.data
  const off = !s?.snaptrade_enabled

  // Once connected, go straight to the portfolio dashboard — no login parts.
  if (s?.credentials_valid && s?.linked && !manage) {
    return <PortfolioDashboard onManage={() => setManage(true)} />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, width: '100%', maxWidth: 1040, margin: '0 auto' }}>
      {manage && s?.linked && (
        <div><Button variant="ghost" size="sm" onClick={() => setManage(false)}>← Back to portfolio</Button></div>
      )}
      {/* Header */}
      <div style={{ ...card }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={title}>🔗 Connect your brokerage <span style={{ fontWeight: 400, color: 'var(--ink-muted)', fontSize: 12 }}>via SnapTrade</span></div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Badge variant={s?.snaptrade_enabled ? 'success' : 'default'}>{s?.snaptrade_enabled ? 'Enabled' : 'Disabled'}</Badge>
            {s?.snaptrade_enabled && <Badge variant={s?.credentials_valid ? 'success' : 'danger'}>{s?.credentials_valid ? 'Ready' : 'Keys invalid'}</Badge>}
            {s?.linked && <Badge variant="info">Linked</Badge>}
          </div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.6, margin: 0 }}>
          Sign in to any supported brokerage with secure OAuth — your broker password never touches this app.
          Connecting is just a login; it does <strong>not</strong> require trading to be enabled.
        </p>

        {off && (
          <EmptyState compact icon="🔌" title="SnapTrade is turned off"
            description="An admin sets SNAPTRADE_ENABLED=true + adds API keys before anyone can connect." />
        )}
        {!off && s && !s.credentials_valid && (
          <EmptyState compact icon="⚠️" title="SnapTrade keys not valid yet"
            description={s.credentials_reason || 'Ask an admin to check the SnapTrade API keys.'} />
        )}

        {!off && s?.credentials_valid && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* The primary action: connect ANY brokerage */}
            <Button variant="primary" fullWidth
              loading={busy === 'any' && connectMut.isPending}
              onClick={() => connectMut.mutate(null)}>
              Connect a brokerage
            </Button>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11.5, color: 'var(--ink-faint)', alignSelf: 'center' }}>Quick connect:</span>
              {QUICK.map(q => (
                <Button key={q.key} variant="secondary" size="sm"
                  loading={busy === q.key && connectMut.isPending}
                  onClick={() => connectMut.mutate(q.key)}>
                  {q.name}
                </Button>
              ))}
            </div>
          </div>
        )}
        {msg && <div style={{ fontSize: 12.5, color: 'var(--ink-muted)' }}>{msg}</div>}
      </div>

      {/* How trading works per broker */}
      {!off && s?.credentials_valid && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
          {QUICK.map(q => (
            <div key={q.key} style={{ ...card, padding: 14, gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{q.name}</span>
                <Badge variant={q.trade ? 'success' : 'info'}>{q.trade ? 'Data + Trade' : 'Data only'}</Badge>
              </div>
              <p style={{ fontSize: 12, color: 'var(--ink-muted)', margin: 0, lineHeight: 1.5 }}>
                {q.trade
                  ? 'Live orders route through SnapTrade (impact → place), behind compliance + 2FA.'
                  : 'Account data via SnapTrade. Orders execute on the secure local route (SnapTrade can’t place Fidelity trades).'}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Linked accounts across ALL brokerages */}
      {!off && s?.credentials_valid && s?.linked && (
        <LinkedAccounts onDisconnect={() => disconnectMut.mutate()} disconnecting={disconnectMut.isPending} />
      )}
    </div>
  )
}

// ── Linked accounts (all brokerages) ──────────────────────────────────────────

function LinkedAccounts({ onDisconnect, disconnecting }: { onDisconnect: () => void; disconnecting: boolean }) {
  const qc = useQueryClient()
  const accountsQ = useQuery<{ accounts: Account[] }>({
    queryKey: ['broker-accounts', 'any'],
    queryFn: () => api.get('/broker/accounts?broker=any').then(r => r.data),
    refetchInterval: 60_000,
  })

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 12, padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)' }}>Connected accounts</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="ghost" size="sm" onClick={() => { accountsQ.refetch(); qc.invalidateQueries({ queryKey: ['broker-status'] }) }}>Refresh</Button>
          <Button variant="ghost" size="sm" loading={disconnecting} onClick={onDisconnect}>Disconnect</Button>
        </div>
      </div>

      <AsyncBoundary
        data={accountsQ.data?.accounts}
        isLoading={accountsQ.isLoading}
        isError={accountsQ.isError}
        error={accountsQ.error}
        onRetry={accountsQ.refetch}
        emptyTitle="No accounts connected yet"
        empty={<EmptyState compact icon="🏦" title="No accounts yet" description="Press “Connect a brokerage” above and finish signing in." />}
        loading={<div style={{ padding: 16, color: 'var(--ink-muted)' }}><Spinner /> Loading accounts…</div>}
      >
        {(accounts) => (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {accounts.map((a, i) => (
              <AccountRow key={a.account_id} account={a} defaultOpen={accounts.length === 1 || i === 0} />
            ))}
          </div>
        )}
      </AsyncBoundary>
    </div>
  )
}

const money = (n: number | undefined) =>
  n == null ? '—' : '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

// ── One account: clickable header that expands to holdings + balance ───────────
function AccountRow({ account, defaultOpen }: { account: Account; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = account.account_id

  const posQ = useQuery<{ positions: Position[] }>({
    queryKey: ['broker-positions', id],
    queryFn: () => api.get(`/broker/accounts/${id}/positions?broker=any`).then(r => r.data),
    enabled: open,
    refetchInterval: open ? 60_000 : false,
  })
  const balQ = useQuery<{ balance: Balance }>({
    queryKey: ['broker-balance', id],
    queryFn: () => api.get(`/broker/accounts/${id}/balances?broker=any`).then(r => r.data),
    enabled: open,
  })

  const positions = posQ.data?.positions ?? []
  const holdingsValue = positions.reduce((s, p) => s + (p.market_value || 0), 0)
  const bal = balQ.data?.balance
  const stale = positions.some(p => p.stale) || bal?.stale

  return (
    <div style={{ borderRadius: 10, border: '1px solid var(--surface-rule)', background: 'var(--surface-raised)', overflow: 'hidden' }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
                 padding: '12px 14px', background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--ink)' }}>{account.name || 'Account'}</span>
          <span style={{ fontSize: 11.5, color: 'var(--ink-faint)' }}>
            {account.institution || 'Brokerage'} · {account.account_mask}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {open && <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>{money((bal?.total_value || 0) + holdingsValue || holdingsValue + (bal?.cash || 0))}</span>}
          <span style={{ fontSize: 12, color: 'var(--ink-faint)', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>▸</span>
        </div>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--surface-rule)', padding: '10px 14px 14px' }}>
          {stale && (
            <div style={{ fontSize: 11, color: '#d97706', marginBottom: 8 }}>
              ⚠️ SnapTrade holdings can lag up to ~24h — values may be delayed.
            </div>
          )}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 10, fontSize: 12.5 }}>
            <span style={{ color: 'var(--ink-muted)' }}>Cash <strong style={{ color: 'var(--ink)' }}>{money(bal?.cash)}</strong></span>
            <span style={{ color: 'var(--ink-muted)' }}>Holdings <strong style={{ color: 'var(--ink)' }}>{money(holdingsValue)}</strong></span>
            <span style={{ color: 'var(--ink-muted)' }}>Positions <strong style={{ color: 'var(--ink)' }}>{positions.length}</strong></span>
          </div>

          <AsyncBoundary
            data={posQ.data?.positions}
            isLoading={posQ.isLoading}
            isError={posQ.isError}
            error={posQ.error}
            onRetry={posQ.refetch}
            emptyTitle="No holdings in this account"
            loading={<div style={{ padding: 12, color: 'var(--ink-muted)' }}><Spinner /> Loading holdings…</div>}
          >
            {(rows) => (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, fontVariantNumeric: 'tabular-nums' }}>
                  <thead>
                    <tr style={{ color: 'var(--ink-faint)', textAlign: 'right' }}>
                      <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 600 }}>Symbol</th>
                      <th style={{ padding: '4px 8px', fontWeight: 600 }}>Qty</th>
                      <th style={{ padding: '4px 8px', fontWeight: 600 }}>Price</th>
                      <th style={{ padding: '4px 8px', fontWeight: 600 }}>Market value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(p => (
                      <tr key={p.symbol} style={{ borderTop: '1px solid var(--surface-rule)' }}>
                        <td style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 600, color: 'var(--ink)' }}>{p.symbol}</td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--ink-muted)' }}>{p.quantity}</td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--ink-muted)' }}>{money(p.price)}</td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--ink)' }}>{money(p.market_value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </AsyncBoundary>
        </div>
      )}
    </div>
  )
}
