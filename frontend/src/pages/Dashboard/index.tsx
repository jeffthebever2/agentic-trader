import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getPaperStatus } from '@/api/paper'
import { getMarketChart, getQuotes } from '@/api/market'
import { getDiagnostics } from '@/api/admin'
import { getMlStatus } from '@/api/ml'
import { LineChart } from '@/components/charts/LineChart'
import api from '@/api/client'
import type { Quote, Portfolio, PaperAccount, CandidateRow } from '@/types'

// ── Constants ──────────────────────────────────────────────────────────────
const STRATEGY_COLORS: Record<string, string> = {
  algorithm: '#22d3ee',
  machine_learning: '#a78bfa',
  ml_new: '#60a5fa',
  combined: '#34d399',
  pure_ai: '#fb923c',
  long_hold: '#f59e0b',
  unified_brain: '#e879f9',
}

const CHART_SYMBOLS = ['SPY', 'QQQ', 'NVDA', 'AAPL'] as const
type ChartSymbol = (typeof CHART_SYMBOLS)[number]

const DEFAULT_WATCHLIST = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'META', 'MSFT', 'AMZN']
const TAPE_TICKERS = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'META', 'MSFT', 'AMZN', 'GOOGL', 'BRK.B']

type OpportunityMode = 'gainers' | 'losers' | 'active'
type FeedTab = 'candidates' | 'trades'

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt$(n: number, decimals = 0) {
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })
}

function winRateColor(wr: number) {
  if (wr >= 0.65) return '#4ade80'
  if (wr >= 0.5) return '#facc15'
  return '#f87171'
}

function pnlColor(n: number) {
  return n >= 0 ? '#4ade80' : '#f87171'
}

interface OpportunityItem {
  ticker?: string
  symbol?: string
  price?: number
  last_price?: number
  change_pct?: number
  changePct?: number
  volume?: number
}

interface TradeRow {
  ticker?: string
  strategy?: string
  date?: string
  exit_date?: string
  pnl?: number
  entry?: number
  exit?: number
  shares?: number
  action?: string
}

interface HistoryResponse {
  trades?: TradeRow[]
  items?: TradeRow[]
  rows?: TradeRow[]
}

interface WatchlistResponse {
  tickers?: string[]
  watchlist?: string[]
}

// ── Sub-components ─────────────────────────────────────────────────────────

function TickerTape() {
  const tapeQ = useQuery({
    queryKey: ['market', 'quotes', 'tape'],
    queryFn: () => getQuotes(TAPE_TICKERS),
    refetchInterval: 30_000,
    staleTime: 20_000,
  })
  const quotes = tapeQ.data ?? {}

  const items = TAPE_TICKERS.map(t => {
    const q: Quote | undefined = quotes[t]
    return { ticker: t, price: q?.price ?? null, changePct: q?.change_pct ?? null }
  })

  const content = items.map(it => {
    const color = it.changePct == null ? 'var(--ink-muted)' : it.changePct >= 0 ? '#4ade80' : '#f87171'
    const arrow = it.changePct == null ? '' : it.changePct >= 0 ? '▲' : '▼'
    const pctStr = it.changePct == null ? '' : ` ${arrow}${Math.abs(it.changePct).toFixed(2)}%`
    const priceStr = it.price != null ? ` $${it.price.toFixed(2)}` : ''
    return (
      <span key={it.ticker} style={{ marginRight: 32, whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
        <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{it.ticker}</span>
        <span style={{ color: 'var(--ink-muted)', marginLeft: 4 }}>{priceStr}</span>
        <span style={{ color, marginLeft: 4 }}>{pctStr}</span>
      </span>
    )
  })

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--surface-rule)',
      borderRadius: 8,
      overflow: 'hidden',
      padding: '8px 0',
    }}>
      <div style={{ overflow: 'hidden', position: 'relative' }}>
        <style>{`
          @keyframes tape-scroll {
            0%   { transform: translateX(0); }
            100% { transform: translateX(-50%); }
          }
          .tape-inner {
            display: inline-flex;
            animation: tape-scroll 40s linear infinite;
            white-space: nowrap;
          }
          .tape-inner:hover { animation-play-state: paused; }
        `}</style>
        <div className="tape-inner" style={{ padding: '0 16px' }}>
          {content}{content}
        </div>
      </div>
    </div>
  )
}

