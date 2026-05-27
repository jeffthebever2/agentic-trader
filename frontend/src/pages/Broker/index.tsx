import { useState, useRef, useCallback, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import api, { wsUrl } from '@/api/client'

// ── Types ────────────────────────────────────────────────────────────────────

interface WebullStatus {
  connected: boolean
  account_id?: string
  token_expires?: string
  has_trade_pin?: boolean
}

interface WebullAccount {
  net_liq: number
  buying_power: number
  cash: number
  unrealized_pnl: number
  account_id?: string
  token_expires?: string
}

interface WebullPosition {
  symbol: string
  quantity: number
  cost_basis: number
  last_price: number
  market_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
}

interface WebullOrder {
  symbol: string
  action: string
  order_type: string
  quantity: number
  filled_qty: number
  price: number
  avg_fill: number
  status: string
  create_time: string
}

interface FidelityStatus {
  connected: boolean
  session_file?: boolean
}

interface FidelityPosition {
  symbol: string
  description?: string
  last_price?: string
  today_gain_loss?: string
  today_gain_pct?: string
  total_gain_loss?: string
  total_gain_pct?: string
  market_value?: string
  pct_of_account?: string
  qty?: string
  cost_basis?: string
  cost_per_share?: string
}

interface FidelitySummary {
  total_value?: string | null
  daily_change?: string | null
  daily_change_pct?: string | null
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt$(n: number | null | undefined): string {
  if (n == null) return '—'
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function pnlColor(n: number): string {
  return n >= 0 ? '#4ade80' : '#f87171'
}

function fmtPct(n: number): string {
  return (n >= 0 ? '+' : '') + (n * 100).toFixed(2) + '%'
}

// ── Shared style snippets ────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 10,
  padding: 20,
}

const label11: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--ink-faint)',
  textTransform: 'uppercase',
  letterSpacing: '.04em',
  marginBottom: 4,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  fontSize: 13,
  background: 'var(--surface-raised)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 6,
  color: 'var(--ink)',
  outline: 'none',
  boxSizing: 'border-box',
}

const btnPrimary: React.CSSProperties = {
  padding: '8px 16px',
  fontSize: 13,
  fontWeight: 600,
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 6,
  cursor: 'pointer',
}

const btnSecondary: React.CSSProperties = {
  padding: '8px 14px',
  fontSize: 13,
  fontWeight: 600,
  background: 'var(--surface-raised)',
  color: 'var(--ink)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 6,
  cursor: 'pointer',
}

const tblHead: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--ink-faint)',
  textTransform: 'uppercase',
  letterSpacing: '.04em',
  padding: '8px 12px',
  borderBottom: '1px solid var(--surface-rule)',
  textAlign: 'left',
  whiteSpace: 'nowrap',
}

const tblCell: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--ink)',
  padding: '8px 12px',
  borderBottom: '1px solid var(--surface-rule)',
  fontFamily: 'var(--font-mono)',
  whiteSpace: 'nowrap',
}

function StatusBadge({ connected }: { connected: boolean }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      fontSize: 12,
      fontWeight: 600,
      padding: '3px 10px',
      borderRadius: 999,
      background: connected ? '#4ade8022' : 'var(--surface-raised)',
      color: connected ? '#4ade80' : 'var(--ink-faint)',
      border: `1px solid ${connected ? '#4ade8055' : 'var(--surface-rule)'}`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: connected ? '#4ade80' : 'var(--ink-faint)',
        display: 'inline-block',
      }} />
      {connected ? 'Connected' : 'Disconnected'}
    </span>
  )
}

// ── Webull Panel ─────────────────────────────────────────────────────────────

function WebullLoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pin, setPin] = useState('')
  const [mfaCode, setMfaCode] = useState('')
  const [error, setError] = useState('')
  const [needsMfa, setNeedsMfa] = useState(false)

  const requestMfa = useMutation({
    mutationFn: () => api.post('/webull/request-mfa'),
  })

  const loginMut = useMutation({
    mutationFn: (body: object) => api.post('/webull/login', body),
    onSuccess: (res) => {
      if (res.data?.needs_mfa) {
        setNeedsMfa(true)
        setError('')
      } else if (res.data?.success) {
        onSuccess()
      } else {
        setError(res.data?.error ?? 'Login failed')
      }
    },
    onError: (e: Error) => setError(e.message),
  })

  function handleLogin() {
    setError('')
    loginMut.mutate({ username: email, password, pin: pin || undefined, mfa_code: mfaCode || undefined })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 380 }}>
      <div>
        <div style={label11}>Email / Phone</div>
        <input style={inputStyle} type="text" placeholder="email@example.com"
          value={email} onChange={e => setEmail(e.target.value)} />
      </div>
      <div>
        <div style={label11}>Password</div>
        <input style={inputStyle} type="password" placeholder="••••••••"
          value={password} onChange={e => setPassword(e.target.value)} />
      </div>
      <div>
        <div style={label11}>Trading PIN (optional)</div>
        <input style={inputStyle} type="password" placeholder="6-digit PIN"
          value={pin} onChange={e => setPin(e.target.value)} />
      </div>
      <div>
        <div style={label11}>MFA Code</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input style={{ ...inputStyle, flex: 1 }} type="text" placeholder="6-digit code"
            value={mfaCode} onChange={e => setMfaCode(e.target.value)} />
          <button style={btnSecondary}
            onClick={() => requestMfa.mutate()}
            disabled={requestMfa.isPending}>
            {requestMfa.isPending ? 'Sending…' : 'Send Code'}
          </button>
        </div>
        {requestMfa.isSuccess && (
          <div style={{ fontSize: 11, color: '#4ade80', marginTop: 4 }}>MFA code sent.</div>
        )}
      </div>
      {needsMfa && (
        <div style={{ fontSize: 12, color: '#fb923c', background: '#fb923c11',
          borderRadius: 6, padding: '8px 10px' }}>
          MFA required — enter code above then click Connect again.
        </div>
      )}
      {error && (
        <div style={{ fontSize: 12, color: '#f87171', background: '#f8717111',
          borderRadius: 6, padding: '8px 10px' }}>
          {error}
        </div>
      )}
      <button style={btnPrimary} onClick={handleLogin} disabled={loginMut.isPending}>
        {loginMut.isPending ? 'Connecting…' : 'Connect'}
      </button>
    </div>
  )
}

function WebullAccountCards({ account }: { account: WebullAccount }) {
  const metrics = [
    { label: 'Net Liquidation', value: fmt$(account.net_liq) },
    { label: 'Buying Power', value: fmt$(account.buying_power) },
    { label: 'Cash', value: fmt$(account.cash) },
    { label: 'Unrealized P&L', value: fmt$(account.unrealized_pnl), pnl: account.unrealized_pnl },
  ]
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
      {metrics.map(m => (
        <div key={m.label} style={{ ...card, padding: 16 }}>
          <div style={label11}>{m.label}</div>
          <div style={{
            fontSize: 20, fontWeight: 700,
            color: m.pnl != null ? pnlColor(m.pnl) : 'var(--ink)',
            fontFamily: 'var(--font-mono)',
          }}>{m.value}</div>
        </div>
      ))}
    </div>
  )
}

