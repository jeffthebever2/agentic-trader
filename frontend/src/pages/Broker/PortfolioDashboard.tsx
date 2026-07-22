import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Treemap, ResponsiveContainer, Tooltip } from 'recharts'
import api from '@/api/client'
import { AsyncBoundary, EmptyState, Spinner, Button } from '@/components/ui'

interface Account { account_id: string; name: string; institution: string; account_mask: string }
interface Position {
  symbol: string; quantity: number; price: number; market_value: number; stale: boolean
  avg_cost: number; cost_basis: number; unrealized_pnl: number; unrealized_pct: number
}
interface Balance { cash: number; buying_power: number; total_value: number; currency: string; stale: boolean }

const CASH_TICKERS = new Set(['SPAXX', 'FDRXX', 'FZFXX', 'FCASH', 'FGXX'])
const SERIES = (i: number) => `var(--series-${(i % 8) + 1})`

const money = (n: number | undefined, dp = 2) =>
  n == null ? '—' : '$' + n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
// Compact for billion-scale portfolios: $1.23B / $340.5M above $1M, full below so
// small accounts stay exact. Keeps the figure strip + value column from overflowing.
const smart = (n: number | undefined): string => {
  if (n == null) return '—'
  const a = Math.abs(n)
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B'
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M'
  return money(n)
}
const pct = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%'
const signed = (n: number): string => (n >= 0 ? '+' : '') + smart(n)
const signedPct = (n: number) => (n >= 0 ? '+' : '') + pct(n)
const pnlColor = (n: number) => (n > 0 ? 'var(--success)' : n < 0 ? 'var(--danger)' : 'var(--ink-muted)')

