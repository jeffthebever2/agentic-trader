import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getPaperStatus } from '@/api/paper'
import { getMarketChart, getQuotes, getNewsSummary } from '@/api/market'
import { getMlStatus } from '@/api/ml'
import { CandlestickChart } from '@/components/charts/CandlestickChart'
import api from '@/api/client'
import type { Quote, Portfolio, PaperAccount, CandidateRow } from '@/types'

interface FidelitySummary {
  total_value?: string | null
  daily_change?: string | null
  daily_change_pct?: string | null
}

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

const DEFAULT_CHART_SYMBOLS = ['SPY', 'QQQ', 'NVDA', 'AAPL']
type ChartSymbol = string

function loadChartSymbols(): string[] {
  try {
    const raw = localStorage.getItem('dash_chart_symbols')
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length > 0) return parsed.slice(0, 8)
    }
  } catch { /* ignore */ }
  return DEFAULT_CHART_SYMBOLS
}

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
  timestamp?: string
  exit_date?: string
  pnl?: number
  entry?: number
  exit?: number
  shares?: number
  action?: string
}

interface TradeLogPage {
  entries?: TradeRow[]
  total?: number
  page?: number
  pages?: number
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
      <span key={it.ticker} className="ticker-item" style={{ fontFamily: 'var(--font-mono)' }}>
        <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{it.ticker}</span>
        <span style={{ color: 'var(--ink-muted)', marginLeft: 4 }}>{priceStr}</span>
        <span style={{ color, marginLeft: 4 }}>{pctStr}</span>
      </span>
    )
  })

  return (
    <div id="dash-ticker-wrap" style={{
      height: 44,
      flexShrink: 0,
      borderBottom: '1px solid var(--surface-rule)',
      overflow: 'hidden',
      position: 'relative',
      background: 'var(--surface)',
    }}>
      <style>{`
        @keyframes tape-scroll {
          0%   { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        #dash-ticker-track {
          display: inline-flex;
          align-items: center;
          height: 100%;
          animation: tape-scroll 40s linear infinite;
          white-space: nowrap;
        }
        #dash-ticker-track:hover { animation-play-state: paused; }
      `}</style>
      {/* Edge fades */}
      <div style={{ position: 'absolute', right: 0, top: 0, height: '100%', width: 48, background: 'linear-gradient(270deg, var(--surface), transparent)', pointerEvents: 'none', zIndex: 2 }} />
      <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: 48, background: 'linear-gradient(90deg, var(--surface), transparent)', pointerEvents: 'none', zIndex: 2 }} />
      <div id="dash-ticker-track" style={{ padding: '0 16px' }}>
        {content}{content}
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
  const totalCash = accounts.reduce((s, a) => s + Number(a.summary?.cash ?? 0), 0)

  const logsQ = useQuery({
    queryKey: ['logs', 'stats'],
    queryFn: () => api.get<{ total_analyses: number; unique_tickers: number }>('/logs/stats').then(r => r.data),
    staleTime: 300_000,
    refetchInterval: 300_000,
  })
  const totalAnalyses = logsQ.data?.total_analyses ?? 0
  const uniqueTickers = logsQ.data?.unique_tickers ?? 0

  // Overlay Fidelity data if connected
  const fidelityQ = useQuery({
    queryKey: ['fidelity', 'summary'],
    queryFn: () => api.get<{ summary?: FidelitySummary }>('/fidelity/summary').then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  })
  const fidSummary = fidelityQ.data?.summary

  const portfolioValue = fidSummary?.total_value ?? (paperQ.isLoading ? '—' : fmt$(totalValue))
  const portfolioSub   = fidSummary?.total_value ? 'via Fidelity' : 'Paper trading'
  const dayPnlValue    = fidSummary?.daily_change ?? (paperQ.isLoading ? '—' : fmt$(totalPnl))
  const dayPnlColor    = fidSummary?.daily_change
    ? (fidSummary.daily_change.startsWith('-') ? '#f87171' : '#4ade80')
    : pnlColor(totalPnl)

  const stats = [
    { id: 'stat-portfolio', label: 'Portfolio Value', value: portfolioValue, color: 'var(--ink)', sub: portfolioSub },
    { id: 'stat-daypnl',    label: 'Day P&L',         value: dayPnlValue,   color: dayPnlColor, sub: fidSummary?.daily_change_pct ?? 'Since open' },
    { id: 'stat-analyses',  label: 'Total Analyses',  value: logsQ.isLoading ? '—' : String(totalAnalyses), color: 'var(--ink)', sub: 'LLM signals' },
    { id: 'stat-cash',      label: 'Cash Available',  value: paperQ.isLoading ? '—' : fmt$(totalCash),   color: 'var(--ink)', sub: `${uniqueTickers} tickers analyzed` },
  ]

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(4, 1fr)',
      flexShrink: 0,
      borderBottom: '1px solid var(--surface-rule)',
      background: 'var(--surface)',
    }}>
      {stats.map((s, i) => (
        <div key={s.id} className="dash-stat-cell" id={s.id} style={{
          padding: '12px 20px',
          borderRight: i < 3 ? '1px solid var(--surface-rule)' : 'none',
        }}>
          <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700 }}>
            {s.label}
          </div>
          <div className={`dash-stat-num${s.color === 'var(--ink)' ? ' text-gradient-ink' : ''}`} style={{ fontSize: 22, fontWeight: 700, color: s.color, fontFamily: 'var(--font-mono)', letterSpacing: '-0.035em' }}>
            {s.value}
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 4, minHeight: 16 }}>
            {s.sub}
          </div>
        </div>
      ))}
    </div>
  )
}

