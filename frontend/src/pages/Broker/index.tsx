import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'

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
  account_id?: string
  accounts?: unknown[]
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

function WebullPlaceOrder() {
  const [symbol, setSymbol] = useState('')
  const [action, setAction] = useState('BUY')
  const [orderType, setOrderType] = useState('Market')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [tif, setTif] = useState('GTC')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const orderMut = useMutation({
    mutationFn: (body: object) => api.post('/webull/order', body),
    onSuccess: (res) => {
      if (res.data?.success !== false) {
        setSuccess('Order placed.')
        setSymbol(''); setQty(''); setPrice('')
      } else {
        setError(res.data?.error ?? 'Order failed')
      }
    },
    onError: (e: Error) => setError(e.message),
  })

  function handlePlace() {
    setError(''); setSuccess('')
    if (!symbol || !qty) { setError('Symbol and quantity required.'); return }
    const body: Record<string, unknown> = { symbol: symbol.toUpperCase(), action, order_type: orderType, qty: Number(qty), tif }
    if (orderType === 'Limit') {
      if (!price) { setError('Limit price required.'); return }
      body.price = Number(price)
    }
    orderMut.mutate(body)
  }

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 14 }}>Place Order</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, maxWidth: 700 }}>
        <div>
          <div style={label11}>Symbol</div>
          <input style={inputStyle} type="text" placeholder="AAPL"
            value={symbol} onChange={e => setSymbol(e.target.value)} />
        </div>
        <div>
          <div style={label11}>Action</div>
          <select style={inputStyle} value={action} onChange={e => setAction(e.target.value)}>
            <option value="BUY">Buy</option>
            <option value="SELL">Sell</option>
          </select>
        </div>
        <div>
          <div style={label11}>Order Type</div>
          <select style={inputStyle} value={orderType} onChange={e => setOrderType(e.target.value)}>
            <option value="Market">Market</option>
            <option value="Limit">Limit</option>
          </select>
        </div>
        <div>
          <div style={label11}>Quantity</div>
          <input style={inputStyle} type="number" min="1" placeholder="10"
            value={qty} onChange={e => setQty(e.target.value)} />
        </div>
        {orderType === 'Limit' && (
          <div>
            <div style={label11}>Limit Price</div>
            <input style={inputStyle} type="number" step="0.01" placeholder="150.00"
              value={price} onChange={e => setPrice(e.target.value)} />
          </div>
        )}
        <div>
          <div style={label11}>Time in Force</div>
          <select style={inputStyle} value={tif} onChange={e => setTif(e.target.value)}>
            <option value="GTC">GTC</option>
            <option value="Day">Day</option>
          </select>
        </div>
      </div>
      <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
        <button style={btnPrimary} onClick={handlePlace} disabled={orderMut.isPending}>
          {orderMut.isPending ? 'Placing…' : 'Place Order'}
        </button>
        {error && <span style={{ fontSize: 12, color: '#f87171' }}>{error}</span>}
        {success && <span style={{ fontSize: 12, color: '#4ade80' }}>{success}</span>}
      </div>
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

interface FidelityAccount {
  account_id: string
  name?: string
  balance?: number
  buying_power?: number
  unrealized_pnl?: number
}