export default function PortfolioDashboard({ onManage }: { onManage: () => void }) {
  const qc = useQueryClient()
  const accountsQ = useQuery<{ accounts: Account[] }>({
    queryKey: ['broker-accounts', 'any'],
    queryFn: () => api.get('/broker/accounts?broker=any').then(r => r.data),
    refetchInterval: 120_000,
  })
  const accounts = accountsQ.data?.accounts ?? []
  const [sel, setSel] = useState<string | null>(null)
  const accountId = sel ?? accounts[0]?.account_id ?? null
  const account = accounts.find(a => a.account_id === accountId) ?? accounts[0]

  const posQ = useQuery<{ positions: Position[] }>({
    queryKey: ['broker-positions', accountId],
    queryFn: () => api.get(`/broker/accounts/${accountId}/positions?broker=any`).then(r => r.data),
    enabled: !!accountId,
    refetchInterval: 60_000,
  })
  const balQ = useQuery<{ balance: Balance }>({
    queryKey: ['broker-balance', accountId],
    queryFn: () => api.get(`/broker/accounts/${accountId}/balances?broker=any`).then(r => r.data),
    enabled: !!accountId,
  })

  const refreshMut = useMutation({
    mutationFn: () => api.post(`/broker/accounts/${accountId}/refresh`).then(r => r.data),
    onSuccess: () => { posQ.refetch(); balQ.refetch(); qc.invalidateQueries({ queryKey: ['broker-accounts', 'any'] }) },
  })

  const positions = useMemo(
    () => (posQ.data?.positions ?? []).slice().sort((a, b) => b.market_value - a.market_value),
    [posQ.data])
  const total = positions.reduce((s, p) => s + (p.market_value || 0), 0)
  const cash = positions.filter(p => CASH_TICKERS.has(p.symbol)).reduce((s, p) => s + p.market_value, 0) || (balQ.data?.balance?.cash ?? 0)
  const invested = total - cash
  // Unrealized P&L across non-cash holdings (cost basis excludes cash funds).
  const equities = positions.filter(p => !CASH_TICKERS.has(p.symbol))
  const totalPnl = equities.reduce((s, p) => s + (p.unrealized_pnl || 0), 0)
  const totalCost = equities.reduce((s, p) => s + (p.cost_basis || 0), 0)
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0
  const stale = positions.some(p => p.stale) || balQ.data?.balance?.stale

  // Treemap data with a resolved series color per holding (rank order).
  const tree = positions.map((p, i) => ({ name: p.symbol, size: p.market_value, fill: SERIES(i), weight: total ? p.market_value / total * 100 : 0 }))

  return (
    <div className="pf-fade" style={{ display: 'flex', flexDirection: 'column', gap: 22, width: '100%', maxWidth: 1180, margin: '0 auto' }}>
      {/* Identity + actions */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800, letterSpacing: '-.02em', color: 'var(--ink)' }}>{account?.name || 'Portfolio'}</h1>
          <span style={{ fontSize: 12.5, color: 'var(--ink-faint)' }}>{account?.institution} · {account?.account_mask}</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {accounts.length > 1 && (
            <select value={accountId ?? ''} onChange={e => setSel(e.target.value)}
              style={{ padding: '6px 10px', fontSize: 12.5, borderRadius: 8, border: '1px solid var(--surface-rule)', background: 'var(--surface)', color: 'var(--ink)' }}>
              {accounts.map(a => <option key={a.account_id} value={a.account_id}>{a.name} · {a.account_mask}</option>)}
            </select>
          )}
          <Button variant="ghost" size="sm" loading={refreshMut.isPending} onClick={() => refreshMut.mutate()}>Refresh</Button>
          <Button variant="ghost" size="sm" onClick={onManage}>Manage</Button>
        </div>
      </div>

      <AsyncBoundary
        data={posQ.data?.positions ?? (accountId ? undefined : [])}
        isLoading={accountsQ.isLoading || posQ.isLoading}
        isError={posQ.isError || accountsQ.isError}
        error={posQ.error || accountsQ.error}
        onRetry={() => { accountsQ.refetch(); posQ.refetch() }}
        emptyTitle="No holdings"
        empty={<EmptyState icon="🏦" title="No holdings yet" description="This account has no positions, or SnapTrade is still syncing (holdings can lag ~24h)." />}
        loading={<div style={{ padding: 48, textAlign: 'center', color: 'var(--ink-muted)' }}><Spinner size={22} /> Loading portfolio…</div>}
      >
        {() => (
          <>
            {/* Figure strip — terminal-style, hairline-separated. Not a hero card. */}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 0, flexWrap: 'wrap' }}>
              <Figure label="Total value" value={smart(total)} lead />
              <Rule />
              <Figure label="Unrealized P&L" value={signed(totalPnl)} color={pnlColor(totalPnl)} sub={signedPct(totalPnlPct)} />
              <Rule />
              <Figure label="Invested" value={smart(invested)} />
              <Rule />
              <Figure label="Cash" value={smart(cash)} />
              <Rule />
              <Figure label="Positions" value={String(equities.length)} />
              {stale && <span style={{ fontSize: 11, color: '#d97706', marginLeft: 'auto', alignSelf: 'center' }}>SnapTrade data can lag ~24h</span>}
            </div>

            {/* Allocation treemap — sized by value, validated categorical fills. */}
            <section>
              <SectionLabel>Allocation by value</SectionLabel>
              <div style={{ height: 300, marginTop: 10 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <Treemap data={tree} dataKey="size" nameKey="name" stroke="var(--canvas)"
                    content={<Tile />} isAnimationActive={false}>
                    <Tooltip content={<TreeTip />} />
                  </Treemap>
                </ResponsiveContainer>
              </div>
            </section>

            {/* Holdings — the precise numbers (also the accessible table view). */}
            <section>
              <SectionLabel>Holdings <span style={{ color: 'var(--ink-faint)', fontWeight: 500 }}>· {positions.length} lines</span></SectionLabel>
              <div style={{ overflowX: 'auto', marginTop: 6 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>
                  <thead>
                    <tr style={{ color: 'var(--ink-faint)', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '.06em' }}>
                      <th style={{ textAlign: 'left', padding: '8px 4px', fontWeight: 700 }}>Symbol</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 700 }}>Qty</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 700 }}>Avg</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 700 }}>Price</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 700 }}>Value</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 700 }}>Unrealized P&L</th>
                      <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 700, width: '24%' }}>Weight</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p, i) => {
                      const w = total > 0 ? (p.market_value / total) * 100 : 0
                      return (
                        <tr key={p.symbol} className="pf-row" style={{ borderTop: '1px solid var(--surface-rule)' }}>
                          <td style={{ padding: '11px 4px' }}>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9 }}>
                              <span style={{ width: 9, height: 9, borderRadius: 3, background: SERIES(i) }} />
                              <span style={{ fontWeight: 700, color: 'var(--ink)', letterSpacing: '.01em' }}>{p.symbol}</span>
                              {CASH_TICKERS.has(p.symbol) && <span style={{ fontSize: 9.5, color: 'var(--ink-faint)', border: '1px solid var(--surface-rule)', borderRadius: 4, padding: '0 4px' }}>CASH</span>}
                            </span>
                          </td>
                          <td style={{ textAlign: 'right', padding: '11px 12px', color: 'var(--ink-muted)' }}>{p.quantity}</td>
                          <td style={{ textAlign: 'right', padding: '11px 12px', color: 'var(--ink-muted)' }}>{p.avg_cost ? money(p.avg_cost) : '—'}</td>
                          <td style={{ textAlign: 'right', padding: '11px 12px', color: 'var(--ink-muted)' }}>{money(p.price)}</td>
                          <td style={{ textAlign: 'right', padding: '11px 12px', color: 'var(--ink)', fontWeight: 600 }} title={money(p.market_value)}>{smart(p.market_value)}</td>
                          <td style={{ textAlign: 'right', padding: '11px 12px', fontWeight: 600 }}>
                            {CASH_TICKERS.has(p.symbol) ? <span style={{ color: 'var(--ink-faint)' }}>—</span> : (
                              <span style={{ color: pnlColor(p.unrealized_pnl) }}>
                                {signed(p.unrealized_pnl)} <span style={{ fontSize: 11, opacity: .85 }}>{signedPct(p.unrealized_pct)}</span>
                              </span>
                            )}
                          </td>
                          <td style={{ padding: '11px 12px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                              <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--surface-raised)', overflow: 'hidden' }}>
                                <div className="pf-bar" style={{ width: `${Math.min(100, w)}%`, height: '100%', background: SERIES(i), borderRadius: 3 }} />
                              </div>
                              <span style={{ fontSize: 11.5, color: 'var(--ink-muted)', minWidth: 44, textAlign: 'right' }}>{pct(w)}</span>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </AsyncBoundary>
    </div>
  )
}

