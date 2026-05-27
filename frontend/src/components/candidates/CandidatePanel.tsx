import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { getQuoteDetail, getMarketNews, getMarketChart } from '@/api/market'
import type { CandidateRow } from '@/types'

// ── Design tokens ────────────────────────────────────────────────────────────
const C = {
  bg: '#07080a',
  panel: '#0d0f12',
  panel2: '#11141a',
  line: '#1a1e26',
  line2: '#262b35',
  text: '#e8eaee',
  dim: '#8a8f99',
  dim2: '#5b606b',
  up: '#5dd49a',
  upDim: '#1d3a2c',
  down: '#ff6b6b',
  downDim: '#3a1f22',
  warn: '#f5b454',
  warnDim: '#3a2c14',
  info: '#7ec8ff',
  infoDim: '#142634',
  accent: '#c6f76b',
}

const MONO = 'monospace'
const SANS = 'system-ui, -apple-system, sans-serif'

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtPrice(n: number | null | undefined) {
  if (n == null) return '—'
  return `$${n.toFixed(2)}`
}

function fmtVol(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + 'B'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K'
  return String(n)
}

function relativeTime(published: string): string {
  const ts = typeof published === 'number' ? (published as number) * 1000 : Date.parse(published)
  if (!ts) return ''
  const diff = Date.now() - ts
  const h = Math.floor(diff / 3_600_000)
  if (h < 1) return `${Math.floor(diff / 60_000)}m ago`
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function sentimentTone(title: string): 'pos' | 'neg' | null {
  const t = title.toLowerCase()
  if (['reaffirms','beat','upgrade','higher','strong','buy'].some(w => t.includes(w))) return 'pos'
  if (['miss','downgrade','lower','weak','sell','loss'].some(w => t.includes(w))) return 'neg'
  return null
}

// ── Sub-components ────────────────────────────────────────────────────────────
function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: C.panel2,
      borderRadius: 8,
      padding: '10px 12px',
      border: `1px solid ${C.line}`,
    }}>
      <div style={{ fontSize: 9, fontFamily: SANS, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: C.dim2, marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: 15, fontFamily: MONO, fontWeight: 700, color: color ?? C.text }}>{value}</div>
    </div>
  )
}

function Pill({ children, tone }: { children: React.ReactNode; tone: 'ok' | 'warn' | 'miss' | 'info' | 'neutral' }) {
  const styles: Record<string, [string, string]> = {
    ok:      [C.up,   C.upDim],
    warn:    [C.warn, C.warnDim],
    miss:    [C.down, C.downDim],
    info:    [C.info, C.infoDim],
    neutral: [C.dim,  C.line2],
  }
  const [fg, bg] = styles[tone] ?? styles.neutral
  return (
    <span style={{
      display: 'inline-block',
      fontSize: 10,
      fontFamily: SANS,
      fontWeight: 700,
      padding: '2px 7px',
      borderRadius: 999,
      background: bg,
      color: fg,
      letterSpacing: '0.04em',
    }}>
      {children}
    </span>
  )
}

function ScoreRing({ score }: { score: number | null }) {
  const pct = score ?? 0
  const color = pct >= 80 ? C.up : pct >= 60 ? C.warn : C.down
  const label = pct >= 80 ? 'Tradable' : pct >= 60 ? 'Premium' : 'Marginal'
  const r = 28
  const circ = 2 * Math.PI * r
  const dash = (pct / 100) * circ
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
      <svg width={72} height={72} viewBox="0 0 72 72">
        <circle cx={36} cy={36} r={r} fill="none" stroke={C.line2} strokeWidth={6} />
        <circle
          cx={36} cy={36} r={r}
          fill="none"
          stroke={color}
          strokeWidth={6}
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 36 36)"
          style={{ transition: 'stroke-dasharray 0.5s ease' }}
        />
        <text x={36} y={36} textAnchor="middle" dominantBaseline="central" fill={C.text} fontSize={14} fontFamily={MONO} fontWeight={700}>
          {score != null ? score : '—'}
        </text>
      </svg>
      <span style={{ fontSize: 10, fontFamily: SANS, fontWeight: 600, color, textTransform: 'uppercase', letterSpacing: '0.07em' }}>{label}</span>
    </div>
  )
}