function StatRow({ paperQ }: { paperQ: ReturnType<typeof useQuery<ReturnType<typeof getPaperStatus> extends Promise<infer T> ? T : never>> }) {
  const accounts = (paperQ.data?.accounts ?? []) as PaperAccount[]
  const totalValue = accounts.reduce((s, a) => s + Number(a.summary?.total_value ?? a.summary?.cash ?? 0), 0)
  const totalPnl = accounts.reduce((s, a) => {
    const start = Number(a.summary?.starting_cash ?? 0)
    const val = Number(a.summary?.total_value ?? a.summary?.cash ?? 0)
    return s + (start ? val - start : Number(a.summary?.realized_pnl ?? 0))
  }, 0)
  const totalAnalyses = accounts.reduce((s, a) => s + (a.candidates?.count ?? 0), 0)
  const totalCash = accounts.reduce((s, a) => s + Number(a.summary?.cash ?? 0), 0)

  const stats = [
    { id: 'stat-portfolio', label: 'Portfolio Value', value: paperQ.isLoading ? '—' : fmt$(totalValue), color: 'var(--ink)' },
    { id: 'stat-daypnl', label: 'Day P&L', value: paperQ.isLoading ? '—' : fmt$(totalPnl), color: pnlColor(totalPnl) },
    { id: 'stat-analyses', label: 'Total Analyses', value: paperQ.isLoading ? '—' : String(totalAnalyses), color: 'var(--ink)' },
    { id: 'stat-cash', label: 'Cash Available', value: paperQ.isLoading ? '—' : fmt$(totalCash), color: 'var(--ink)' },
  ]

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
      {stats.map(s => (
        <div key={s.id} id={s.id} style={{
          background: 'var(--surface)',
          border: '1px solid var(--surface-rule)',
          borderRadius: 8,
          padding: '14px 16px',
        }}>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            {s.label}
          </div>
          <div style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: 'var(--font-mono)' }}>
            {s.value}
          </div>
        </div>
      ))}
    </div>
  )
}

function MarketChartPanel() {
  const [symbol, setSymbol] = useState<ChartSymbol>('SPY')

  const chartQ = useQuery({
    queryKey: ['market', 'chart', symbol],
    queryFn: () => getMarketChart(symbol, '30d', '1d'),
    staleTime: 300_000,
  })

  const datasets = (chartQ.data?.dates?.length)
    ? [{
        label: symbol,
        data: chartQ.data.dates.map((d, i) => ({ x: d.slice(0, 10), y: chartQ.data!.close[i] ?? 0 })),
        color: '#60a5fa',
        fill: true,
      }]
    : []

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>Market Chart</div>
        <div style={{ display: 'flex', gap: 4 }}>
          {CHART_SYMBOLS.map(s => (
            <button key={s} onClick={() => setSymbol(s)} style={{
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid var(--surface-rule)',
              background: symbol === s ? 'var(--accent)' : 'var(--surface-raised)',
              color: symbol === s ? '#fff' : 'var(--ink-muted)',
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.15s',
            }}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div id="dash-market-canvas" style={{ position: 'relative', height: 200 }}>
        {chartQ.isLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--ink-faint)', fontSize: 12 }}>
            Loading chart…
          </div>
        ) : datasets.length > 0 ? (
          <LineChart datasets={datasets} height={200} yFormatter={v => '$' + v.toFixed(0)} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--ink-faint)', fontSize: 12 }}>
            No data
          </div>
        )}
      </div>
    </div>
  )
}