type ChartPeriod = '5d' | '30d' | '90d' | '1y'
const CHART_PERIODS: ChartPeriod[] = ['5d', '30d', '90d', '1y']

function MarketChartPanel() {
  const [symbols, setSymbols] = useState<string[]>(loadChartSymbols)
  const [symbol, setSymbol] = useState<ChartSymbol>(() => loadChartSymbols()[0] ?? 'SPY')
  const [period, setPeriod] = useState<ChartPeriod>('30d')
  const [editingSymbols, setEditingSymbols] = useState(false)
  const [newTicker, setNewTicker] = useState('')

  function saveSymbols(list: string[]) {
    const capped = list.slice(0, 8)
    setSymbols(capped)
    setSymbol(capped[0] ?? 'SPY')
    try { localStorage.setItem('dash_chart_symbols', JSON.stringify(capped)) } catch { /* ignore */ }
  }

  function addSymbol() {
    const t = newTicker.trim().toUpperCase()
    if (!t || symbols.includes(t) || symbols.length >= 8) return
    saveSymbols([...symbols, t])
    setNewTicker('')
  }

  function removeSymbol(s: string) {
    saveSymbols(symbols.filter(x => x !== s))
  }

  const chartQ = useQuery({
    queryKey: ['market', 'chart', symbol, period],
    queryFn: () => getMarketChart(symbol, period, '1d'),
    staleTime: 300_000,
  })

  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)', padding: '16px 20px 12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)', marginBottom: 0 }}>Market Overview</div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          {symbols.map(s => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
              <button id={`dcb-${s}`} onClick={() => { if (!editingSymbols) setSymbol(s) }} className={`dash-chart-btn${symbol === s ? ' active' : ''}`} style={{
                padding: '3px 10px',
                borderRadius: editingSymbols ? '4px 0 0 4px' : 4,
                border: '1px solid var(--surface-rule)',
                background: symbol === s ? 'var(--accent)' : 'var(--surface-raised)',
                color: symbol === s ? '#fff' : 'var(--ink-muted)',
                fontSize: 11,
                fontWeight: 600,
                cursor: 'pointer',
              }}>
                {s}
              </button>
              {editingSymbols && (
                <button onClick={() => removeSymbol(s)} style={{
                  padding: '3px 6px',
                  borderRadius: '0 4px 4px 0',
                  border: '1px solid var(--surface-rule)',
                  borderLeft: 'none',
                  background: 'var(--surface-raised)',
                  color: 'var(--ink-faint)',
                  fontSize: 11,
                  cursor: 'pointer',
                  lineHeight: 1,
                }}>×</button>
              )}
            </div>
          ))}
          {editingSymbols && (
            <>
              <input
                type="text"
                placeholder="Ticker"
                value={newTicker}
                onChange={e => setNewTicker(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addSymbol()}
                style={{
                  width: 60,
                  padding: '3px 6px',
                  borderRadius: 4,
                  border: '1px solid var(--surface-rule)',
                  background: 'var(--canvas)',
                  color: 'var(--ink)',
                  fontSize: 11,
                  outline: 'none',
                }}
              />
              <button onClick={addSymbol} style={{
                padding: '3px 8px',
                borderRadius: 4,
                border: '1px solid var(--surface-rule)',
                background: 'var(--surface-raised)',
                color: 'var(--ink-muted)',
                fontSize: 11,
                cursor: 'pointer',
              }}>Add</button>
            </>
          )}
          <button onClick={() => setEditingSymbols(v => !v)} style={{
            padding: '3px 8px',
            borderRadius: 4,
            border: '1px solid var(--surface-rule)',
            background: editingSymbols ? 'var(--surface-soft)' : 'transparent',
            color: 'var(--ink-faint)',
            fontSize: 11,
            cursor: 'pointer',
          }}>{editingSymbols ? 'Done' : 'Edit'}</button>
          <div style={{ width: 1, background: 'var(--surface-rule)', margin: '0 2px', alignSelf: 'stretch' }} />
          {CHART_PERIODS.map(p => (
            <button key={p} id={`dcp-${p}`} onClick={() => setPeriod(p)} style={{
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid var(--surface-rule)',
              background: period === p ? 'var(--surface-soft)' : 'var(--surface-raised)',
              color: period === p ? 'var(--ink)' : 'var(--ink-muted)',
              fontSize: 11,
              fontWeight: period === p ? 700 : 500,
              cursor: 'pointer',
            }}>
              {p}
            </button>
          ))}
        </div>
      </div>
      <div id="dash-market-canvas" style={{ position: 'relative', minHeight: 240 }}>
        {chartQ.isLoading ? (
          <div id="dash-chart-loading" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'var(--ink-faint)' }}>
            Loading chart…
          </div>
        ) : chartQ.data?.dates?.length ? (
          <CandlestickChart data={chartQ.data} height={240} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 240, color: 'var(--ink-faint)', fontSize: 12 }}>
            No data
          </div>
        )}
      </div>
    </div>
  )
}