function WebullPinCard({ onUnlocked }: { onUnlocked: () => void }) {
  const [pinVal, setPinVal] = useState('')
  const [error, setError] = useState('')

  const pinMut = useMutation({
    mutationFn: () => api.post('/webull/trade-pin', { pin: pinVal }),
    onSuccess: (res) => {
      if (res.data?.success) { onUnlocked() }
      else setError(res.data?.error ?? 'PIN rejected')
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>
        Unlock Trading PIN
      </div>
      <div style={{ display: 'flex', gap: 8, maxWidth: 320 }}>
        <input style={{ ...inputStyle, flex: 1 }} type="password" placeholder="6-digit PIN"
          value={pinVal} onChange={e => setPinVal(e.target.value)} />
        <button style={btnPrimary} onClick={() => { setError(''); pinMut.mutate() }}
          disabled={pinMut.isPending}>
          {pinMut.isPending ? 'Unlocking…' : 'Unlock'}
        </button>
      </div>
      {error && (
        <div style={{ fontSize: 12, color: '#f87171', marginTop: 8 }}>{error}</div>
      )}
    </div>
  )
}

function WebullPositions() {
  const q = useQuery<{ positions: WebullPosition[] }>({
    queryKey: ['webull', 'positions'],
    queryFn: () => api.get('/webull/positions').then(r => r.data),
    refetchInterval: 30_000,
  })

  if (q.isLoading) return <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading positions…</div>
  if (q.isError)   return <div style={{ fontSize: 12, color: '#f87171' }}>Failed to load positions.</div>

  const positions = q.data?.positions ?? []

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 14 }}>
        Positions ({positions.length})
      </div>
      {positions.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No open positions.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol','Qty','Cost','Last','Mkt Value','Unr. P&L','Unr. %',''].map(h => (
                  <th key={h} style={tblHead}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.symbol}>
                  <td style={{ ...tblCell, fontWeight: 600 }}>{p.symbol}</td>
                  <td style={tblCell}>{p.quantity}</td>
                  <td style={tblCell}>{fmt$(p.cost_basis)}</td>
                  <td style={tblCell}>{fmt$(p.last_price)}</td>
                  <td style={tblCell}>{fmt$(p.market_value)}</td>
                  <td style={{ ...tblCell, color: pnlColor(p.unrealized_pnl) }}>{fmt$(p.unrealized_pnl)}</td>
                  <td style={{ ...tblCell, color: pnlColor(p.unrealized_pnl_pct) }}>{fmtPct(p.unrealized_pnl_pct)}</td>
                  <td style={tblCell}></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function WebullOrders() {
  const [statusFilter, setStatusFilter] = useState('Working')

  const q = useQuery<{ orders: WebullOrder[] }>({
    queryKey: ['webull', 'orders', statusFilter],
    queryFn: () => api.get(`/webull/orders?status=${statusFilter}`).then(r => r.data),
    refetchInterval: 15_000,
  })

  const orders = q.data?.orders ?? []

  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Orders</div>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
          style={{ ...inputStyle, width: 'auto', padding: '4px 8px', fontSize: 12 }}>
          <option value="Working">Working</option>
          <option value="Filled">Filled</option>
          <option value="Cancelled">Cancelled</option>
          <option value="All">All</option>
        </select>
      </div>
      {q.isLoading ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading orders…</div>
      ) : orders.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No orders.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol','Action','Type','Qty','Filled','Price','Avg Fill','Status','Time',''].map(h => (
                  <th key={h} style={tblHead}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={i}>
                  <td style={{ ...tblCell, fontWeight: 600 }}>{o.symbol}</td>
                  <td style={{ ...tblCell, color: o.action === 'BUY' ? '#4ade80' : '#f87171' }}>{o.action}</td>
                  <td style={tblCell}>{o.order_type}</td>
                  <td style={tblCell}>{o.quantity}</td>
                  <td style={tblCell}>{o.filled_qty}</td>
                  <td style={tblCell}>{o.price ? fmt$(o.price) : '—'}</td>
                  <td style={tblCell}>{o.avg_fill ? fmt$(o.avg_fill) : '—'}</td>
                  <td style={tblCell}>
                    <span style={{
                      fontSize: 11, fontWeight: 600, padding: '2px 7px', borderRadius: 999,
                      background: o.status === 'Filled' ? '#4ade8022' : 'var(--surface-raised)',
                      color: o.status === 'Filled' ? '#4ade80'
                        : o.status === 'Cancelled' ? '#f87171' : 'var(--ink-faint)',
                    }}>{o.status}</span>
                  </td>
                  <td style={{ ...tblCell, fontSize: 11, color: 'var(--ink-faint)' }}>{o.create_time}</td>
                  <td style={tblCell}></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Order form schema ────────────────────────────────────────────────────────

const OrderSchema = z.object({
  symbol:     z.string().min(1, 'Required').max(5).regex(/^[A-Z]+$/, 'Uppercase only'),
  action:     z.enum(['BUY', 'SELL']),
  order_type: z.enum(['Market', 'Limit']),
  qty:        z.string().min(1, 'Required').refine(v => {
    const n = Number(v)
    return Number.isInteger(n) && n > 0 && n <= 10_000
  }, 'Must be integer 1–10,000'),
  price:      z.string().optional(),
  tif:        z.enum(['GTC', 'Day']),
})
type OrderForm = z.infer<typeof OrderSchema>

function WebullPlaceOrder() {
  const [serverError, setServerError] = useState('')
  const [success, setSuccess]         = useState('')

  const {
    register,
    handleSubmit,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<OrderForm>({
    resolver: zodResolver(OrderSchema),
    defaultValues: { action: 'BUY', order_type: 'Market', tif: 'GTC' },
  })

  const orderType = watch('order_type')

  const orderMut = useMutation({
    mutationFn: (body: object) => api.post('/webull/orders', body),
    onSuccess: (res) => {
      if (res.data?.success !== false) {
        setSuccess('Order placed.')
        setServerError('')
        reset()
      } else {
        setServerError(res.data?.error ?? 'Order failed')
        setSuccess('')
      }
    },
    onError: (e: Error) => { setServerError(e.message); setSuccess('') },
  })

  function onSubmit(values: OrderForm) {
    setServerError(''); setSuccess('')
    const body: Record<string, unknown> = {
      ticker:        values.symbol.toUpperCase(),
      action:        values.action,
      order_type:    values.order_type === 'Limit' ? 'LMT' : 'MKT',
      qty:           Number(values.qty),
      time_in_force: values.tif,
    }
    if (values.order_type === 'Limit' && values.price) {
      const p = Number(values.price)
      if (p > 0) body.price = p
    }
    orderMut.mutate(body)
  }

  const fieldErr: React.CSSProperties = { fontSize: 11, color: '#f87171', marginTop: 3 }

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 14 }}>Place Order</div>
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, maxWidth: 700 }}>
          <div>
            <div style={label11}>Symbol</div>
            <input
              style={inputStyle}
              type="text"
              placeholder="AAPL"
              {...register('symbol')}
            />
            {errors.symbol && <div style={fieldErr}>{errors.symbol.message}</div>}
          </div>
          <div>
            <div style={label11}>Action</div>
            <select style={inputStyle} {...register('action')}>
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
            </select>
            {errors.action && <div style={fieldErr}>{errors.action.message}</div>}
          </div>
          <div>
            <div style={label11}>Order Type</div>
            <select style={inputStyle} {...register('order_type')}>
              <option value="Market">Market</option>
              <option value="Limit">Limit</option>
            </select>
            {errors.order_type && <div style={fieldErr}>{errors.order_type.message}</div>}
          </div>
          <div>
            <div style={label11}>Quantity</div>
            <input
              style={inputStyle}
              type="number"
              min="1"
              placeholder="10"
              {...register('qty')}
            />
            {errors.qty && <div style={fieldErr}>{errors.qty.message}</div>}
          </div>
          {orderType === 'Limit' && (
            <div>
              <div style={label11}>Limit Price</div>
              <input
                style={inputStyle}
                type="number"
                step="0.01"
                placeholder="150.00"
                {...register('price')}
              />
              {errors.price && <div style={fieldErr}>{errors.price.message}</div>}
            </div>
          )}
          <div>
            <div style={label11}>Time in Force</div>
            <select style={inputStyle} {...register('tif')}>
              <option value="GTC">GTC</option>
              <option value="Day">Day</option>
            </select>
            {errors.tif && <div style={fieldErr}>{errors.tif.message}</div>}
          </div>
        </div>
        <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
          <button style={btnPrimary} type="submit" disabled={isSubmitting || orderMut.isPending}>
            {isSubmitting || orderMut.isPending ? 'Placing…' : 'Place Order'}
          </button>
          {serverError && <span style={{ fontSize: 12, color: '#f87171' }}>{serverError}</span>}
          {success     && <span style={{ fontSize: 12, color: '#4ade80' }}>{success}</span>}
        </div>
      </form>
    </div>
  )
}