function FidelityPanel() {
  const qc = useQueryClient()

  const statusQ = useQuery<FidelityStatus>({
    queryKey: ['fidelity', 'status'],
    queryFn: () => api.get('/fidelity/status').then(r => r.data),
    refetchInterval: 60_000,
    retry: 1,
  })

  const status = statusQ.data
  const connected = status?.connected ?? false
  const accounts: FidelityAccount[] = (status?.accounts as FidelityAccount[]) ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Connection Card */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Fidelity OAuth Connection</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
              Connects via Fidelity's OAuth 2.0 authorization flow.
            </div>
          </div>
          <StatusBadge connected={connected} />
        </div>

        {statusQ.isLoading && (
          <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Checking status…</div>
        )}

        {!connected && !statusQ.isLoading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{
              background: 'var(--surface-raised)',
              borderRadius: 8,
              padding: 14,
              fontSize: 12,
              color: 'var(--ink-faint)',
              lineHeight: 1.6,
              maxWidth: 480,
            }}>
              <div style={{ fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>How to connect:</div>
              <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <li>Click <b>Connect Fidelity</b> — you will be redirected to Fidelity's login page.</li>
                <li>Authorize the application for read and trade access.</li>
                <li>You will be redirected back automatically once authorized.</li>
              </ol>
            </div>
            <div>
              <a
                href="/api/fidelity/authorize"
                style={{ ...btnPrimary, textDecoration: 'none', display: 'inline-block' }}
              >
                Connect Fidelity
              </a>
            </div>
          </div>
        )}

        {connected && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {status?.account_id && (
              <div style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                Account ID: {status.account_id}
              </div>
            )}
            {accounts.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
                {accounts.map((acc) => (
                  <div key={acc.account_id} style={{ ...card, padding: 16, background: 'var(--surface-raised)' }}>
                    <div style={label11}>{acc.name ?? acc.account_id}</div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
                      {acc.balance != null ? fmt$(acc.balance) : '—'}
                    </div>
                    {acc.buying_power != null && (
                      <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 4 }}>
                        BP: {fmt$(acc.buying_power)}
                      </div>
                    )}
                    {acc.unrealized_pnl != null && (
                      <div style={{ fontSize: 12, fontWeight: 600, color: pnlColor(acc.unrealized_pnl), marginTop: 4 }}>
                        {fmt$(acc.unrealized_pnl)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button style={btnSecondary}
                onClick={() => api.post('/fidelity/disconnect').then(() => qc.invalidateQueries({ queryKey: ['fidelity'] }))}>
                Disconnect
              </button>
              <button style={btnSecondary}
                onClick={() => qc.invalidateQueries({ queryKey: ['fidelity'] })}>
                Refresh
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Positions */}
      {connected && <FidelityPositions />}

      {/* Orders */}
      {connected && <FidelityOrders />}
    </div>
  )
}

function FidelityPositions() {
  const q = useQuery<{ positions: WebullPosition[] }>({
    queryKey: ['fidelity', 'positions'],
    queryFn: () => api.get('/fidelity/positions').then(r => r.data),
    refetchInterval: 30_000,
  })

  const positions = q.data?.positions ?? []

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 14 }}>
        Positions ({positions.length})
      </div>
      {q.isLoading ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading positions…</div>
      ) : positions.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No open positions.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol','Qty','Cost','Last','Mkt Value','Unr. P&L','Unr. %'].map(h => (
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function FidelityOrders() {
  const q = useQuery<{ orders: WebullOrder[] }>({
    queryKey: ['fidelity', 'orders'],
    queryFn: () => api.get('/fidelity/orders').then(r => r.data),
    refetchInterval: 15_000,
  })

  const orders = q.data?.orders ?? []

  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 14 }}>Orders</div>
      {q.isLoading ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading orders…</div>
      ) : orders.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No orders.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Symbol','Action','Type','Qty','Filled','Price','Avg Fill','Status','Time'].map(h => (
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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
  const [active, setActive] = useState<BrokerId>('webull')

  return (
    <div id="panel-broker" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>Broker</div>
      </div>

      {/* Broker switcher */}
      <div style={{
        display: 'inline-flex',
        background: 'var(--surface-raised)',
        borderRadius: 8,
        padding: 3,
        gap: 2,
        width: 'fit-content',
        border: '1px solid var(--surface-rule)',
      }}>
        {BROKERS.map(b => (
          <button
            key={b.id}
            onClick={() => setActive(b.id)}
            style={{
              padding: '6px 18px',
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 6,
              border: 'none',
              cursor: 'pointer',
              transition: 'background .15s, color .15s',
              background: active === b.id ? 'var(--surface)' : 'transparent',
              color: active === b.id ? 'var(--ink)' : 'var(--ink-faint)',
              boxShadow: active === b.id ? '0 1px 3px rgba(0,0,0,.08)' : 'none',
            }}
          >
            {b.label}
          </button>
        ))}
      </div>

      {/* Panel */}
      {active === 'webull'   && <WebullPanel />}
      {active === 'fidelity' && <FidelityPanel />}
    </div>
  )
}