function OpportunitiesPanel() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<OpportunityMode>('gainers')

  const oppQ = useQuery({
    queryKey: ['market', 'opportunities', mode],
    queryFn: () => api.get<OpportunityItem[]>('/market/opportunities', { params: { mode } }).then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
  })

  const items = Array.isArray(oppQ.data) ? oppQ.data.slice(0, 12) : []

  const tabs: { key: OpportunityMode; label: string; icon: string }[] = [
    { key: 'gainers', label: 'Gainers', icon: '▲' },
    { key: 'losers', label: 'Losers', icon: '▼' },
    { key: 'active', label: 'Movers', icon: '⚡' },
  ]

  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)', padding: '14px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)', marginBottom: 0 }}>Today's Opportunities</div>
        <div style={{ display: 'flex', gap: 6 }}>
          {tabs.map(t => (
            <button key={t.key} id={`dob-${t.key}`} onClick={() => setMode(t.key)} className={`dash-opp-btn${mode === t.key ? ' active' : ''}`} style={{
              padding: '3px 10px',
              borderRadius: 4,
              border: '1px solid var(--surface-rule)',
              background: mode === t.key ? 'var(--accent)' : 'var(--surface-raised)',
              color: mode === t.key ? '#fff' : 'var(--ink-muted)',
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
            }}>
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      </div>
      <div id="dash-opportunities" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4 }}>
        {oppQ.isLoading ? (
          <div style={{ gridColumn: '1 / -1', padding: '16px 4px', color: 'var(--ink-faint)', fontSize: 12 }}>Loading…</div>
        ) : items.length === 0 ? (
          <div style={{ gridColumn: '1 / -1', padding: '16px 4px', color: 'var(--ink-faint)', fontSize: 12 }}>No data available</div>
        ) : (
          items.map((it, i) => {
            const ticker = it.ticker ?? it.symbol ?? '—'
            const price = it.price ?? it.last_price
            const pct = it.change_pct ?? it.changePct
            const col = pct == null ? 'var(--ink-muted)' : pct >= 0 ? '#4ade80' : '#f87171'
            return (
              <div
                key={i}
                className="dash-opp-card"
                onClick={() => navigate(`/analyze?ticker=${encodeURIComponent(ticker)}`)}
                style={{
                  background: 'var(--surface-soft)',
                  border: '1px solid var(--surface-rule)',
                  borderRadius: 6,
                  padding: '8px 10px',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 12, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{ticker}</div>
                <div style={{ fontSize: 11, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                  {price != null ? `$${price.toFixed(2)}` : '—'}
                </div>
                <div style={{ fontSize: 11, color: col, fontWeight: 600, marginTop: 2 }}>
                  {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

interface NewsItem {
  title: string
  summary: string
  url: string
  source: string
  published: string
}

interface NewsApiResponse {
  symbol: string
  news: NewsItem[]
}

function timeAgo(published: string) {
  try {
    const diff = Date.now() - new Date(published).getTime()
    const h = Math.floor(diff / 3_600_000)
    const m = Math.floor(diff / 60_000)
    if (h >= 24) return `${Math.floor(h / 24)}d ago`
    if (h >= 1) return `${h}h ago`
    return `${m}m ago`
  } catch { return '' }
}

// Market-wide symbols to aggregate news from
const MARKET_NEWS_SYMBOLS = ['SPY', 'QQQ', 'IWM']

function NewsPanel() {
  // Fetch news for market-wide symbols in parallel
  const results = useQueries({
    queries: MARKET_NEWS_SYMBOLS.map(sym => ({
      queryKey: ['market', 'news', sym],
      queryFn: () => api.get<NewsApiResponse>(`/market/news?symbol=${sym}`).then(r => r.data),
      staleTime: 240_000,
      refetchInterval: 300_000,
    }))
  })

  // AI market sentiment for SPY (broad market proxy)
  const spySummaryQ = useQuery({
    queryKey: ['market', 'news-summary', 'SPY'],
    queryFn: () => getNewsSummary('SPY'),
    staleTime: 1_800_000,
    refetchInterval: 1_800_000,
  })

  const isLoading = results.some(r => r.isLoading)
  const seen = new Set<string>()
  const items: NewsItem[] = results
    .flatMap(r => r.data?.news ?? [])
    .filter(n => { if (seen.has(n.title)) return false; seen.add(n.title); return true })
    .slice(0, 10)

  const sm = spySummaryQ.data
  const sentColor = sm?.sentiment === 'bullish' ? 'var(--accent)'
    : sm?.sentiment === 'bearish' ? '#e05252' : '#d4a017'

  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)', padding: '14px 20px' }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)' }}>
          Market News
        </div>
        {sm && !spySummaryQ.isLoading && (
          <>
            <span style={{ fontSize: 9, fontWeight: 700, color: sentColor, background: 'var(--surface-soft)', padding: '1px 7px', borderRadius: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {sm.sentiment}
            </span>
            <span style={{ fontSize: 9, color: 'var(--ink-faint)', background: 'var(--surface-soft)', padding: '1px 6px', borderRadius: 4 }}>
              {sm.price_impact}
            </span>
          </>
        )}
      </div>

      {/* AI summary snippet */}
      {sm?.summary && (
        <div style={{ fontSize: 11.5, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 10, padding: '8px 10px', background: 'var(--surface-soft)', borderRadius: 6, borderLeft: `2px solid ${sentColor}` }}>
          {sm.summary}
        </div>
      )}

      {isLoading ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading…</div>
      ) : items.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No news available</div>
      ) : (
        <div>
          {items.map((it, i) => (
            <div
              key={i}
              onClick={() => window.open(it.url, '_blank', 'noopener')}
              style={{
                padding: '8px 12px',
                borderBottom: '1px solid var(--surface-rule)',
                cursor: 'pointer',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-soft)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 3 }}>
                {it.source} • {timeAgo(it.published)}
              </div>
              <div style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 700, lineHeight: 1.4 }}>
                {it.title}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function LiveFeedPanel({ accounts }: { accounts: PaperAccount[] }) {
  const [tab, setTab] = useState<FeedTab>('candidates')

  const historyQ = useQuery({
    queryKey: ['logs', 'trades', 'dashboard'],
    queryFn: () => api.get<TradeLogPage>('/logs/trades', { params: { page: 1, page_size: 20 } }).then(r => r.data),
    staleTime: 60_000,
  })

  const candidates: (CandidateRow & { _stratLabel: string })[] = accounts.flatMap(a =>
    (a.candidates?.rows ?? []).map(r => ({ ...r, _stratLabel: a.label }))
  )

  const trades: TradeRow[] = (historyQ.data?.entries ?? []) as TradeRow[]

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--surface-soft)' }}>
      {/* Tab bar */}
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--surface-rule)', padding: '0 20px', flexShrink: 0, background: 'var(--surface)' }}>
        <button id="dlf-tab-cand" onClick={() => setTab('candidates')} className={`dlf-tab${tab === 'candidates' ? ' dlf-tab-active' : ''}`}>Candidates</button>
        <button id="dlf-tab-trades" onClick={() => setTab('trades')} className={`dlf-tab${tab === 'trades' ? ' dlf-tab-active' : ''}`}>Recent Trades</button>
        <div style={{ flex: 1 }} />
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 20px 14px' }}>
        {tab === 'candidates' ? (
          candidates.length === 0 ? (
            <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No candidates today</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  {['Ticker', 'Strategy', 'Entry', 'Target', 'ML%', 'Score'].map(h => (
                    <th key={h} className={['Entry', 'Target', 'ML%', 'Score'].includes(h) ? 'num' : undefined}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {candidates.map((c, i) => (
                  <tr key={i}>
                    <td className="sym font-mono">{c.ticker}</td>
                    <td>{c._stratLabel}</td>
                    <td className="num font-mono" style={{ color: 'var(--ink)' }}>${Number(c.entry).toFixed(2)}</td>
                    <td className="num font-mono" style={{ color: '#4ade80' }}>${Number(c.target).toFixed(2)}</td>
                    <td className="num font-mono" style={{ color: 'var(--accent)' }}>{(Number(c.ml_probability) * 100).toFixed(0)}%</td>
                    <td className="num font-mono" style={{ color: 'var(--ink)' }}>{Number(c.score).toFixed(2)}</td>
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
            <table className="data-table">
              <thead>
                <tr>
                  {['Ticker', 'Strategy', 'Date', 'P&L'].map(h => (
                    <th key={h} className={h === 'P&L' ? 'num' : undefined}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const pnl = t.pnl ?? 0
                  return (
                    <tr key={i}>
                      <td className="sym font-mono">{t.ticker ?? '—'}</td>
                      <td>{t.strategy ?? '—'}</td>
                      <td>{(t.timestamp ?? t.exit_date ?? t.date ?? '').slice(0, 10) || '—'}</td>
                      <td className="num font-mono" style={{ color: pnlColor(pnl) }}>
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
    <div style={{ padding: 16, borderBottom: '1px solid var(--surface-rule)', flexShrink: 0 }}>
      <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)', marginBottom: 10 }}>Quick Analyze</div>
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
        <button onClick={run} className="btn-primary" style={{
          padding: '9px 0',
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 700,
          width: '100%',
        }}>
          Run Analysis
        </button>
      </div>
    </div>
  )
}

interface DeepHealth {
  status?: 'healthy' | 'degraded'
  checks?: {
    ml_model?: { ok: boolean; detail?: string }
    paper_trader?: { ok: boolean; detail?: string }
    cloudflare_tunnel?: { ok: boolean; detail?: string }
    disk?: { ok: boolean; free_gb?: number; detail?: string }
    autofix_monitor?: { ok: boolean; detail?: string }
    webserver?: { ok: boolean; note?: string }
  }
}

function isMarketOpen(): boolean {
  const now = new Date()
  // Convert to US/Eastern
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const day = et.getDay() // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false
  const h = et.getHours()
  const m = et.getMinutes()
  const mins = h * 60 + m
  return mins >= 9 * 60 + 30 && mins < 16 * 60
}

interface SystemServices {
  data?: {
    paper_runner?: { running: boolean; pid?: number }
    cloudflare_tunnel?: { running: boolean }
    autofix_monitor?: { running: boolean }
    retrain?: { running: boolean; pids?: number[] }
    ml_model?: { ok: boolean; age_hours?: number; wf_roc?: number | null }
    disk?: { ok: boolean; free_gb?: number }
  }
}

function SystemStatus() {
  const mlQ = useQuery({
    queryKey: ['ml', 'status'],
    queryFn: getMlStatus,
    staleTime: 120_000,
  })
  const deepQ = useQuery({
    queryKey: ['health', 'deep'],
    queryFn: () => api.get<DeepHealth>('/health/deep').then(r => r.data),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  })
  const fidelityQ = useQuery({
    queryKey: ['fidelity', 'status', 'chip'],
    queryFn: () => api.get<{ connected?: boolean; status?: string }>('/fidelity/status').then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  })
  const servicesQ = useQuery({
    queryKey: ['system', 'services'],
    queryFn: () => api.get<SystemServices>('/system/services').then(r => r.data),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  })

  const deep = deepQ.data
  const svc = servicesQ.data?.data

  // null = unknown/loading (grey), true = ok (green), false = error (red)
  // 'info' = informational state (grey) — e.g. market closed is not an error
  function chip(label: string, ok: boolean | null | 'info', detail?: string) {
    const cls = ok === 'info' ? 'dash-chip' : ok == null ? 'dash-chip' : ok ? 'dash-chip ok' : 'dash-chip err'
    return (
      <div key={label} className={cls} title={detail ?? label}>
        <span className="dash-chip-dot" />
        <span>{label}</span>
      </div>
    )
  }

  const marketOpen = isMarketOpen()
  const mlOk = deep?.checks?.ml_model?.ok ?? (mlQ.data ? mlQ.data.bundle_exists : null)
  const fidelityOk = fidelityQ.data
    ? (fidelityQ.data.connected === true || fidelityQ.data.status === 'connected')
    : null
  const tunnelOk = deep?.checks?.cloudflare_tunnel?.ok ?? svc?.cloudflare_tunnel?.running ?? null
  const diskOk = deep?.checks?.disk?.ok ?? svc?.disk?.ok ?? null
  const diskFreeGb = deep?.checks?.disk?.free_gb ?? svc?.disk?.free_gb
  const autofixOk = deep?.checks?.autofix_monitor?.ok ?? svc?.autofix_monitor?.running ?? null
  const retrainRunning = svc?.retrain?.running ?? null
  const mlRoc = svc?.ml_model?.wf_roc

  const overallOk = deep?.status === 'healthy'
  const overallColor = deep == null ? 'var(--ink-faint)' : overallOk ? '#4ade80' : '#facc15'

  return (
    <div style={{ padding: 16, borderBottom: '1px solid var(--surface-rule)', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)' }}>System Status</div>
        {deep && (
          <span style={{ fontSize: 10, fontWeight: 700, color: overallColor }}>
            {overallOk ? '● HEALTHY' : '● DEGRADED'}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {/* Market: grey when closed (not an error), green when open */}
        {chip('Market', marketOpen ? true : 'info', marketOpen ? 'NYSE open (ET)' : 'NYSE closed (ET)')}
        {chip('ML Model', mlOk, mlRoc != null ? `WF ROC ${mlRoc.toFixed(4)}` : deep?.checks?.ml_model?.detail)}
        {fidelityOk !== null && chip('Fidelity', fidelityOk)}
        {tunnelOk !== null && chip('Tunnel', tunnelOk, deep?.checks?.cloudflare_tunnel?.detail)}
        {diskOk !== null && chip('Disk', diskOk, diskFreeGb != null ? `${diskFreeGb} GB free` : undefined)}
        {autofixOk !== null && chip('AutoFix', autofixOk)}
        {retrainRunning && chip('Retrain', 'info', 'Retrain running...')}
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
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px 10px' }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)', marginBottom: 0 }}>Portfolio Stats</div>
        <button style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}>↻</button>
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
	                width: '100%',
	                transformOrigin: 'left center',
	                transform: `scaleX(${r.wr != null ? Math.min(r.wr, 1) : 0})`,
	                background: r.wr != null ? winRateColor(r.wr) : 'var(--surface-rule)',
	                borderRadius: 2,
	                transition: 'transform .4s var(--ease-out)',
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
    queryKey: ['portfolio'],
    queryFn: () => api.get<Portfolio>('/portfolio').then(r => r.data),
    staleTime: 120_000,
  })

  const sectors = portfolioQ.data?.sector_exposure ?? portfolioQ.data?.positions?.reduce<Record<string, number>>((acc, pos) => {
    const sector = pos.sector || 'Unknown'
    acc[sector] = (acc[sector] ?? 0) + (pos.market_value || 0)
    return acc
  }, {}) ?? {}
  const totalExposure = Object.values(sectors).reduce((sum, value) => sum + value, 0)
  const sectorEntries = Object.entries(sectors).sort((a, b) => b[1] - a[1])
  const maxVal = sectorEntries.length ? sectorEntries[0][1] : 1

  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)' }}>
      <div style={{ padding: '12px 16px 10px' }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)' }}>Portfolio Exposure</div>
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
                <span style={{ fontSize: 11, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
                  {totalExposure > 0 ? ((pct / totalExposure) * 100).toFixed(1) : '0.0'}%
                </span>
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
  const navigate = useNavigate()

  const candidates = accounts.flatMap(a =>
    (a.candidates?.rows ?? []).slice(0, 4).map(r => ({ ...r, _stratLabel: a.label, _strategy: a.strategy }))
  ).slice(0, 10)

  // Fetch news for top 4 unique candidate tickers in parallel
  const candTickers = [...new Set(candidates.map(c => c.ticker?.toUpperCase()).filter(Boolean))].slice(0, 4) as string[]
  const newsResults = useQueries({
    queries: candTickers.map(sym => ({
      queryKey: ['market', 'news', sym],
      queryFn: () => api.get<NewsApiResponse>(`/market/news?symbol=${sym}`).then(r => r.data),
      staleTime: 240_000,
    }))
  })

  // Combine news, filter by candidate ticker mention, dedupe by title
  const tickerSet = new Set(candTickers)
  const seenTitles = new Set<string>()
  const candNews: NewsItem[] = newsResults
    .flatMap(r => r.data?.news ?? [])
    .filter(n => {
      if (seenTitles.has(n.title)) return false
      seenTitles.add(n.title)
      const text = (n.title + ' ' + (n.summary ?? '')).toUpperCase()
      return [...tickerSet].some(t => text.includes(t))
    })
    .slice(0, 4)

  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--surface-rule)' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px 10px' }}>
        <div style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', color: 'var(--ink-faint)' }}>
          Paper Candidates
        </div>
        <span style={{ fontSize: 10, color: 'var(--ink-faint)', background: 'var(--surface-soft)', padding: '1px 6px', borderRadius: 999 }}>
          {candidates.length}
        </span>
      </div>

      {/* Candidate cards */}
      <div id="dash-candidates" style={{ padding: '0 12px 12px' }}>
        {candidates.length === 0 ? (
          <div style={{ padding: '20px 4px', fontSize: 12, color: 'var(--ink-faint)', textAlign: 'center' as const }}>
            No candidates today
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 6 }}>
            {candidates.map((c, i) => {
              const entry = Number(c.entry)
              const target = Number(c.target)
              const stop = Number(c.stop)
              const rr = (entry > 0 && entry > stop && stop > 0)
                ? ((target - entry) / (entry - stop))
                : null
              const ml = Number(c.ml_probability) * 100
              const gateOk = (c.gate_status ?? '') === 'PASS'
              const stratColor = STRATEGY_COLORS[c._strategy ?? ''] ?? '#94a3b8'
              return (
                <div
                  key={i}
                  onClick={() => navigate(`/analyze?ticker=${encodeURIComponent(c.ticker)}`)}
                  style={{
                    background: 'var(--surface-soft)',
                    border: '1px solid var(--surface-rule)',
                    borderLeft: `3px solid ${stratColor}`,
                    borderRadius: 6,
                    padding: '10px 12px',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--canvas)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'var(--surface-soft)')}
                >
                  {/* Top row: ticker + gate badge */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 14, fontWeight: 800, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
                        {c.ticker}
                      </span>
                      <span style={{ fontSize: 9, color: stratColor, background: `${stratColor}18`, padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>
                        {c._stratLabel}
                      </span>
                    </div>
                    {c.gate_status && (
                      <span style={{
                        fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3,
                        background: gateOk ? 'rgba(74,222,128,0.15)' : 'rgba(251,191,36,0.15)',
                        color: gateOk ? '#4ade80' : '#fbbf24',
                      }}>
                        {c.gate_status}
                      </span>
                    )}
                  </div>

                  {/* Price trio */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4, marginBottom: 6 }}>
                    {[
                      { label: 'Entry', value: `$${entry.toFixed(2)}`, color: 'var(--ink)' },
                      { label: 'Target', value: `$${target.toFixed(2)}`, color: '#4ade80' },
                      { label: 'Stop', value: `$${stop.toFixed(2)}`, color: '#f87171' },
                    ].map(p => (
                      <div key={p.label} style={{ textAlign: 'center' as const }}>
                        <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 1 }}>{p.label}</div>
                        <div style={{ fontSize: 11, fontWeight: 700, color: p.color, fontFamily: 'var(--font-mono)' }}>{p.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Stats row: R:R, ML%, Score */}
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {rr !== null && (
                      <span style={{
                        fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 700,
                        color: rr >= 2 ? '#4ade80' : rr >= 1 ? '#fbbf24' : '#f87171',
                      }}>
                        R:R {rr.toFixed(1)}
                      </span>
                    )}
                    <span style={{ fontSize: 10, color: 'var(--accent)', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
                      ML {ml.toFixed(0)}%
                    </span>
                    {c.score != null && (
                      <span style={{ fontSize: 10, color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}>
                        Score {Number(c.score).toFixed(0)}
                      </span>
                    )}
                    <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--ink-faint)' }}>→</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Candidate news section — only show if there are relevant news items */}
      {candNews.length > 0 && (
        <div style={{ borderTop: '1px solid var(--surface-rule)', padding: '10px 16px 12px' }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.08em', color: 'var(--ink-faint)', marginBottom: 8 }}>
            Candidate News
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 6 }}>
            {candNews.map((n, i) => (
              <div
                key={i}
                onClick={() => window.open(n.url, '_blank', 'noopener')}
                style={{ cursor: 'pointer', padding: '6px 8px', borderRadius: 5, background: 'var(--surface-soft)' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--canvas)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'var(--surface-soft)')}
              >
                <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>
                  {n.source} · {timeAgo(n.published)}
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 600, lineHeight: 1.35 }}>
                  {n.title}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function WatchlistPanel() {
  const navigate = useNavigate()
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
    <div style={{ flexShrink: 0 }}>
      <div style={{ padding: '12px 16px 10px' }}>
        <div className="dash-section-label" style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-faint)' }}>Watchlist</div>
      </div>
      <div>
        {tickers.slice(0, 10).map(t => {
          const q: Quote | undefined = quotes[t]
          const pct = q?.change_pct ?? null
          const price = q?.price ?? null
          return (
            <div
              key={t}
              className="dash-watch-row"
              onClick={() => navigate(`/analyze?ticker=${encodeURIComponent(t)}`)}
              style={{ padding: '8px 14px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer' }}
            >
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
    <div id="panel-dashboard" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Ticker tape — full width, flush */}
      <TickerTape />

      {/* Stat row — full width, flush */}
      <StatRow paperQ={paperQ} />

      {/* Main grid: left 1fr | right 320px */}
      <div id="dash-main-grid" style={{
        display: 'grid',
        gridTemplateColumns: narrow ? '1fr' : '1fr 380px',
        flex: 1,
        overflow: 'hidden',
        minHeight: 0,
      }}>
        {/* LEFT COLUMN */}
        <div style={{ borderRight: narrow ? 'none' : '1px solid var(--surface-rule)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <MarketChartPanel />
          <OpportunitiesPanel />
          <NewsPanel />
          <LiveFeedPanel accounts={accounts} />
        </div>

        {/* RIGHT COLUMN */}
        <div style={{ overflowY: 'auto', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>
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