function OpportunitiesPanel() {
  const [mode, setMode] = useState<OpportunityMode>('gainers')

  const oppQ = useQuery({
    queryKey: ['market', 'opportunities', mode],
    queryFn: () => api.get<OpportunityItem[]>('/market/opportunities', { params: { mode } }).then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })

  const items = Array.isArray(oppQ.data) ? oppQ.data.slice(0, 12) : []

  const tabs: { key: OpportunityMode; label: string }[] = [
    { key: 'gainers', label: 'Gainers' },
    { key: 'losers', label: 'Losers' },
    { key: 'active', label: 'Movers' },
  ]

  return (
    <div id="dash-opportunities" style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>Today's Opportunities</div>
        <div style={{ display: 'flex', gap: 4 }}>
          {tabs.map(t => (
            <button key={t.key} onClick={() => setMode(t.key)} style={{
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid var(--surface-rule)',
              background: mode === t.key ? 'var(--accent)' : 'var(--surface-raised)',
              color: mode === t.key ? '#fff' : 'var(--ink-muted)',
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
            }}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ padding: 12 }}>
        {oppQ.isLoading ? (
          <div style={{ padding: '16px 4px', color: 'var(--ink-faint)', fontSize: 12 }}>Loading…</div>
        ) : items.length === 0 ? (
          <div style={{ padding: '16px 4px', color: 'var(--ink-faint)', fontSize: 12 }}>No data available</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
            {items.map((it, i) => {
              const ticker = it.ticker ?? it.symbol ?? '—'
              const price = it.price ?? it.last_price
              const pct = it.change_pct ?? it.changePct
              const col = pct == null ? 'var(--ink-muted)' : pct >= 0 ? '#4ade80' : '#f87171'
              return (
                <div key={i} style={{
                  background: 'var(--surface-raised)',
                  border: '1px solid var(--surface-rule)',
                  borderRadius: 6,
                  padding: '10px 12px',
                }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{ticker}</div>
                  <div style={{ fontSize: 12, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                    {price != null ? `$${price.toFixed(2)}` : '—'}
                  </div>
                  <div style={{ fontSize: 12, color: col, fontWeight: 600, marginTop: 2 }}>
                    {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function LiveFeedPanel({ accounts }: { accounts: PaperAccount[] }) {
  const [tab, setTab] = useState<FeedTab>('candidates')

  const historyQ = useQuery({
    queryKey: ['history', 1],
    queryFn: () => api.get<HistoryResponse>('/history', { params: { page: 1, page_size: 20 } }).then(r => r.data),
    staleTime: 60_000,
  })

  const candidates: (CandidateRow & { _stratLabel: string })[] = accounts.flatMap(a =>
    (a.candidates?.rows ?? []).map(r => ({ ...r, _stratLabel: a.label }))
  )

  const trades: TradeRow[] = (
    historyQ.data?.trades ?? historyQ.data?.items ?? historyQ.data?.rows ?? []
  )

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--surface-rule)' }}>
        {(['candidates', 'trades'] as FeedTab[]).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            flex: 1,
            padding: '10px 0',
            background: 'none',
            border: 'none',
            borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === t ? 'var(--accent)' : 'var(--ink-muted)',
            fontWeight: tab === t ? 700 : 500,
            fontSize: 13,
            cursor: 'pointer',
            textTransform: 'capitalize',
          }}>
            {t === 'candidates' ? `Candidates (${candidates.length})` : 'Recent Trades'}
          </button>
        ))}
      </div>

      <div style={{ maxHeight: 320, overflowY: 'auto' }}>
        {tab === 'candidates' ? (
          candidates.length === 0 ? (
            <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No candidates today</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--surface-soft)' }}>
                  {['Ticker', 'Strategy', 'Entry', 'Target', 'ML%', 'Score'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--ink-faint)', fontWeight: 600, fontSize: 11, borderBottom: '1px solid var(--surface-rule)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {candidates.map((c, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{c.ticker}</td>
                    <td style={{ padding: '8px 12px', color: 'var(--ink-muted)' }}>{c._stratLabel}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>${Number(c.entry).toFixed(2)}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: '#4ade80' }}>${Number(c.target).toFixed(2)}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>{(Number(c.ml_probability) * 100).toFixed(0)}%</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>{Number(c.score).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          historyQ.isLoading ? (
            <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>Loading trades…</div>
          ) : trades.length === 0 ? (
            <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No recent trades</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--surface-soft)' }}>
                  {['Ticker', 'Strategy', 'Date', 'P&L'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--ink-faint)', fontWeight: 600, fontSize: 11, borderBottom: '1px solid var(--surface-rule)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const pnl = t.pnl ?? 0
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{t.ticker ?? '—'}</td>
                      <td style={{ padding: '8px 12px', color: 'var(--ink-muted)' }}>{t.strategy ?? '—'}</td>
                      <td style={{ padding: '8px 12px', color: 'var(--ink-muted)' }}>{(t.exit_date ?? t.date ?? '').slice(0, 10) || '—'}</td>
                      <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: pnlColor(pnl) }}>
                        {pnl >= 0 ? '+' : ''}{fmt$(pnl, 2)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  )
}

// ── Right Column ───────────────────────────────────────────────────────────

function QuickAnalyze() {
  const navigate = useNavigate()
  const [ticker, setTicker] = useState('')
  const today = new Date().toISOString().slice(0, 10)
  const [date, setDate] = useState(today)
  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 10px',
    background: 'var(--surface-soft)',
    border: '1px solid var(--surface-rule)',
    borderRadius: 6,
    color: 'var(--ink)',
    fontSize: 13,
    outline: 'none',
    boxSizing: 'border-box',
  }

  function run() {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    navigate(`/analyze?ticker=${encodeURIComponent(t)}&date=${encodeURIComponent(date)}`)
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>Quick Analyze</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input
          type="text"
          placeholder="Ticker (e.g. AAPL)"
          value={ticker}
          onChange={e => setTicker(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && run()}
          style={inputStyle}
        />
        <input
          type="date"
          value={date}
          onChange={e => setDate(e.target.value)}
          style={inputStyle}
        />
        <button onClick={run} style={{
          padding: '9px 0',
          background: 'var(--accent)',
          color: '#fff',
          border: 'none',
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 700,
          cursor: 'pointer',
          width: '100%',
        }}>
          Run Analysis
        </button>
      </div>
    </div>
  )
}

interface DiagData {
  fidelity?: { status?: string; connected?: boolean }
  market_data?: { status?: string }
  ml?: { status?: string; available?: boolean }
  market_status?: { status?: string; is_open?: boolean; [key: string]: unknown }
  [key: string]: unknown
}

function SystemStatus() {
  const diagQ = useQuery({
    queryKey: ['admin', 'diagnostics'],
    queryFn: getDiagnostics,
    staleTime: 60_000,
    refetchInterval: 120_000,
  })
  const mlQ = useQuery({
    queryKey: ['ml', 'status'],
    queryFn: getMlStatus,
    staleTime: 120_000,
  })

  const diag = diagQ.data as DiagData | undefined

  function chip(label: string, ok: boolean | null) {
    const bg = ok == null ? 'var(--surface-soft)' : ok ? 'rgba(74,222,128,0.15)' : 'rgba(248,113,113,0.15)'
    const color = ok == null ? 'var(--ink-muted)' : ok ? '#4ade80' : '#f87171'
    const dot = ok == null ? '○' : ok ? '●' : '●'
    return (
      <div key={label} style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        background: bg, border: `1px solid ${color}30`,
        borderRadius: 12, padding: '4px 10px', fontSize: 11, color,
      }}>
        <span>{dot}</span>
        <span style={{ fontWeight: 600 }}>{label}</span>
      </div>
    )
  }

  const fidelityOk = diag?.fidelity ? (diag.fidelity.status === 'connected' || diag.fidelity.connected === true) : null
  const marketDataOk = diag?.market_data ? diag.market_data.status !== 'error' : null
  const mlOk = mlQ.data ? mlQ.data.bundle_exists : null
  const marketOpen = diag?.market_status
    ? ((diag.market_status as { is_open?: boolean }).is_open ?? diag.market_status.status === 'open')
    : null

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 10 }}>System Status</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {chip('Fidelity', fidelityOk)}
        {chip('Market Data', marketDataOk)}
        {chip('ML Model', mlOk)}
        {chip('Market', marketOpen)}
      </div>
    </div>
  )
}

interface AnalyticsData {
  win_rate?: number
  total_pnl?: number
  total_trades?: number
  by_strategy?: Record<string, { win_rate?: number; total_pnl?: number; trades?: number }>
}

function PortfolioStats({ accounts }: { accounts: PaperAccount[] }) {
  const analyticsQ = useQuery({
    queryKey: ['paper', 'analytics'],
    queryFn: () => api.get<AnalyticsData>('/paper/analytics').then(r => r.data),
    staleTime: 60_000,
  })
  const data = analyticsQ.data

  const totalWinRate = data?.win_rate ?? null
  const totalPnl = data?.total_pnl ?? null
  const totalTrades = data?.total_trades ?? null

  const stratRows = accounts.map(a => {
    const key = a.strategy
    const byStrat = data?.by_strategy?.[key]
    const wr = byStrat?.win_rate ?? null
    const pnl = byStrat?.total_pnl ?? null
    const trades = byStrat?.trades ?? null
    const color = STRATEGY_COLORS[key] ?? '#94a3b8'
    return { label: a.label, strategy: key, wr, pnl, trades, color }
  })

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
        Portfolio Stats
      </div>
      {/* Summary strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0, borderBottom: '1px solid var(--surface-rule)' }}>
        {[
          { label: 'Win Rate', value: totalWinRate != null ? `${(totalWinRate * 100).toFixed(0)}%` : '—', color: totalWinRate != null ? winRateColor(totalWinRate) : 'var(--ink)' },
          { label: 'Total P&L', value: totalPnl != null ? fmt$(totalPnl) : '—', color: totalPnl != null ? pnlColor(totalPnl) : 'var(--ink)' },
          { label: 'Trades', value: totalTrades != null ? String(totalTrades) : '—', color: 'var(--ink)' },
        ].map((s, i) => (
          <div key={s.label} style={{
            padding: '10px 12px',
            borderRight: i < 2 ? '1px solid var(--surface-rule)' : 'none',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{s.label}</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: s.color, fontFamily: 'var(--font-mono)' }}>{s.value}</div>
          </div>
        ))}
      </div>
      {/* Per-strategy rows */}
      <div id="dash-stats-rows">
        {stratRows.map(r => (
          <div key={r.strategy} style={{ padding: '8px 14px', borderBottom: '1px solid var(--surface-rule)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: r.color, flexShrink: 0 }} />
                <span style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 600 }}>{r.label}</span>
              </div>
              <span style={{ fontSize: 11, color: r.wr != null ? winRateColor(r.wr) : 'var(--ink-faint)', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
                {r.wr != null ? `${(r.wr * 100).toFixed(0)}%` : '—'}
              </span>
            </div>
            {/* Win rate bar */}
            <div style={{ height: 3, background: 'var(--surface-soft)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${r.wr != null ? Math.min(r.wr * 100, 100) : 0}%`,
                background: r.wr != null ? winRateColor(r.wr) : 'var(--surface-rule)',
                borderRadius: 2,
                transition: 'width 0.4s ease',
              }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function PortfolioExposure() {
  const portfolioQ = useQuery({
    queryKey: ['broker', 'portfolio'],
    queryFn: () => api.get<Portfolio>('/broker/portfolio').then(r => r.data),
    staleTime: 120_000,
  })

  const sectors = portfolioQ.data?.sector_exposure ?? {}
  const sectorEntries = Object.entries(sectors).sort((a, b) => b[1] - a[1])
  const maxVal = sectorEntries.length ? sectorEntries[0][1] : 1

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
        Portfolio Exposure
      </div>
      <div style={{ padding: '10px 14px' }}>
        {portfolioQ.isLoading ? (
          <div style={{ color: 'var(--ink-faint)', fontSize: 12 }}>Loading…</div>
        ) : sectorEntries.length === 0 ? (
          <div style={{ color: 'var(--ink-faint)', fontSize: 12 }}>No positions</div>
        ) : (
          sectorEntries.map(([sector, pct]) => (
            <div key={sector} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 500 }}>{sector}</span>
                <span style={{ fontSize: 11, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>{(pct * 100).toFixed(1)}%</span>
              </div>
              <div style={{ height: 4, background: 'var(--surface-soft)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${(pct / maxVal) * 100}%`,
                  background: 'var(--accent)',
                  borderRadius: 2,
                }} />
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function PaperCandidatesPanel({ accounts }: { accounts: PaperAccount[] }) {
  const candidates = accounts.flatMap(a =>
    (a.candidates?.rows ?? []).slice(0, 3).map(r => ({ ...r, _stratLabel: a.label }))
  ).slice(0, 8)

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)', fontSize: 13, fontWeight: 600, color: 'var(--ink)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Paper Candidates</span>
        <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{candidates.length} shown</span>
      </div>
      <div id="dash-candidates">
        {candidates.length === 0 ? (
          <div style={{ padding: '14px 16px', fontSize: 12, color: 'var(--ink-faint)' }}>No candidates</div>
        ) : (
          candidates.map((c, i) => (
            <div key={i} style={{ padding: '8px 14px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{c.ticker}</span>
                <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 2 }}>{c._stratLabel}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 12, color: '#4ade80', fontFamily: 'var(--font-mono)' }}>${Number(c.entry).toFixed(2)}</div>
                <div style={{ fontSize: 10, color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>ML {(Number(c.ml_probability) * 100).toFixed(0)}%</div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function WatchlistPanel() {
  const watchlistQ = useQuery({
    queryKey: ['market', 'watchlist'],
    queryFn: () => api.get<WatchlistResponse | string[]>('/market/watchlist').then(r => {
      const d = r.data
      if (Array.isArray(d)) return d as string[]
      const resp = d as WatchlistResponse
      return resp.tickers ?? resp.watchlist ?? DEFAULT_WATCHLIST
    }),
    staleTime: 300_000,
  })

  const tickers: string[] = watchlistQ.data ?? DEFAULT_WATCHLIST

  const quotesQ = useQuery({
    queryKey: ['market', 'quotes', 'watchlist', tickers.join(',')],
    queryFn: () => getQuotes(tickers),
    staleTime: 30_000,
    refetchInterval: 60_000,
    enabled: tickers.length > 0,
  })

  const quotes = quotesQ.data ?? {}

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
        Watchlist
      </div>
      <div>
        {tickers.slice(0, 10).map(t => {
          const q: Quote | undefined = quotes[t]
          const pct = q?.change_pct ?? null
          const price = q?.price ?? null
          return (
            <div key={t} style={{ padding: '8px 14px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{t}</span>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 12, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
                  {price != null ? `$${price.toFixed(2)}` : '—'}
                </div>
                <div style={{ fontSize: 11, color: pct != null ? (pct >= 0 ? '#4ade80' : '#f87171') : 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}>
                  {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const paperQ = useQuery({
    queryKey: ['paper', 'status'],
    queryFn: getPaperStatus,
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  const accounts = (paperQ.data?.accounts ?? []) as PaperAccount[]

  // Responsive: watch viewport width
  const [narrow, setNarrow] = useState(window.innerWidth < 980)
  const rafRef = useRef<number | null>(null)
  useEffect(() => {
    function onResize() {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(() => setNarrow(window.innerWidth < 980))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  return (
    <div id="panel-dashboard" style={{ padding: 20, maxWidth: 1400, margin: '0 auto' }}>
      {/* Ticker tape — full width */}
      <div style={{ marginBottom: 16 }}>
        <TickerTape />
      </div>

      {/* Two-column layout */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: narrow ? '1fr' : '1fr 320px',
        gap: 16,
        alignItems: 'start',
      }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <StatRow paperQ={paperQ} />
          <MarketChartPanel />
          <OpportunitiesPanel />
          <LiveFeedPanel accounts={accounts} />
        </div>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <QuickAnalyze />
          <SystemStatus />
          <PortfolioStats accounts={accounts} />
          <PortfolioExposure />
          <PaperCandidatesPanel accounts={accounts} />
          <WatchlistPanel />
        </div>
      </div>
    </div>
  )
}