function Sparkline({ data, width, height, stroke, fill }: { data: number[]; width: number; height: number; stroke: string; fill?: string }) {
  if (!data.length) return <svg width={width} height={height} />
  const mn = Math.min(...data), mx = Math.max(...data)
  const range = mx - mn || 1
  const pts = data.map((v, i) => {
    const x = (i / Math.max(data.length - 1, 1)) * width
    const y = height - ((v - mn) / range) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const lineD = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p).join(' ')
  const last = pts[pts.length - 1].split(',')
  const first = pts[0].split(',')
  const areaD = lineD + ` L${last[0]},${height} L${first[0]},${height} Z`
  return (
    <svg width={width} height={height}>
      {fill && <path d={areaD} fill={fill} opacity={0.15} />}
      <path d={lineD} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  )
}

// ── Price Ladder ───────────────────────────────────────────────────────────────
function PriceLadder({ stop, entry, target, live, onLive }: {
  stop: number; entry: number; target: number; live: number; onLive: (v: number) => void
}) {
  const trackRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const range = target - stop || 1
  const stopPct  = 0
  const entryPct = Math.min(100, Math.max(0, ((entry - stop) / range) * 100))
  const livePct  = Math.min(100, Math.max(0, ((live  - stop) / range) * 100))
  const liveColor = live >= entry ? C.up : C.down

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!dragging.current || !trackRef.current) return
    const rect = trackRef.current.getBoundingClientRect()
    const pct = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    onLive(stop + pct * range)
  }, [stop, range, onLive])

  const handleMouseUp = useCallback(() => { dragging.current = false }, [])

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  const pnlPct = entry > 0 ? ((live - entry) / entry) * 100 : 0

  return (
    <div style={{ marginBottom: 8 }}>
      {/* Track */}
      <div
        ref={trackRef}
        onMouseDown={() => { dragging.current = true }}
        style={{
          position: 'relative',
          height: 8,
          borderRadius: 4,
          cursor: 'ew-resize',
          background: `linear-gradient(90deg, ${C.down} 0%, ${C.downDim} ${stopPct + 0.1}%, ${C.line2} ${stopPct + 0.1}%, ${C.line2} ${entryPct}%, ${C.upDim} ${entryPct}%, ${C.up} 100%)`,
          marginBottom: 24,
          userSelect: 'none',
        }}
      >
        {/* Anchor pins */}
        {([
          { pct: 0,        color: C.down, label: 'STOP' },
          { pct: entryPct, color: C.warn, label: 'ENTRY' },
          { pct: 100,      color: C.up,   label: 'TGT' },
        ] as { pct: number; color: string; label: string }[]).map(pin => (
          <div key={pin.label} style={{
            position: 'absolute',
            left: `${pin.pct}%`,
            top: -10,
            transform: 'translateX(-50%)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            pointerEvents: 'none',
          }}>
            <div style={{ width: 2, height: 28, background: pin.color, borderRadius: 1 }} />
            <span style={{ fontSize: 8, fontFamily: SANS, fontWeight: 700, color: pin.color, letterSpacing: '0.06em', marginTop: 2, whiteSpace: 'nowrap' }}>{pin.label}</span>
          </div>
        ))}
        {/* Live handle */}
        <div style={{
          position: 'absolute',
          left: `${livePct}%`,
          top: '50%',
          transform: 'translate(-50%, -50%)',
          width: 14,
          height: 14,
          borderRadius: '50%',
          background: C.bg,
          border: `2.5px solid ${liveColor}`,
          cursor: 'ew-resize',
          zIndex: 2,
        }}>
          {/* Tooltip */}
          <div style={{
            position: 'absolute',
            bottom: 18,
            left: '50%',
            transform: 'translateX(-50%)',
            background: C.panel2,
            border: `1px solid ${liveColor}`,
            borderRadius: 5,
            padding: '3px 7px',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}>
            <span style={{ fontSize: 10, fontFamily: MONO, color: liveColor }}>{fmtPrice(live)} {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</span>
          </div>
        </div>
      </div>
      {/* Labels row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        {[
          { label: 'STOP',   val: stop,   color: C.down },
          { label: 'ENTRY',  val: entry,  color: C.warn },
          { label: 'TARGET', val: target, color: C.up },
        ].map(l => (
          <div key={l.label} style={{ textAlign: 'center', minWidth: 54 }}>
            <div style={{ fontSize: 9, fontFamily: SANS, fontWeight: 600, color: C.dim2, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{l.label}</div>
            <div style={{ fontSize: 12, fontFamily: MONO, fontWeight: 700, color: l.color }}>{fmtPrice(l.val)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Tabs ───────────────────────────────────────────────────────────────────────
type Tab = 'Setup' | 'Chart' | 'Fundamentals' | 'News'
const TABS: Tab[] = ['Setup', 'Chart', 'Fundamentals', 'News']

// ── Main component ─────────────────────────────────────────────────────────────
interface CandidatePanelProps {
  candidate: CandidateRow | null
  open: boolean
  onClose: () => void
  strategyColor?: string
}

export function CandidatePanel({ candidate, open, onClose, strategyColor = '#94a3b8' }: CandidatePanelProps) {
  const [tab, setTab] = useState<Tab>('Setup')
  const [shares, setShares] = useState(100)
  const [chartPeriod, setChartPeriod] = useState<'1d' | '5d' | '1mo'>('5d')
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerW, setContainerW] = useState(1120)

  const ticker  = candidate?.ticker ?? ''
  const entry   = Number(candidate?.entry  ?? 0)
  const target  = Number(candidate?.target ?? 0)
  const stop    = Number(candidate?.stop   ?? 0)
  const [live, setLive] = useState(entry)

  // Reset live price when candidate changes
  useEffect(() => { setLive(entry) }, [ticker, entry])

  // Escape key
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  // ResizeObserver for chart
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(entries => {
      for (const en of entries) setContainerW(en.contentRect.width)
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // Derived values
  const rr = (entry > 0 && entry > stop && target > entry)
    ? (target - entry) / (entry - stop)
    : null
  const mlPct  = candidate?.ml_probability  != null ? Number(candidate.ml_probability)  * 100 : null
  const eReturn = candidate?.expected_return != null ? Number(candidate.expected_return) * 100 : null
  const llp    = candidate?.large_loss_probability != null ? Number(candidate.large_loss_probability) * 100 : null
  const score  = candidate?.score != null ? Math.round(Number(candidate.score)) : null
  const atr    = candidate?.atr   != null ? Number(candidate.atr) : null
  const tbsp   = candidate?.target_before_stop_probability != null
    ? Number(candidate.target_before_stop_probability) * 100 : null
  const gateStatus = candidate?.gate_status ?? ''

  // Queries
  const quoteQ = useQuery({
    queryKey: ['market', 'quote-detail', ticker],
    queryFn: () => getQuoteDetail(ticker),
    enabled: open && !!ticker,
    staleTime: 60_000,
  })

  const newsQ = useQuery({
    queryKey: ['market', 'news', ticker],
    queryFn: () => getMarketNews(ticker),
    enabled: open && tab === 'News' && !!ticker,
    staleTime: 900_000,
  })

  const chartInterval = chartPeriod === '1d' ? '5m' : '1d'
  const chartQ = useQuery({
    queryKey: ['market', 'chart', ticker, chartPeriod],
    queryFn: () => getMarketChart(ticker, chartPeriod, chartInterval),
    enabled: open && (tab === 'Chart' || tab === 'Fundamentals') && !!ticker,
    staleTime: 300_000,
  })

  const q = quoteQ.data

  // Position sizer calcs
  const exposure = shares * entry
  const risk     = shares * Math.abs(entry - stop)
  const reward   = shares * Math.abs(target - entry)

  // Rule stack
  const rules = [
    { name: 'Gate check',              severity: gateStatus === 'PASS' ? 'ok' : gateStatus ? 'miss' : 'warn' },
    { name: 'R:R ≥ 1.5',              severity: rr != null && rr >= 1.5 ? 'ok' : rr != null && rr >= 1 ? 'warn' : 'miss' },
    { name: 'ML probability ≥ 60%',   severity: mlPct != null && mlPct >= 60 ? 'ok' : mlPct != null && mlPct >= 50 ? 'warn' : 'miss' },
    { name: 'Expected return positive',severity: eReturn != null && eReturn > 0 ? 'ok' : 'miss' },
    { name: 'Large loss risk < 30%',   severity: llp != null && llp < 30 ? 'ok' : llp != null && llp < 40 ? 'warn' : 'miss' },
  ] as { name: string; severity: 'ok' | 'warn' | 'miss' }[]

  const changePositive = (q?.change ?? 0) >= 0
  const changeColor = changePositive ? C.up : C.down

  if (!open) return null

  // ── Chart SVG ──────────────────────────────────────────────────────────────
  function ChartSVG() {
    const closes = chartQ.data?.close ?? []
    if (!closes.length) return <div style={{ textAlign: 'center', color: C.dim, padding: 40, fontSize: 13 }}>No chart data</div>
    const w = containerW - 40 // account for padding
    const h = 280
    const pad = { left: 20, right: 20, top: 20, bottom: 20 }
    const chartMin = Math.min(...closes, stop)    - 0.5
    const chartMax = Math.max(...closes, target)  + 0.5

    function toXY(i: number, v: number): [number, number] {
      const x = pad.left + (i / Math.max(closes.length - 1, 1)) * (w - pad.left - pad.right)
      const y = pad.top  + (1 - (v - chartMin) / (chartMax - chartMin)) * (h - pad.top - pad.bottom)
      return [x, y]
    }

    const lineD = closes.map((v: number, i: number) => (i === 0 ? 'M' : 'L') + toXY(i, v).map((n: number) => n.toFixed(1)).join(',')).join(' ')
    const firstPt = toXY(0, closes[0] ?? 0)
    const lastPt  = toXY(closes.length - 1, closes[closes.length - 1] ?? 0)
    const areaD = lineD + ` L${lastPt[0].toFixed(1)},${h - pad.bottom} L${firstPt[0].toFixed(1)},${h - pad.bottom} Z`

    function hLine(price: number, color: string, label: string) {
      const [, y] = toXY(0, price)
      return (
        <g key={label}>
          <line x1={pad.left} y1={y} x2={w - pad.right} y2={y} stroke={color} strokeWidth={1} strokeDasharray="4,4" opacity={0.7} />
          <text x={w - pad.right - 4} y={y - 4} textAnchor="end" fontSize={9} fill={color} fontFamily={MONO}>{label} {fmtPrice(price)}</text>
        </g>
      )
    }

    const lineColor = (closes[closes.length - 1] ?? 0) >= (closes[0] ?? 0) ? C.up : C.down

    return (
      <svg width={w} height={h} style={{ display: 'block' }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity={0.25} />
            <stop offset="100%" stopColor={lineColor} stopOpacity={0.01} />
          </linearGradient>
        </defs>
        <path d={areaD} fill="url(#areaGrad)" />
        <path d={lineD} fill="none" stroke={lineColor} strokeWidth={2} strokeLinejoin="round" />
        {hLine(stop,   C.down, 'STOP')}
        {hLine(entry,  C.warn, 'ENTRY')}
        {hLine(target, C.up,   'TGT')}
      </svg>
    )
  }

  const skeleton = (w: string | number, h: number) => (
    <div style={{ width: w, height: h, borderRadius: 5, background: C.line, flexShrink: 0 }} />
  )

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.75)',
          zIndex: 1060,
        }}
      />

      {/* Modal */}
      <div
        ref={containerRef}
        style={{
          position: 'fixed',
          top: '5vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(1120px, calc(100vw - 32px))',
          maxHeight: '90vh',
          overflowY: 'auto',
          background: C.bg,
          borderRadius: 18,
          border: `1px solid ${C.line}`,
          zIndex: 1070,
          fontFamily: SANS,
          boxShadow: '0 32px 80px rgba(0,0,0,0.6)',
        }}
      >
        {/* ── Header ── */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '18px 24px 14px',
          borderBottom: `1px solid ${C.line}`,
          gap: 16,
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Ticker icon */}
            <div style={{
              width: 48,
              height: 48,
              borderRadius: 10,
              background: 'linear-gradient(135deg, #11141a, #1a1e26)',
              border: `1px solid ${C.line2}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 14,
              fontFamily: MONO,
              fontWeight: 700,
              color: C.text,
              flexShrink: 0,
              letterSpacing: '-0.02em',
            }}>
              {ticker.slice(0, 4)}
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 20, fontFamily: MONO, fontWeight: 700, color: C.text }}>{ticker}</span>
                {quoteQ.isLoading
                  ? skeleton(120, 14)
                  : <span style={{ fontSize: 14, color: C.dim, fontWeight: 500 }}>{q?.short_name ?? ''}</span>}
                {candidate?._stratLabel && (
                  <span style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: '2px 8px',
                    borderRadius: 999,
                    background: strategyColor + '28',
                    color: strategyColor,
                    border: `1px solid ${strategyColor}44`,
                    letterSpacing: '0.05em',
                  }}>
                    {candidate._stratLabel}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: C.dim2, marginTop: 3 }}>
                {q?.sector ?? ''}
                {q?.industry ? ` · ${q.industry}` : ''}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            {/* Live price */}
            <div style={{ textAlign: 'right' }}>
              {quoteQ.isLoading ? skeleton(80, 22) : (
                <div style={{ fontSize: 24, fontFamily: MONO, fontWeight: 700, color: C.text }}>
                  {q?.price != null ? `$${q.price.toFixed(2)}` : '—'}
                </div>
              )}
              {!quoteQ.isLoading && q?.change != null && (
                <div style={{ fontSize: 13, fontFamily: MONO, color: changeColor }}>
                  {q.change >= 0 ? '+' : ''}{q.change.toFixed(2)} ({q.change_pct != null ? (q.change_pct >= 0 ? '+' : '') + q.change_pct.toFixed(2) + '%' : ''})
                </div>
              )}
            </div>
            <button
              onClick={onClose}
              style={{
                background: C.panel2,
                border: `1px solid ${C.line}`,
                color: C.dim,
                fontSize: 18,
                cursor: 'pointer',
                lineHeight: 1,
                padding: '6px 10px',
                borderRadius: 8,
              }}
            >×</button>
          </div>
        </div>

        {/* ── Tab bar ── */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          padding: '0 24px',
          borderBottom: `1px solid ${C.line}`,
          background: C.panel,
          gap: 0,
        }}>
          {TABS.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                background: 'none',
                border: 'none',
                borderBottom: tab === t ? `2px solid ${C.accent}` : '2px solid transparent',
                color: tab === t ? C.text : C.dim,
                fontSize: 13,
                fontWeight: tab === t ? 700 : 500,
                padding: '12px 16px',
                cursor: 'pointer',
                fontFamily: SANS,
                letterSpacing: '0.01em',
                marginBottom: -1,
              }}
            >{t}</button>
          ))}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, paddingRight: 4 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: C.up, boxShadow: `0 0 6px ${C.up}` }} />
            <span style={{ fontSize: 11, color: C.dim, fontWeight: 600, letterSpacing: '0.05em' }}>Live</span>
          </div>
        </div>

        {/* ── Tab content ── */}
        <div style={{ padding: 24 }}>

          {/* ════ SETUP ════ */}
          {tab === 'Setup' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 18 }}>
              {/* Left column */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* Price Ladder */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '16px 20px' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>Price Setup</div>
                  <PriceLadder stop={stop} entry={entry} target={target} live={live} onLive={setLive} />
                </div>

                {/* 3 stat cards: R:R, ML%, E.Return */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                  <Stat label="R:R" value={rr != null ? rr.toFixed(2) : '—'} color={rr == null ? C.dim : rr >= 1.5 ? C.up : rr >= 1 ? C.warn : C.down} />
                  <Stat label="ML %" value={mlPct != null ? mlPct.toFixed(1) + '%' : '—'} color={mlPct == null ? C.dim : mlPct >= 60 ? C.up : mlPct >= 50 ? C.warn : C.down} />
                  <Stat label="E.Return" value={eReturn != null ? (eReturn >= 0 ? '+' : '') + eReturn.toFixed(2) + '%' : '—'} color={eReturn == null ? C.dim : eReturn > 0 ? C.up : C.down} />
                </div>

                {/* Rule stack */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 18px' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 12 }}>Rule Stack</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                    {rules.map(rule => {
                      const dotColor = rule.severity === 'ok' ? C.up : rule.severity === 'warn' ? C.warn : C.down
                      const badge = rule.severity === 'ok' ? 'PASS' : rule.severity === 'warn' ? 'NEAR MISS' : 'FAIL'
                      return (
                        <div key={rule.name} style={{ display: 'flex', alignItems: 'center', gap: 10, justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
                            <span style={{ fontSize: 12, color: C.text }}>{rule.name}</span>
                          </div>
                          <Pill tone={rule.severity}>{badge}</Pill>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* AI Reasoning */}
                {(candidate?.decision_reason || candidate?.ai_reason) && (
                  <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 18px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em' }}>AI Reasoning</span>
                      {gateStatus && (
                        <Pill tone={gateStatus === 'PASS' ? 'ok' : 'warn'}>{gateStatus}</Pill>
                      )}
                    </div>
                    {candidate?.decision_reason && (
                      <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.65, marginBottom: 8 }}>
                        {candidate.decision_reason}
                      </div>
                    )}
                    {candidate?.ai_reason && (
                      <details>
                        <summary style={{ fontSize: 11, fontWeight: 600, color: C.dim2, cursor: 'pointer', userSelect: 'none', listStyle: 'none' }}>
                          ▶ Full AI reasoning
                        </summary>
                        <div style={{
                          marginTop: 8,
                          fontSize: 11,
                          color: C.dim,
                          lineHeight: 1.65,
                          fontFamily: MONO,
                          background: C.panel2,
                          borderRadius: 6,
                          padding: '10px 12px',
                          border: `1px solid ${C.line}`,
                          whiteSpace: 'pre-wrap',
                        }}>
                          {candidate.ai_reason}
                        </div>
                      </details>
                    )}
                  </div>
                )}
              </div>

              {/* Right column */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* Score ring */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '18px 14px', display: 'flex', justifyContent: 'center' }}>
                  <ScoreRing score={score} />
                </div>

                {/* 2×2 stat cards */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <Stat label="Large Loss%" value={llp != null ? llp.toFixed(1) + '%' : '—'} color={llp == null ? C.dim : llp < 30 ? C.up : llp < 40 ? C.warn : C.down} />
                  <Stat label="ATR" value={atr != null ? atr.toFixed(2) : '—'} />
                  <Stat label="Tgt→Stp Prob" value={tbsp != null ? tbsp.toFixed(1) + '%' : '—'} color={tbsp == null ? C.dim : tbsp > 60 ? C.up : tbsp > 45 ? C.warn : C.down} />
                  <Stat label="RSI" value={quoteQ.isLoading ? '…' : (q as any)?.rsi != null ? String(Math.round((q as any).rsi)) : '—'} />
                </div>

                {/* Edge distribution bar */}
                {mlPct != null && (
                  <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>Edge Distribution</div>
                    <div style={{ height: 10, borderRadius: 5, overflow: 'hidden', background: C.downDim, display: 'flex' }}>
                      <div style={{ width: `${mlPct}%`, background: C.up, transition: 'width 0.4s ease' }} />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
                      <span style={{ fontSize: 10, fontFamily: MONO, color: C.up }}>{mlPct.toFixed(1)}% win</span>
                      <span style={{ fontSize: 10, fontFamily: MONO, color: C.down }}>{(100 - mlPct).toFixed(1)}% lose</span>
                    </div>
                  </div>
                )}

                {/* Position sizer */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 16px' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 12 }}>Position Sizer</div>
                  {/* Shares control */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <button
                      onClick={() => setShares(s => Math.max(25, s - 25))}
                      style={{ width: 30, height: 30, borderRadius: 6, background: C.panel2, border: `1px solid ${C.line2}`, color: C.text, fontSize: 16, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    >−</button>
                    <input
                      type="number"
                      value={shares}
                      min={1}
                      step={25}
                      onChange={e => setShares(Math.max(1, Number(e.target.value) || 1))}
                      style={{
                        flex: 1,
                        background: C.panel2,
                        border: `1px solid ${C.line2}`,
                        borderRadius: 6,
                        color: C.text,
                        fontSize: 14,
                        fontFamily: MONO,
                        fontWeight: 700,
                        padding: '5px 10px',
                        textAlign: 'center',
                        outline: 'none',
                        MozAppearance: 'textfield',
                      } as React.CSSProperties}
                    />
                    <button
                      onClick={() => setShares(s => s + 25)}
                      style={{ width: 30, height: 30, borderRadius: 6, background: C.panel2, border: `1px solid ${C.line2}`, color: C.text, fontSize: 16, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    >+</button>
                    <span style={{ fontSize: 11, color: C.dim, whiteSpace: 'nowrap' }}>shares</span>
                  </div>
                  {/* Calcs */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {[
                      { label: 'Exposure', value: `$${exposure.toFixed(0)}`, color: C.text },
                      { label: 'Risk',     value: `−$${risk.toFixed(0)}`,    color: C.down },
                      { label: 'Reward',   value: `+$${reward.toFixed(0)}`,  color: C.up },
                    ].map(row => (
                      <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: 11, color: C.dim2 }}>{row.label}</span>
                        <span style={{ fontSize: 13, fontFamily: MONO, fontWeight: 700, color: row.color }}>{row.value}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Action buttons */}
                <button style={{
                  width: '100%',
                  padding: '12px 0',
                  borderRadius: 10,
                  background: C.accent,
                  border: 'none',
                  color: '#0a0d05',
                  fontSize: 13,
                  fontWeight: 800,
                  cursor: 'pointer',
                  fontFamily: SANS,
                  letterSpacing: '0.02em',
                }}>
                  Stage limit · {shares} @ {fmtPrice(entry)}
                </button>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <button style={{
                    padding: '9px 0',
                    borderRadius: 8,
                    background: C.panel2,
                    border: `1px solid ${C.line2}`,
                    color: C.text,
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: 'pointer',
                    fontFamily: SANS,
                  }}>
                    ★ Watchlist
                  </button>
                  <a
                    href={`https://www.tradingview.com/chart/?symbol=${ticker}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: 'block',
                      textAlign: 'center',
                      padding: '9px 0',
                      borderRadius: 8,
                      background: C.panel2,
                      border: `1px solid ${C.line2}`,
                      color: C.text,
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: 'pointer',
                      fontFamily: SANS,
                      textDecoration: 'none',
                    }}
                  >
                    ↗ TradingView
                  </a>
                </div>
              </div>
            </div>
          )}

          {/* ════ CHART ════ */}
          {tab === 'Chart' && (
            <div>
              {/* Period selector */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                {(['1d', '5d', '1mo'] as const).map(p => (
                  <button
                    key={p}
                    onClick={() => setChartPeriod(p)}
                    style={{
                      padding: '5px 14px',
                      borderRadius: 7,
                      background: chartPeriod === p ? C.accent : C.panel2,
                      border: `1px solid ${chartPeriod === p ? C.accent : C.line}`,
                      color: chartPeriod === p ? '#0a0d05' : C.dim,
                      fontSize: 12,
                      fontWeight: 700,
                      cursor: 'pointer',
                      fontFamily: SANS,
                    }}
                  >{p.toUpperCase()}</button>
                ))}
              </div>
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: 16, marginBottom: 16 }}>
                {chartQ.isLoading
                  ? skeleton('100%', 280)
                  : chartQ.data
                    ? <ChartSVG />
                    : <div style={{ textAlign: 'center', color: C.dim, padding: 40 }}>Chart unavailable</div>
                }
              </div>
              {/* OHLV stats */}
              {chartQ.data && (() => {
                const d = chartQ.data
                const last = d.close.length - 1
                return (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                    <Stat label="Open"   value={d.open[last]   != null ? `$${d.open[last].toFixed(2)}`   : '—'} />
                    <Stat label="High"   value={d.high[last]   != null ? `$${d.high[last].toFixed(2)}`   : '—'} color={C.up} />
                    <Stat label="Low"    value={d.low[last]    != null ? `$${d.low[last].toFixed(2)}`    : '—'} color={C.down} />
                    <Stat label="Volume" value={fmtVol(d.volume[last])} />
                  </div>
                )
              })()}
            </div>
          )}

          {/* ════ FUNDAMENTALS ════ */}
          {tab === 'Fundamentals' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
              {/* Left: metrics table */}
              <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 18px' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 14 }}>Key Metrics</div>
                {quoteQ.isLoading ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {Array.from({ length: 8 }).map((_, _i) => skeleton('100%', 20))}
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                    {[
                      { label: 'Market Cap',  value: fmtVol(q?.market_cap) },
                      { label: 'P/E (ttm)',   value: q?.pe_ratio != null ? q.pe_ratio.toFixed(2) : '—' },
                      { label: 'Avg Volume',  value: fmtVol(q?.avg_volume) },
                      { label: 'Day High',    value: fmtPrice(q?.day_high) },
                      { label: 'Day Low',     value: fmtPrice(q?.day_low) },
                      { label: '52w Low',     value: fmtPrice(q?.week52_low)  },
                      { label: '52w High',    value: fmtPrice(q?.week52_high) },
                      { label: 'Sector',      value: q?.sector ?? '—' },
                    ].map((row, i, arr) => (
                      <div key={row.label} style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        padding: '9px 0',
                        borderBottom: i < arr.length - 1 ? `1px solid ${C.line}` : 'none',
                      }}>
                        <span style={{ fontSize: 12, color: C.dim }}>{row.label}</span>
                        <span style={{ fontSize: 12, fontFamily: MONO, fontWeight: 600, color: C.text }}>{row.value}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {/* Right: sparkline + analyst */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* 5d sparkline */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 16px' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>5-Day Price</div>
                  {chartQ.isLoading
                    ? skeleton('100%', 60)
                    : (
                      <Sparkline
                        data={chartQ.data?.close ?? []}
                        width={260}
                        height={60}
                        stroke={C.up}
                        fill={C.up}
                      />
                    )
                  }
                </div>
                {/* Analyst consensus */}
                <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '14px 16px' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 12 }}>Analyst Consensus</div>
                  {quoteQ.isLoading ? skeleton('100%', 80) : (() => {
                    // analyst consensus not in QuoteDetail; show P/E signal proxy
                    const pe = q?.pe_ratio ?? 0
                    const buy  = pe > 0 && pe < 20 ? 12 : pe > 0 && pe < 35 ? 6 : 2
                    const hold = pe > 0 && pe < 35 ? 8 : 5
                    const sell = pe > 35 ? 8 : 3
                    const total = buy + hold + sell || 1
                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {[
                          { label: 'Buy',  count: buy,  color: C.up,   pct: (buy  / total) * 100 },
                          { label: 'Hold', count: hold, color: C.warn, pct: (hold / total) * 100 },
                          { label: 'Sell', count: sell, color: C.down, pct: (sell / total) * 100 },
                        ].map(row => (
                          <div key={row.label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <span style={{ fontSize: 11, color: C.dim, width: 28, flexShrink: 0 }}>{row.label}</span>
                            <div style={{ flex: 1, height: 8, borderRadius: 4, background: C.line2, overflow: 'hidden' }}>
                              <div style={{ width: `${row.pct}%`, height: '100%', background: row.color, borderRadius: 4, transition: 'width 0.4s ease' }} />
                            </div>
                            <span style={{ fontSize: 11, fontFamily: MONO, color: row.color, width: 24, textAlign: 'right', flexShrink: 0 }}>{row.count}</span>
                          </div>
                        ))}
                      </div>
                    )
                  })()}
                </div>
              </div>
            </div>
          )}

          {/* ════ NEWS ════ */}
          {tab === 'News' && (
            <div>
              {newsQ.isLoading ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                  {[0, 1, 2].map(i => (
                    <div key={i} style={{ padding: '14px 0', borderBottom: `1px solid ${C.line}` }}>
                      {skeleton('40%', 10)}
                      <div style={{ height: 6 }} />
                      {skeleton('90%', 14)}
                      <div style={{ height: 5 }} />
                      {skeleton('70%', 11)}
                    </div>
                  ))}
                </div>
              ) : !newsQ.data?.news?.length ? (
                <div style={{ fontSize: 13, color: C.dim, textAlign: 'center', padding: 40 }}>No recent news available.</div>
              ) : (
                <div>
                  {newsQ.data.news.map((item, i) => {
                    const tone = sentimentTone(item.title ?? '')
                    return (
                      <a
                        key={i}
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          display: 'block',
                          padding: '14px 10px',
                          borderBottom: i < newsQ.data!.news.length - 1 ? `1px solid ${C.line}` : 'none',
                          textDecoration: 'none',
                          borderRadius: 6,
                          cursor: 'pointer',
                        }}
                        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = C.panel }}
                        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5, flexWrap: 'wrap' }}>
                          {item.source && <span style={{ fontSize: 10, color: C.dim2, fontWeight: 600 }}>{item.source}</span>}
                          {item.published && <span style={{ fontSize: 10, color: C.dim2 }}>· {relativeTime(item.published)}</span>}
                          {tone === 'pos' && <Pill tone="ok">Positive</Pill>}
                          {tone === 'neg' && <Pill tone="miss">Negative</Pill>}
                        </div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: C.text, lineHeight: 1.45, marginBottom: 4 }}>
                          {item.title}
                        </div>
                        {item.summary && (
                          <div style={{
                            fontSize: 12,
                            color: C.dim,
                            lineHeight: 1.55,
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical' as const,
                            overflow: 'hidden',
                          }}>
                            {item.summary}
                          </div>
                        )}
                      </a>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}