// ── pieces ─────────────────────────────────────────────────────────────────────

function Figure({ label, value, lead, color, sub }: { label: string; value: string; lead?: boolean; color?: string; sub?: string }) {
  return (
    <div style={{ padding: '0 20px 0 0' }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: 3 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: lead ? 30 : 18, fontWeight: lead ? 800 : 600, letterSpacing: lead ? '-.02em' : '0', color: color || 'var(--ink)', fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value}</span>
        {sub && <span style={{ fontSize: 12.5, fontWeight: 700, color: color || 'var(--ink-muted)', fontVariantNumeric: 'tabular-nums' }}>{sub}</span>}
      </div>
    </div>
  )
}
const Rule = () => <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--surface-rule)', margin: '2px 20px 2px 0' }} />
function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.09em', textTransform: 'uppercase', color: 'var(--ink-muted)' }}>{children}</div>
}

// Treemap tile: colored rect + legible symbol/weight when the tile is big enough.
// Color from rank index and weight from root.value so it never depends on recharts
// spreading custom data fields onto the node.
function Tile(props: any) {
  const { x, y, width, height, name, index, value, root } = props
  if (width == null || width <= 0 || height <= 0) return null
  const fill = SERIES(index ?? 0)
  const totalVal = root?.value || 0
  const weight = totalVal > 0 ? (value / totalVal) * 100 : 0
  const big = width > 56 && height > 38
  const mid = width > 40 && height > 24
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} rx={5} ry={5} fill={fill} stroke="var(--canvas)" strokeWidth={2} />
      {mid && (
        <text x={x + 9} y={y + 20} fill="#fff" fontSize={big ? 13 : 11} fontWeight={800}
          style={{ textShadow: '0 1px 3px rgba(0,0,0,.7), 0 0 3px rgba(0,0,0,.5)' }}>{name}</text>
      )}
      {big && (
        <text x={x + 9} y={y + 36} fill="#fff" fontSize={11} fontWeight={600} opacity={0.92}
          style={{ textShadow: '0 1px 3px rgba(0,0,0,.7), 0 0 3px rgba(0,0,0,.5)' }}>{pct(weight)}</text>
      )}
    </g>
  )
}

function TreeTip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload || {}
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: '8px 11px', boxShadow: 'var(--shadow-2)', fontSize: 12.5 }}>
      <div style={{ fontWeight: 800, color: 'var(--ink)' }}>{d.name}</div>
      <div style={{ color: 'var(--ink-muted)', fontVariantNumeric: 'tabular-nums' }}>{smart(d.size)} · {pct(d.weight || 0)}</div>
    </div>
  )
}