function WebullPanel() {
  const qc = useQueryClient()
  const [pinUnlocked, setPinUnlocked] = useState(false)

  const statusQ = useQuery<WebullStatus>({
    queryKey: ['webull', 'status'],
    queryFn: () => api.get('/webull/status').then(r => r.data),
    refetchInterval: 60_000,
  })

  const accountQ = useQuery<WebullAccount>({
    queryKey: ['webull', 'account'],
    queryFn: () => api.get('/webull/account').then(r => r.data),
    enabled: statusQ.data?.connected === true,
    refetchInterval: 30_000,
  })

  const logoutMut = useMutation({
    mutationFn: () => api.post('/webull/logout'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['webull'] }); setPinUnlocked(false) },
  })

  const refreshMut = useMutation({
    mutationFn: () => api.post('/webull/refresh'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['webull'] }),
  })

  const status = statusQ.data
  const connected = status?.connected ?? false
  const needsPinSetup = connected && status?.has_trade_pin === false && !pinUnlocked

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Connection Card */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Webull Connection</div>
          <StatusBadge connected={connected} />
        </div>

        {!connected ? (
          <WebullLoginForm onSuccess={() => { qc.invalidateQueries({ queryKey: ['webull'] }) }} />
        ) : (
          <>
            {accountQ.data && <WebullAccountCards account={accountQ.data} />}
            <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center', flexWrap: 'wrap' }}>
              <button style={btnSecondary} onClick={() => logoutMut.mutate()} disabled={logoutMut.isPending}>
                {logoutMut.isPending ? 'Disconnecting…' : 'Disconnect'}
              </button>
              <button style={btnSecondary} onClick={() => refreshMut.mutate()} disabled={refreshMut.isPending}>
                {refreshMut.isPending ? 'Refreshing…' : 'Refresh Token'}
              </button>
              {(accountQ.data?.account_id || status?.account_id) && (
                <span style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 8 }}>
                  ID: {accountQ.data?.account_id ?? status?.account_id}
                  {(accountQ.data?.token_expires ?? status?.token_expires) && (
                    <> · Expires: {accountQ.data?.token_expires ?? status?.token_expires}</>
                  )}
                </span>
              )}
            </div>
          </>
        )}
      </div>

      {/* PIN Unlock */}
      {needsPinSetup && (
        <WebullPinCard onUnlocked={() => setPinUnlocked(true)} />
      )}

      {/* Positions */}
      {connected && <WebullPositions />}

      {/* Orders */}
      {connected && <WebullOrders />}

      {/* Place Order */}
      {connected && (pinUnlocked || status?.has_trade_pin !== false) && (
        <WebullPlaceOrder />
      )}
    </div>
  )
}

