import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '@/api/client'
import { getMlStatus } from '@/api/ml'
import { getPaperStatus } from '@/api/paper'
import { useWebSocket } from '@/hooks/useWebSocket'
import { WS_ML_TRAIN } from '@/api/ws'
import type { WsMessage } from '@/api/ws'
import type { PaperAccount } from '@/types'
import { Badge } from '@/components/ui/Badge'
import { FeatureImportanceBar } from '@/components/charts/FeatureImportanceBar'

// ── Internal types ────────────────────────────────────────────────────────────

interface EodStat {
  strategy: string
  closed_trades?: number
  winning_trades?: number
  losing_trades?: number
  realized_pnl?: number
  return_pct?: number
  avg_trade_pnl?: number
  starting_cash?: number
  best_trade?: { pnl?: number; ticker?: string }
  worst_trade?: { pnl?: number; ticker?: string }
}

interface AccountSummary {
  starting_cash?: number
  cash?: number
  total_value?: number
  realized_pnl?: number
  open_positions?: unknown[]
  gfv_count?: number
  clv_count?: number
  freeriding_count?: number
  pdt_flagged?: boolean
  gfv_restricted?: boolean
  settled_cash?: number
  unsettled_cash?: number
  not_started?: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrColor(wr: number | null): string {
  if (wr === null) return '#6b7280'
  if (wr >= 0.65) return '#4ade80'
  if (wr >= 0.50) return '#facc15'
  return '#f87171'
}

function pnlColor(pnl: number | null | undefined): string {
  if (pnl == null) return 'var(--ink)'
  return pnl >= 0 ? '#4ade80' : '#f87171'
}

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function fmtPnl(n: number | null | undefined): string {
  if (n == null) return '—'
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// ── Win-rate ring ─────────────────────────────────────────────────────────────

function WrRing({ wr }: { wr: number | null }) {
  const pct = wr != null ? Math.round(wr * 100) : 0
  const color = wrColor(wr)
  const deg = pct * 3.6
  return (
    <div style={{
      width: 72, height: 72, borderRadius: '50%',
      background: `conic-gradient(${color} ${deg}deg, var(--surface-rule) ${deg}deg)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
    }}>
      <div style={{
        width: 52, height: 52, borderRadius: '50%',
        background: 'var(--surface)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13, fontWeight: 700, color,
      }}>
        {wr != null ? `${pct}%` : '—'}
      </div>
    </div>
  )
}

// ── Strategy card ─────────────────────────────────────────────────────────────

function StrategyCard({ account, stat }: { account: PaperAccount; stat: EodStat | undefined }) {
  const summary = account.summary as AccountSummary | null
  const openCount = (summary?.open_positions as unknown[])?.length ?? 0
  const closed = stat?.closed_trades ?? 0
  const wins = stat?.winning_trades ?? 0
  const losses = stat?.losing_trades ?? 0
  const wr: number | null = closed > 0 ? wins / closed : null
  const pnl = stat?.realized_pnl ?? summary?.realized_pnl
  const returnPct = stat?.return_pct
  const avgPnl = stat?.avg_trade_pnl
  const totalValue = summary?.total_value
  const isRestricted = summary?.gfv_restricted ?? false
  const hasViolations = (summary?.gfv_count ?? 0) > 0 || (summary?.clv_count ?? 0) > 0 || (summary?.freeriding_count ?? 0) > 0
  const notStarted = summary?.not_started

  return (
    <div style={{
      background: 'var(--surface)', border: `1px solid ${isRestricted ? '#f87171' : 'var(--surface-rule)'}`,
      borderRadius: 8, padding: 20, display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)' }}>{account.label}</div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>
            {openCount} open position{openCount !== 1 ? 's' : ''}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
          {isRestricted && (
            <span style={{ fontSize: 10, fontWeight: 700, color: '#f87171', background: 'rgba(248,113,113,0.12)', padding: '2px 6px', borderRadius: 4 }}>
              RESTRICTED
            </span>
          )}
          {hasViolations && !isRestricted && (
            <span style={{ fontSize: 10, fontWeight: 600, color: '#facc15', background: 'rgba(250,204,21,0.12)', padding: '2px 6px', borderRadius: 4 }}>
              VIOLATIONS
            </span>
          )}
          {notStarted && (
            <span style={{ fontSize: 10, color: 'var(--ink-faint)', background: 'var(--surface-raised)', padding: '2px 6px', borderRadius: 4 }}>
              NOT STARTED
            </span>
          )}
        </div>
      </div>

      {/* Ring + W/L */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <WrRing wr={wr} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 16px' }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Wins</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#4ade80' }}>{wins}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Losses</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#f87171' }}>{losses}</div>
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Total Closed</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{closed}</div>
          </div>
        </div>
      </div>

      {/* P&L row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 12px' }}>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Realized P&L</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: pnlColor(pnl) }}>{fmtPnl(pnl)}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Return</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: pnlColor(returnPct) }}>
            {returnPct != null ? `${returnPct >= 0 ? '+' : ''}${fmt(returnPct)}%` : '—'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Avg Trade P&L</div>
          <div style={{ fontSize: 13, fontWeight: 600, color: pnlColor(avgPnl) }}>{fmtPnl(avgPnl)}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Portfolio Value</div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
            {totalValue != null ? `$${totalValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
          </div>
        </div>
      </div>

      {/* Best / worst */}
      {(stat?.best_trade?.ticker || stat?.worst_trade?.ticker) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px', paddingTop: 8, borderTop: '1px solid var(--surface-rule)' }}>
          {stat?.best_trade?.ticker && (
            <div>
              <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Best Trade</div>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#4ade80' }}>
                {stat.best_trade.ticker}
                {stat.best_trade.pnl != null && <span style={{ marginLeft: 4, fontWeight: 400 }}>({fmtPnl(stat.best_trade.pnl)})</span>}
              </div>
            </div>
          )}
          {stat?.worst_trade?.ticker && (
            <div>
              <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>Worst Trade</div>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#f87171' }}>
                {stat.worst_trade.ticker}
                {stat.worst_trade.pnl != null && <span style={{ marginLeft: 4, fontWeight: 400 }}>({fmtPnl(stat.worst_trade.pnl)})</span>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MLPage() {
  const [trainLog, setTrainLog] = useState('')
  const [training, setTraining] = useState(false)
  const [trainInput, setTrainInput] = useState('')
  const [holdDays, setHoldDays] = useState('5')

  const { data: paperData, isLoading: paperLoading, refetch: refetchPaper } = useQuery({
    queryKey: ['paper', 'status'],
    queryFn: getPaperStatus,
    refetchInterval: 30_000,
  })

  const { data: mlData, isLoading: mlLoading, refetch: refetchMl } = useQuery({
    queryKey: ['ml', 'status'],
    queryFn: getMlStatus,
    refetchInterval: 60_000,
  })

  const { data: mlHistory } = useQuery({
    queryKey: ['ml', 'history'],
    queryFn: () => api.get('/ml/history').then(r => Array.isArray(r.data) ? r.data : []).catch(() => []),
    staleTime: 300_000,
  })

  const { send, close } = useWebSocket(WS_ML_TRAIN, {
    enabled: training,
    onMessage: (msg: WsMessage) => {
      if (msg.type === 'done' || msg.type === 'error') setTraining(false)
      setTrainLog(prev => prev + (msg.text ?? JSON.stringify(msg)) + '\n')
    },
    onClose: () => setTraining(false),
  })

  // ── Derived data ─────────────────────────────────────────────────────────

  const accounts = paperData?.accounts ?? []
  const eodStats: EodStat[] = (paperData?.end_of_day as { statistics?: EodStat[] } | null)?.statistics ?? []

  const statByStrategy = Object.fromEntries(eodStats.map(s => [s.strategy, s]))

  // Aggregate numbers
  const totalClosed = eodStats.reduce((a, s) => a + (s.closed_trades ?? 0), 0)
  const totalWins = eodStats.reduce((a, s) => a + (s.winning_trades ?? 0), 0)
  const totalPnl = eodStats.reduce((a, s) => a + (s.realized_pnl ?? 0), 0)
  const overallWr: number | null = totalClosed > 0 ? totalWins / totalClosed : null

  // Violations
  const gfvCount = accounts.reduce((a, acc) => a + ((acc.summary as AccountSummary | null)?.gfv_count ?? 0), 0)
  const clvCount = accounts.reduce((a, acc) => a + ((acc.summary as AccountSummary | null)?.clv_count ?? 0), 0)
  const freeriding = accounts.reduce((a, acc) => a + ((acc.summary as AccountSummary | null)?.freeriding_count ?? 0), 0)
  const pdtFlagged = accounts.some(acc => (acc.summary as AccountSummary | null)?.pdt_flagged)
  const anyRestricted = accounts.some(acc => (acc.summary as AccountSummary | null)?.gfv_restricted)

  function violationColor(count: number, ban1 = false): string {
    if (ban1) return count > 0 ? '#f87171' : '#4ade80'
    if (count >= 3) return '#f87171'
    if (count > 0) return '#facc15'
    return '#4ade80'
  }

  const isLoading = paperLoading || mlLoading

  function handleRefresh() {
    void refetchPaper()
    void refetchMl()
  }

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div id="panel-ml" style={{ padding: 24, maxWidth: 1100 }}>

      {/* ── 1. Header ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
            Portfolio Statistics
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-muted)', marginTop: 4 }}>
            Live performance across all trading strategies
          </div>
        </div>
        <button
          className="btn-secondary"
          onClick={handleRefresh}
          disabled={isLoading}
          style={{ minWidth: 90 }}
        >
          {isLoading ? 'Loading…' : '↻ Refresh'}
        </button>
      </div>

      {/* ── 2. Aggregate summary strip ── */}
      <div id="stats-aggregate" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 28 }}>
        {/* Overall Win Rate */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Overall Win Rate
          </div>
          <div id="stats-total-wr" style={{ fontSize: 32, fontWeight: 800, color: wrColor(overallWr), lineHeight: 1 }}>
            {overallWr != null ? `${Math.round(overallWr * 100)}%` : '—'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 6 }}>
            {totalWins}W / {totalClosed - totalWins}L across all strategies
          </div>
        </div>

        {/* Total P&L */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Total P&L
          </div>
          <div id="stats-total-pnl" style={{ fontSize: 32, fontWeight: 800, color: pnlColor(totalPnl), lineHeight: 1 }}>
            {fmtPnl(eodStats.length > 0 ? totalPnl : null)}
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 6 }}>
            Realized across {accounts.length} strateg{accounts.length !== 1 ? 'ies' : 'y'}
          </div>
        </div>

        {/* Trades Closed */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 20 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Trades Closed
          </div>
          <div id="stats-total-trades" style={{ fontSize: 32, fontWeight: 800, color: 'var(--ink)', lineHeight: 1 }}>
            {totalClosed}
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 6 }}>
            {eodStats.length > 0 ? `${eodStats.length} active strateg${eodStats.length !== 1 ? 'ies' : 'y'}` : 'No trades recorded yet'}
          </div>
        </div>
      </div>

      {/* ── 3. Per-strategy cards ── */}
      {accounts.length > 0 && (
        <div id="stats-strategy-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16, marginBottom: 28 }}>
          {accounts.map(acc => (
            <StrategyCard
              key={acc.strategy}
              account={acc}
              stat={statByStrategy[acc.strategy]}
            />
          ))}
        </div>
      )}

      {/* ── 4. Violations tracker ── */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 20, marginBottom: 28 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)', marginBottom: 16 }}>
          Trading Restrictions &amp; Violations
        </div>

        {anyRestricted && (
          <div style={{
            background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.4)',
            borderRadius: 6, padding: '10px 14px', marginBottom: 16,
            fontSize: 13, fontWeight: 600, color: '#f87171',
          }}>
            ⚠ Account restricted to settled cash only
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
          {/* GFV */}
          <div style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 14 }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
              Good Faith Violations
            </div>
            <div id="stats-gfv" style={{ fontSize: 28, fontWeight: 800, color: violationColor(gfvCount) }}>
              {gfvCount}
            </div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 4 }}>3 = 90-day ban</div>
          </div>

          {/* CLV */}
          <div style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 14 }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
              Cash Liq. Violations
            </div>
            <div id="stats-clv" style={{ fontSize: 28, fontWeight: 800, color: violationColor(clvCount) }}>
              {clvCount}
            </div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 4 }}>3 = 90-day ban</div>
          </div>

          {/* Freeriding */}
          <div style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 14 }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
              Freeriding
            </div>
            <div id="stats-freeriding" style={{ fontSize: 28, fontWeight: 800, color: violationColor(freeriding, true) }}>
              {freeriding}
            </div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 4 }}>1 = instant ban</div>
          </div>

          {/* PDT */}
          <div style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 14 }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
              Pattern Day Trades
            </div>
            <div id="stats-pdt" style={{ fontSize: 28, fontWeight: 800, color: pdtFlagged ? '#facc15' : '#4ade80' }}>
              {pdtFlagged ? 'FLAGGED' : 'OK'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 4 }}>4+ in 5d = PDT flag</div>
          </div>
        </div>
      </div>

      {/* ── 5. Cash & Settlement ── */}
      {accounts.length > 0 && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 20, marginBottom: 28 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)', marginBottom: 16 }}>
            Cash &amp; Settlement
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {accounts.map(acc => {
              const s = acc.summary as AccountSummary | null
              const settled = s?.settled_cash ?? s?.cash ?? 0
              const unsettled = s?.unsettled_cash ?? 0
              const total = settled + unsettled
              const settledPct = total > 0 ? (settled / total) * 100 : 100
              const unsettledPct = total > 0 ? (unsettled / total) * 100 : 0

              return (
                <div key={acc.strategy}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{acc.label}</div>
                    <div style={{ fontSize: 12, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
                      <span style={{ color: '#4ade80' }}>
                        ${settled.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                      {unsettled > 0 && (
                        <span style={{ color: 'var(--ink-faint)' }}>
                          {' '}+{' '}
                          <span style={{ color: '#facc15' }}>
                            ${unsettled.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                          {' '}pending
                        </span>
                      )}
                    </div>
                  </div>
                  <div style={{ height: 8, borderRadius: 99, background: 'var(--surface-raised)', overflow: 'hidden', display: 'flex' }}>
	                    <div style={{ width: `${settledPct}%`, background: '#4ade80' }} />
	                    <div style={{ width: `${unsettledPct}%`, background: '#facc15' }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── 6. ML Model Details (collapsible) ── */}
      <details style={{ marginBottom: 16 }}>
        <summary style={{
          cursor: 'pointer', userSelect: 'none',
          fontSize: 14, fontWeight: 700, color: 'var(--ink)',
          background: 'var(--surface)', border: '1px solid var(--surface-rule)',
          borderRadius: 8, padding: '14px 20px', listStyle: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>ML Model Details</span>
          <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>▼</span>
        </summary>
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--surface-rule)',
          borderTop: 'none', borderRadius: '0 0 8px 8px', padding: 20,
        }}>
          {mlData ? (
            <>
              {/* Status badge */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
                <div id="ml-status-badge">
                  <Badge variant={mlData.bundle_exists ? 'success' : 'danger'}>
                    {mlData.status_label ?? (mlData.bundle_exists ? 'Model Loaded' : 'No Model')}
                  </Badge>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 16, marginBottom: 20 }}>
                {/* Last Trained — first, prominent */}
                {mlData.created_at && (
                  <div style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 12, gridColumn: 'span 2' }}>
                    <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 4 }}>Last Trained</div>
                    <div id="ml-last-trained" style={{ fontSize: 14, fontWeight: 700, color: 'var(--accent)' }}>
                      {new Date(mlData.created_at).toLocaleString()}
                    </div>
                  </div>
                )}
                {[
                  { label: 'Confidence Threshold', value: mlData.settings?.ml_probability_threshold?.toFixed(2), id: 'ml-confidence-thresh' },
                  { label: 'Features', value: mlData.settings?.feature_count, id: 'ml-features' },
                  { label: 'Test Period', value: mlData.settings?.test_period, id: 'ml-test-period' },
                  { label: 'Trained Rows', value: mlData.settings?.train_rows?.toLocaleString(), id: 'ml-rows' },
                  { label: 'Hold Days', value: mlData.settings?.hold, id: 'ml-hold-days' },
                  { label: 'ROC AUC', value: mlData.metrics?.win_probability?.roc_auc?.toFixed(4), id: 'ml-roc' },
                ].map(m => (
                  <div key={m.id} style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: 12 }}>
                    <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 4 }}>{m.label}</div>
                    <div id={m.id} style={{ fontSize: 18, fontWeight: 700, color: 'var(--accent)' }}>
                      {m.value != null ? String(m.value) : '—'}
                    </div>
                  </div>
                ))}
              </div>

              {/* Feature importance bars */}
              {(mlData.feature_importance?.length ?? 0) > 0 && (
                <div id="ml-fi-bars">
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>Feature Importance (top 15)</div>
                  <FeatureImportanceBar features={mlData.feature_importance} topN={15} />
                </div>
              )}

              {/* Retrain History */}
              {(() => {
                const hist = (mlHistory as Array<Record<string, unknown>> | undefined) ?? []
                if (!hist.length) return null
                const sorted = [...hist].reverse() // newest first
                return (
                  <div style={{ marginTop: 20 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 10 }}>
                      Retrain History ({sorted.length} cycle{sorted.length !== 1 ? 's' : ''})
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                            {['Date', 'WF ROC', 'Rows', 'Outcome'].map(h => (
                              <th key={h} style={{ padding: '6px 10px', fontWeight: 500, color: 'var(--ink-faint)', textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {sorted.map((m, i) => {
                            const outcome = String(m.outcome ?? '')
                            const deployed = outcome.startsWith('deployed') || outcome.includes('deploy')
                            const failed = outcome.includes('failed') || outcome.includes('error') || outcome.includes('leakage')
                            const wfRoc = m.win_roc_wf ?? m.win_roc ?? null
                            const rocColor = wfRoc == null ? 'var(--ink-muted)' : Number(wfRoc) >= 0.51 ? '#4ade80' : Number(wfRoc) >= 0.49 ? '#facc15' : '#f87171'
                            const outcomeColor = deployed ? '#4ade80' : failed ? '#f87171' : 'var(--ink-faint)'
                            const outcomeLabel = deployed ? '✓ deployed' : failed ? '✗ failed' : outcome.slice(0, 24)
                            return (
                              <tr key={i} style={{ borderBottom: '1px solid var(--surface-rule)', background: i === 0 ? 'rgba(74,222,128,0.04)' : 'transparent' }}>
                                <td style={{ padding: '6px 10px', color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', fontSize: 11 }}>
                                  {m.retrain_date as string ?? (m.timestamp ? new Date(m.timestamp as string).toLocaleDateString() : '—')}
                                  {i === 0 && <span style={{ marginLeft: 6, fontSize: 9, fontWeight: 700, color: 'var(--accent)', background: 'rgba(255,255,255,0.08)', padding: '1px 5px', borderRadius: 3 }}>LATEST</span>}
                                </td>
                                <td style={{ padding: '6px 10px', fontFamily: 'var(--font-mono)', color: rocColor, fontWeight: 700 }}>
                                  {wfRoc != null ? Number(wfRoc).toFixed(4) : '—'}
                                </td>
                                <td style={{ padding: '6px 10px', color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
                                  {m.csv_rows != null ? Number(m.csv_rows).toLocaleString() : '—'}
                                </td>
                                <td style={{ padding: '6px 10px' }}>
                                  <span style={{ color: outcomeColor, fontSize: 11 }}>{outcomeLabel}</span>
                                  {m.notes != null && (
                                    <div style={{ fontSize: 10, color: 'var(--ink-faint)', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>
                                      {String(m.notes).slice(0, 80)}
                                    </div>
                                  )}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )
              })()}
            </>
          ) : (
            <div style={{ color: 'var(--ink-faint)', fontSize: 13 }}>No ML model data available.</div>
          )}
        </div>
      </details>

      {/* ── 7. Retrain Form (collapsible) ── */}
      <details>
        <summary style={{
          cursor: 'pointer', userSelect: 'none',
          fontSize: 14, fontWeight: 700, color: 'var(--ink)',
          background: 'var(--surface)', border: '1px solid var(--surface-rule)',
          borderRadius: 8, padding: '14px 20px', listStyle: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>Retrain Model</span>
          <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>▼</span>
        </summary>
        <div style={{
          background: 'var(--surface)', border: '1px solid var(--surface-rule)',
          borderTop: 'none', borderRadius: '0 0 8px 8px', padding: 20,
        }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--ink-faint)' }}>Input File</label>
              <input
                id="ml-train-input"
                className="input"
                style={{ maxWidth: 260 }}
                placeholder="backtest_results.json"
                value={trainInput}
                onChange={e => setTrainInput(e.target.value)}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--ink-faint)' }}>Hold Days</label>
              <input
                id="ml-hold-input"
                className="input"
                style={{ width: 80 }}
                type="number"
                min="1"
                max="60"
                value={holdDays}
                onChange={e => setHoldDays(e.target.value)}
              />
            </div>
            <button
              id="ml-train-btn"
              className="btn-primary"
              onClick={() => {
                setTrainLog('')
                setTraining(true)
                setTimeout(() => send({ input: trainInput, hold_days: Number(holdDays) }), 100)
              }}
              disabled={training || !trainInput}
            >
              {training ? 'Training…' : 'Start Training'}
            </button>
            <button
              id="ml-retrain-all-btn"
              className="btn-secondary"
              disabled={training}
              onClick={async () => {
                if (!window.confirm('Start full retrain with all_tickers.txt? This takes 3-5 hours.')) return
                try {
                  const r = await api.post('/ml/retrain', { tickers: 'all_tickers.txt' })
                  window.alert(`Retrain started (PID ${r.data?.pid}). Monitor at Logs → Server Logs → Retrain.`)
                } catch (e: unknown) {
                  window.alert(`Failed: ${(e as Error).message}`)
                }
              }}
            >
              Retrain All (Auto)
            </button>
            {training && (
              <button
                id="ml-stop-btn"
                className="btn-secondary"
                onClick={() => { close(); setTraining(false) }}
              >
                Stop
              </button>
            )}
          </div>
          {trainLog && (
            <div id="ml-train-log-card">
              <pre id="ml-train-log" style={{
                background: 'var(--surface-soft)', borderRadius: 6, padding: 12,
                fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--ink-muted)',
                maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap',
                border: '1px solid var(--surface-rule)',
              }}>
                {trainLog}
              </pre>
            </div>
          )}
        </div>
      </details>
    </div>
  )
}