// ── Fidelity Panel ───────────────────────────────────────────────────────────

type FiLoginStep = 'idle' | 'connecting' | 'need_totp' | 'authenticated' | 'error'

function FidelityLoginForm({ onConnected }: { onConnected: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp]         = useState('')
  const [step, setStep]         = useState<FiLoginStep>('idle')
  const [logs, setLogs]         = useState<string[]>([])
  const [errMsg, setErrMsg]     = useState('')
  const wsRef = useRef<WebSocket | null>(null)
  const logsEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const addLog = useCallback((msg: string) => {
    setLogs(prev => [...prev, msg])
  }, [])

  function connect() {
    if (!username.trim() || !password) return
    setStep('connecting')
    setLogs([])
    setErrMsg('')

    const ws = new WebSocket(wsUrl('/ws/fidelity-auth'))
    wsRef.current = ws

    ws.onopen = () => {
      ws.send(JSON.stringify({ username: username.trim(), password }))
    }
    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data) as { step: string; message?: string; prompt?: string }
      if (msg.message) addLog(msg.message)
      if (msg.step === 'need_totp') {
        setStep('need_totp')
      } else if (msg.step === 'authenticated') {
        setStep('authenticated')
        ws.close()
        onConnected()
      } else if (msg.step === 'error') {
        setStep('error')
        setErrMsg(msg.message ?? 'Login failed')
        ws.close()
      }
    }
    ws.onerror = () => { setStep('error'); setErrMsg('WebSocket error — check server logs') }
    ws.onclose = () => { if (step === 'connecting') setStep('idle') }
  }

  function submitTotp() {
    if (!totp.trim() || !wsRef.current) return
    wsRef.current.send(JSON.stringify({ totp: totp.trim() }))
    setStep('connecting')
    addLog('Submitting verification code…')
  }

  const busy = step === 'connecting'

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32, background: 'var(--surface-soft)', flex: 1 }}>
      <div style={{ ...card, width: '100%', maxWidth: 400, padding: 32 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--ink)', marginBottom: 6 }}>Connect to Fidelity</div>
        <div style={{ fontSize: 13, color: 'var(--ink-faint)', marginBottom: 24, lineHeight: 1.5 }}>
          Credentials used only by this local server via headless browser. Never sent externally.
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <div style={label11}>Fidelity Username</div>
            <input className="input" style={inputStyle} placeholder="username"
              value={username} onChange={e => setUsername(e.target.value)}
              disabled={busy} autoComplete="username" />
          </div>
          <div>
            <div style={label11}>Password</div>
            <input className="input" style={inputStyle} type="password" placeholder="••••••••"
              value={password} onChange={e => setPassword(e.target.value)}
              disabled={busy} autoComplete="current-password"
              onKeyDown={e => { if (e.key === 'Enter' && step === 'idle') connect() }} />
          </div>
        </div>

        {step !== 'need_totp' && (
          <button style={{ ...btnPrimary, width: '100%', marginTop: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
            onClick={connect} disabled={busy || !username || !password}>
            {busy ? 'Connecting…' : 'Connect to Fidelity'}
          </button>
        )}

        {/* TOTP */}
        {step === 'need_totp' && (
          <div style={{ marginTop: 20, padding: 16, background: 'rgba(245,158,11,.06)', border: '1px solid rgba(245,158,11,.22)', borderRadius: 6 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#f59e0b', marginBottom: 6 }}>Two-Factor Authentication</div>
            <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 12 }}>Enter the 6-digit code from your authenticator or SMS.</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="input" style={{ ...inputStyle, textAlign: 'center', letterSpacing: '.25em', fontSize: 20, fontFamily: 'monospace', width: 155, flexShrink: 0 }}
                placeholder="000000" maxLength={8} inputMode="numeric"
                value={totp} onChange={e => setTotp(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') submitTotp() }}
                autoFocus />
              <button style={{ ...btnPrimary, flex: 1 }} onClick={submitTotp}>Submit</button>
            </div>
          </div>
        )}

        {/* Status log */}
        {logs.length > 0 && (
          <div style={{ marginTop: 16, padding: 12, background: 'var(--surface-soft)', borderRadius: 4, border: '1px solid var(--surface-rule)' }}>
            <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--ink-muted)', lineHeight: 1.7, maxHeight: 110, overflowY: 'auto' }}>
              {logs.map((l, i) => <div key={i}>{l}</div>)}
              <div ref={logsEndRef} />
            </div>
          </div>
        )}

        {errMsg && (
          <div style={{ fontSize: 12, color: '#ef4444', marginTop: 12, padding: '8px 12px', background: 'rgba(239,68,68,.08)', border: '1px solid rgba(239,68,68,.2)', borderRadius: 4 }}>
            {errMsg}
          </div>
        )}
      </div>
    </div>
  )
}

function FidelityTradingPanel({ onDisconnect }: { onDisconnect: () => void }) {
  const qc = useQueryClient()
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [interval, setInterval]   = useState('D')
  const [chartStyle, setChartStyle] = useState('1')
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)

  const posQ = useQuery<{ positions: FidelityPosition[]; grand_totals: FidelitySummary }>({
    queryKey: ['fidelity', 'positions'],
    queryFn: () => api.get('/fidelity/positions').then(r => r.data),
    refetchInterval: 60_000,
  })

  useEffect(() => {
    if (posQ.data) setRefreshedAt(new Date())
  }, [posQ.data])

  const fidStatusQ = useQuery<FidelityStatus>({
    queryKey: ['fidelity', 'status'],
    queryFn: () => api.get('/fidelity/status').then(r => r.data),
    refetchInterval: 60_000,
    retry: false,
  })

  const logoutMut = useMutation({
    mutationFn: () => api.post('/fidelity/logout'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['fidelity'] }); onDisconnect() },
  })

  const positions: FidelityPosition[] = posQ.data?.positions ?? []
  const grand = posQ.data?.grand_totals
  const fidConnected = fidStatusQ.data?.connected ?? true

  function gainColor(s?: string | null): string {
    if (!s) return 'var(--ink-muted)'
    return s.startsWith('-') ? '#f87171' : '#4ade80'
  }

  const tvSrc = selectedSymbol
    ? `https://www.tradingview.com/widgetembed/?symbol=${encodeURIComponent(selectedSymbol)}&interval=${interval}&style=${chartStyle}&theme=dark&locale=en&hide_top_toolbar=0&save_image=0&hide_legend=0&details=1&hotlist=0&calendar=0`
    : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', minHeight: 0 }}>
      {/* Stats bar */}
      <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--surface-rule)', padding: '10px 20px', display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0, minHeight: 54 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingRight: 20, borderRight: '1px solid var(--surface-rule)', marginRight: 20, flexShrink: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>Fidelity</div>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999,
            display: 'inline-flex', alignItems: 'center', gap: 5,
            background: fidConnected ? 'rgba(74,222,128,0.15)' : 'rgba(248,113,113,0.15)',
            color: fidConnected ? '#4ade80' : '#f87171',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: fidConnected ? '#4ade80' : '#f87171', display: 'inline-block' }} />
            {fidConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        {grand?.total_value && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 36, flex: 1 }}>
            <div>
              <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700, marginBottom: 2 }}>Portfolio Value</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)', lineHeight: 1.1, fontVariantNumeric: 'tabular-nums' }}>{grand.total_value}</div>
            </div>
            {grand.daily_change && (
              <div>
                <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700, marginBottom: 2 }}>Day P&L</div>
                <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.1, fontVariantNumeric: 'tabular-nums', color: gainColor(grand.daily_change) }}>
                  {grand.daily_change}{grand.daily_change_pct ? <span style={{ fontSize: 13, marginLeft: 6 }}>{grand.daily_change_pct}</span> : null}
                </div>
              </div>
            )}
            <div>
              <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700, marginBottom: 2 }}>Positions</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)', lineHeight: 1.1 }}>{positions.length}</div>
            </div>
          </div>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
          <button style={btnSecondary} onClick={() => qc.invalidateQueries({ queryKey: ['fidelity'] })}>↻ Refresh</button>
          <button style={{ ...btnSecondary, color: '#ef4444' }} onClick={() => logoutMut.mutate()} disabled={logoutMut.isPending}>
            {logoutMut.isPending ? 'Disconnecting…' : 'Disconnect'}
          </button>
        </div>
      </div>

      {/* Body: holdings sidebar + chart */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
        {/* Holdings sidebar */}
        <div style={{ width: 288, borderRight: '1px solid var(--surface-rule)', display: 'flex', flexDirection: 'column', flexShrink: 0, background: 'var(--surface)' }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Holdings</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {refreshedAt && (
                <span style={{ fontSize: 10, color: 'var(--ink-faint)' }}>
                  {refreshedAt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}
                </span>
              )}
              <button onClick={() => qc.invalidateQueries({ queryKey: ['fidelity', 'positions'] })}
                style={{ fontSize: 14, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700, lineHeight: 1, padding: 0 }}
                title="Refresh positions">↻ Refresh</button>
            </div>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {posQ.isLoading ? (
              <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--ink-faint)' }}>Loading positions…</div>
            ) : positions.length === 0 ? (
              <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13 }}>No positions loaded</div>
            ) : positions.map(p => (
              <button key={p.symbol}
                onClick={() => setSelectedSymbol(p.symbol)}
                style={{
                  width: '100%', textAlign: 'left', background: selectedSymbol === p.symbol ? 'var(--surface-raised)' : 'transparent',
                  border: selectedSymbol === p.symbol ? '1px solid var(--surface-rule)' : '1px solid transparent',
                  borderRadius: 6, padding: '7px 10px', cursor: 'pointer', transition: 'background .1s',
                }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{p.symbol}</div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{p.market_value ?? '—'}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 2 }}>
                  <div style={{ fontSize: 10, color: 'var(--ink-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 130 }}>
                    {p.description ?? ''}{p.qty ? ` · ${p.qty} shares` : ''}
                  </div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: gainColor(p.today_gain_loss), flexShrink: 0 }}>
                    {p.today_gain_loss ?? ''}{p.today_gain_pct ? <span style={{ fontSize: 10, marginLeft: 3 }}>{p.today_gain_pct}</span> : null}
                  </div>
                </div>
              </button>
            ))}
          </div>
          {/* Positions table with Day P&L column */}
          {positions.length > 0 && (
            <div style={{ borderTop: '1px solid var(--surface-rule)', flexShrink: 0, overflowX: 'auto', maxHeight: 220, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 260 }}>
                <thead>
                  <tr>
                    {['Symbol', 'Mkt Val', 'Day P&L'].map(h => (
                      <th key={h} style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.04em', padding: '6px 8px', borderBottom: '1px solid var(--surface-rule)', textAlign: 'left', whiteSpace: 'nowrap', background: 'var(--surface)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => (
                    <tr key={p.symbol} style={{ cursor: 'pointer' }} onClick={() => setSelectedSymbol(p.symbol)}>
                      <td style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink)', padding: '5px 8px', borderBottom: '1px solid var(--surface-rule)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{p.symbol}</td>
                      <td style={{ fontSize: 11, color: 'var(--ink)', padding: '5px 8px', borderBottom: '1px solid var(--surface-rule)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{p.market_value ?? '—'}</td>
                      <td style={{ fontSize: 11, fontWeight: 600, padding: '5px 8px', borderBottom: '1px solid var(--surface-rule)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', color: gainColor(p.today_gain_loss) }}>
                        {p.today_gain_loss ?? '—'}
                        {p.today_gain_pct ? <span style={{ fontSize: 10, marginLeft: 3 }}>{p.today_gain_pct}</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Chart area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {/* Chart toolbar */}
          <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flex: 1, minWidth: 0, overflow: 'hidden' }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', flexShrink: 0 }}>
                {selectedSymbol ?? '—'}
              </div>
              {selectedSymbol && positions.find(p => p.symbol === selectedSymbol)?.description && (
                <div style={{ fontSize: 12, color: 'var(--ink-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {positions.find(p => p.symbol === selectedSymbol)!.description}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
              <select value={interval} onChange={e => setInterval(e.target.value)}
                style={{ ...inputStyle, width: 80, fontSize: 12, padding: '4px 8px', height: 30 }}>
                <option value="1">1 min</option>
                <option value="5">5 min</option>
                <option value="15">15 min</option>
                <option value="60">1 hour</option>
                <option value="D">Daily</option>
                <option value="W">Weekly</option>
              </select>
              <select value={chartStyle} onChange={e => setChartStyle(e.target.value)}
                style={{ ...inputStyle, width: 108, fontSize: 12, padding: '4px 8px', height: 30 }}>
                <option value="1">Candles</option>
                <option value="0">Bars</option>
                <option value="2">Line</option>
                <option value="3">Area</option>
                <option value="8">Heikin Ashi</option>
              </select>
            </div>
          </div>
          {/* Chart fills remaining space */}
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', position: 'relative', background: 'var(--surface-soft)' }}>
            {tvSrc ? (
              <iframe
                key={tvSrc}
                src={tvSrc}
                style={{ width: '100%', height: '100%', border: 'none' }}
                allow="fullscreen"
                title={`TradingView chart for ${selectedSymbol}`}
              />
            ) : (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, color: 'var(--ink-faint)', pointerEvents: 'none' }}>
                <div style={{ fontSize: 48, opacity: .2 }}>📈</div>
                <div style={{ fontSize: 14 }}>Select a holding to view chart</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function FidelityPanel() {
  const qc = useQueryClient()
  const [forceLogin, setForceLogin] = useState(false)

  const statusQ = useQuery<FidelityStatus>({
    queryKey: ['fidelity', 'status'],
    queryFn: () => api.get('/fidelity/status').then(r => r.data),
    refetchInterval: 60_000,
    retry: 1,
  })

  const connected = statusQ.data?.connected ?? false
  const showLogin = !connected || forceLogin

  function handleConnected() {
    setForceLogin(false)
    qc.invalidateQueries({ queryKey: ['fidelity'] })
  }

  if (statusQ.isLoading) {
    return <div style={{ padding: 40, fontSize: 13, color: 'var(--ink-faint)' }}>Checking Fidelity session…</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', minHeight: 0, margin: -24 }}>
      {showLogin
        ? <FidelityLoginForm onConnected={handleConnected} />
        : <FidelityTradingPanel onDisconnect={() => setForceLogin(true)} />
      }
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

const BROKERS = [
  { id: 'webull',   label: 'Webull' },
  { id: 'fidelity', label: 'Fidelity' },
] as const

type BrokerId = typeof BROKERS[number]['id']

export default function BrokerPage() {
  const [active, setActive] = useState<BrokerId>('fidelity')

  return (
    <div id="panel-broker" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Broker switcher bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '10px 20px',
        background: 'var(--surface)',
        borderBottom: '1px solid var(--surface-rule)',
        flexShrink: 0,
      }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Broker</span>
        {BROKERS.map(b => (
          <button
            key={b.id}
            onClick={() => setActive(b.id)}
            style={{
              padding: '5px 16px',
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 999,
              border: active === b.id ? '1px solid var(--accent)' : '1px solid var(--surface-rule)',
              cursor: 'pointer',
              background: active === b.id ? 'rgba(214,58,0,.1)' : 'transparent',
              color: active === b.id ? 'var(--accent)' : 'var(--ink-faint)',
              transition: 'all .15s',
            }}
          >
            {b.label}
          </button>
        ))}
      </div>

      {/* Panel — Fidelity gets full-height layout, Webull gets scrollable */}
      {active === 'fidelity' && <FidelityPanel />}
      {active === 'webull' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          <WebullPanel />
        </div>
      )}
    </div>
  )
}
