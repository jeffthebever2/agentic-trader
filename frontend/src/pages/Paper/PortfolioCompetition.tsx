/* Aesthetic direction — "Trading-terminal competition board".
 * precise / dense / competitive.  Rooted in the app's existing tokens (dark
 * warm-accent terminal), NOT a new brand and NOT the cream/serif AI default.
 * - Type: system UI for labels; --font-mono + tabular-nums for EVERY number.
 *   Micro-labels 10px uppercase, letter-spacing .09em, --ink-faint.
 * - Color: --surface cards on --canvas; one --accent for the leader; semantic
 *   green/red for P&L (distinct from accent); gold/silver/bronze rank medals.
 *   No gradients, no emoji decoration.
 * - Density: tight. Compact rows on a dense 4–16px scale — a data board, not a marketing page.
 * - Radius: 10–12px cards, matching the app. Elevation via border + soft shadow.
 * - Motion: quiet. 160ms ease-out, press feedback, count-up numbers, reduced-motion safe.
 * - Detail view: centered modal sub-window (size xl) with inner tabs — not a sidebar.
 */
import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { useAutoAnimate } from '@formkit/auto-animate/react'
import {
  getPortfolioAccounts, getPortfolioLeaderboard, getPortfolioRorChart,
  getPortfolioDetail, resetAllPortfolios, resetOnePortfolio,
  type PortfolioCard, type RorSeries,
} from '@/api/paperPortfolios'
import { Badge } from '@/components/ui/Badge'
import { Modal } from '@/components/ui/Modal'
import { AnimatedNumber } from '@/components/ui/AnimatedNumber'
import { Sparkline } from '@/components/charts/Sparkline'
import { LoadingState, ErrorState } from '@/components/shared/LoadingState'
import { useToast } from '@/components/ui/Toast'
import { useAuthStore } from '@/store/auth'

const seriesColor = (i: number) => `hsl(${(i * 137.508) % 360} 68% 58%)`
const MEDAL = ['#f5c451', '#cbd0d8', '#d69256'] // gold · silver · bronze
const UP = '#4ade80', DOWN = '#f87171'

const BADGE_VARIANT: Record<string, 'default' | 'success' | 'danger' | 'warning' | 'info'> = {
  Breakout: 'warning', ML: 'info', 'ML New': 'info', Combined: 'success',
  AI: 'danger', 'Long Hold': 'default', UnifiedBrain: 'info',
  Consensus: 'success', Blend: 'info', Intersect: 'warning',
}
const MULTI_BADGES = new Set(['Consensus', 'Blend', 'Intersect'])

const fmt$ = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const fmtRor = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
const rorColor = (n: number) => (n > 0 ? UP : n < 0 ? DOWN : 'var(--ink-faint)')
const mono = { fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums' } as const
const microLabel = { fontSize: 10, textTransform: 'uppercase', letterSpacing: '.09em', color: 'var(--ink-faint)' } as const

const SORTS = [
  { key: 'all_time_ror', label: 'ROR' },
  { key: 'sharpe_ratio', label: 'Sharpe' },
  { key: 'max_drawdown', label: 'Drawdown' },
  { key: 'profit_factor', label: 'Profit factor' },
  { key: 'current_equity', label: 'Equity' },
]
const ctrlBtn = { fontSize: 11.5, padding: '6px 11px' } as const  // ≥24px hit target, one shared spec

function Rank({ n, size = 22 }: { n: number; size?: number }) {
  if (n <= 0) return <span aria-hidden style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size, height: size, color: 'var(--ink-faint)', fontSize: size * 0.5, ...mono }}>–</span>
  const c = n <= 3 ? MEDAL[n - 1] : undefined
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: 6, fontSize: size * 0.5, fontWeight: 700, flexShrink: 0, ...mono,
      background: c ? `${c}22` : 'var(--surface-soft)', color: c ?? 'var(--ink-faint)',
      border: `1px solid ${c ? c + '66' : 'var(--surface-rule)'}`,
    }}>{n}</span>
  )
}

// ── Podium — top 3 ───────────────────────────────────────────────────────────
function Podium({ leaders, spark, onOpen }: { leaders: PortfolioCard[]; spark: Map<string, number[]>; onOpen: (id: string) => void }) {
  if (leaders.length < 3) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
      {leaders.slice(0, 3).map((c, i) => {
        const s = spark.get(c.portfolio_id) ?? []
        const lead = i === 0
        return (
          <button key={c.portfolio_id} onClick={() => onOpen(c.portfolio_id)} className="pf-card"
            style={{
              textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 8, padding: '14px 16px', cursor: 'pointer',
              background: lead ? 'var(--accent-subtle)' : 'var(--surface)',
              border: `1px solid ${lead ? 'var(--accent)' : 'var(--surface-rule)'}`, borderRadius: 12,
            }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <Rank n={i + 1} size={lead ? 26 : 22} />
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name}</span>
              </div>
              <Badge variant={BADGE_VARIANT[c.badge] ?? 'default'}>{c.badge}</Badge>
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ fontSize: lead ? 30 : 26, fontWeight: 700, lineHeight: 1, color: rorColor(c.all_time_ror), ...mono }}>
                <AnimatedNumber value={c.all_time_ror} format={fmtRor} animateOnMount={false} flash />
              </div>
              <Sparkline data={s.length > 1 ? s : [0, 0]} color={rorColor(c.all_time_ror)} width={lead ? 96 : 76} height={30} />
            </div>
            <div style={{ ...mono, fontSize: 11, color: 'var(--ink-faint)' }}>{fmt$(c.current_equity)} · {c.open_positions} open · Sharpe {c.sharpe_ratio.toFixed(2)}</div>
          </button>
        )
      })}
    </div>
  )
}

// ── Summary KPIs ─────────────────────────────────────────────────────────────
function Summary({ cards }: { cards: PortfolioCard[] }) {
  const s = useMemo(() => {
    if (!cards.length) return null
    const avg = cards.reduce((t, c) => t + c.all_time_ror, 0) / cards.length
    const inProfit = cards.filter(c => c.all_time_ror > 0).length
    const open = cards.reduce((t, c) => t + c.open_positions, 0)
    const multi = cards.filter(c => MULTI_BADGES.has(c.badge)).length
    return { avg, inProfit, open, multi, single: cards.length - multi, n: cards.length }
  }, [cards])
  if (!s) return null
  const tile = (label: string, node: React.ReactNode) => (
    <div style={{ padding: '11px 16px', boxShadow: 'inset -1px 0 0 var(--surface-rule)' }}>
      <div style={microLabel}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink)', marginTop: 3, ...mono }}>{node}</div>
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 12, overflow: 'hidden' }}>
      {tile('Avg ROR', <span style={{ color: rorColor(s.avg) }}>{fmtRor(s.avg)}</span>)}
      {tile('In profit', <>{s.inProfit}<span style={{ fontSize: 12, color: 'var(--ink-faint)' }}> / {s.n}</span></>)}
      {tile('Open positions', String(s.open))}
      {tile('Tools', <>{s.single}<span style={{ fontSize: 11, color: 'var(--ink-faint)' }}> single · </span>{s.multi}<span style={{ fontSize: 11, color: 'var(--ink-faint)' }}> multi</span></>)}
    </div>
  )
}

// ── ROR chart ────────────────────────────────────────────────────────────────
function RorChart({ series, topIds }: { series: RorSeries[]; topIds: Set<string> }) {
  const [hover, setHover] = useState<string | null>(null)
  const W = 860, H = 240, PAD_L = 34, PAD_R = 116, PAD_Y = 34
  const trunc = (s: string, n = 16) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
  const geom = useMemo(() => {
    const withPts = series.filter(s => s.points.length > 0)
    const all = withPts.flatMap(s => s.points.map(p => ({ x: new Date(p.t).getTime(), y: p.ror })))
    if (all.length < 2) return null
    const xs = all.map(p => p.x), ys = all.map(p => p.y)
    const xMin = Math.min(...xs); let xMax = Math.max(...xs)
    let yMin = Math.min(...ys, 0), yMax = Math.max(...ys, 0)
    if (xMax === xMin) xMax = xMin + 1
    const yPad = (yMax - yMin) * 0.12 || 1; yMin -= yPad; yMax += yPad
    const px = (x: number) => PAD_L + ((x - xMin) / (xMax - xMin)) * (W - PAD_L - PAD_R)
    const py = (y: number) => H - PAD_Y - ((y - yMin) / (yMax - yMin)) * (H - PAD_Y * 2)
    return { withPts, px, py, yMax, yMin }
  }, [series])
  if (!geom) return <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13 }}>ROR curves build as the engine records snapshots — check back after a few cycles.</div>
  // stagger overlapping end-labels so leaders that finish near the same ROR don't collide
  const ends = geom.withPts
    .filter(s => (topIds.has(s.portfolio_id) || hover === s.portfolio_id) && s.points.length)
    .map(s => ({ s, y: geom.py(s.points[s.points.length - 1].ror), x: geom.px(new Date(s.points[s.points.length - 1].t).getTime()) }))
    .sort((a, b) => a.y - b.y)
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13
  return (
    <div style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="All-time ROR over time for all portfolios; the same values are listed in the leaderboard table below." style={{ width: '100%', minWidth: 560, display: 'block' }}>
        {[geom.yMax, 0, geom.yMin].map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={geom.py(v)} y2={geom.py(v)} stroke="var(--surface-rule)" strokeDasharray={v === 0 ? '0' : '2 4'} strokeOpacity={v === 0 ? 1 : 0.5} />
            <text x={4} y={geom.py(v) + 3} fontSize={9} fill="var(--ink-faint)" style={mono}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</text>
          </g>
        ))}
        {geom.withPts.slice().sort((a) => (topIds.has(a.portfolio_id) ? 1 : -1)).map(s => {
          const idx = series.findIndex(x => x.portfolio_id === s.portfolio_id)
          const isTop = topIds.has(s.portfolio_id), active = hover === s.portfolio_id, dim = hover !== null ? !active : !isTop
          const pts = s.points.map(p => `${geom.px(new Date(p.t).getTime())},${geom.py(p.ror)}`).join(' ')
          const last = s.points[s.points.length - 1]
          return (
            <g key={s.portfolio_id}>
              <polyline points={pts} fill="none" stroke={seriesColor(idx)} strokeWidth={active ? 2.6 : isTop ? 1.9 : 1.1} strokeOpacity={dim ? 0.12 : 1} strokeLinejoin="round" strokeLinecap="round" />
              {(isTop || active) && last && <circle cx={geom.px(new Date(last.t).getTime())} cy={geom.py(last.ror)} r={active ? 3.5 : 2.6} fill={seriesColor(idx)} fillOpacity={dim ? 0.12 : 1} />}
            </g>
          )
        })}
        {/* direct end-of-line labels for the leaders (no reliance on legend color) */}
        {ends.map(({ s, x, y }) => {
          const idx = series.findIndex(x2 => x2.portfolio_id === s.portfolio_id)
          const last = s.points[s.points.length - 1]
          return (
            <g key={`lbl-${s.portfolio_id}`}>
              {y !== geom.py(last.ror) && <line x1={x} y1={geom.py(last.ror)} x2={x + 5} y2={y} stroke={seriesColor(idx)} strokeWidth={1} strokeOpacity={0.5} />}
              <text x={x + 8} y={y + 3.2} fontSize={10.5} fontWeight={600} fill={seriesColor(idx)} style={mono}>{trunc(s.name)}</text>
            </g>
          )
        })}
      </svg>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 8 }}>
        {series.map((s, i) => (
          <span key={s.portfolio_id} onMouseEnter={() => setHover(s.portfolio_id)} onMouseLeave={() => setHover(null)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, cursor: 'pointer', ...mono, color: hover && hover !== s.portfolio_id ? 'var(--ink-faint)' : 'var(--ink)' }}>
            <span style={{ width: 9, height: 3, borderRadius: 2, background: seriesColor(i), opacity: topIds.has(s.portfolio_id) ? 1 : 0.55 }} />{s.name}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Gallery card ─────────────────────────────────────────────────────────────
function GalleryCard({ c, rank, spark, onClick }: { c: PortfolioCard; rank: number; spark: number[]; onClick: () => void }) {
  return (
    <button onClick={onClick} className="pf-card"
      style={{ textAlign: 'left', width: '100%', background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 12, padding: 14, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <Rank n={rank} />
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name}</span>
        </div>
        <Badge variant={BADGE_VARIANT[c.badge] ?? 'default'}>{c.badge}</Badge>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
        <div>
          <div style={{ fontSize: 25, fontWeight: 700, lineHeight: 1, color: rorColor(c.all_time_ror), ...mono }}>
            <AnimatedNumber value={c.all_time_ror} format={fmtRor} animateOnMount={false} flash />
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 3, ...mono }}>{fmt$(c.current_equity)}</div>
        </div>
        <Sparkline data={spark.length > 1 ? spark : [0, 0]} color={rorColor(c.all_time_ror)} width={84} height={30} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 6, marginTop: 2 }}>
        <MiniStat label="Sharpe" v={c.sharpe_ratio.toFixed(2)} />
        <MiniStat label="DD" v={`${c.max_drawdown.toFixed(0)}%`} />
        <MiniStat label="Trades" v={String(c.total_trades)} />
        <MiniStat label="Open" v={String(c.open_positions)} />
      </div>
    </button>
  )
}
const MiniStat = ({ label, v }: { label: string; v: string }) => (
  <div>
    <div style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--ink-faint)' }}>{label}</div>
    <div style={{ color: 'var(--ink)', fontWeight: 600, fontSize: 12, ...mono }}>{v}</div>
  </div>
)

// ── Detail modal (sub-window) ────────────────────────────────────────────────
type DetailTab = 'positions' | 'trades' | 'compliance'
function PortfolioModal({ id, rank, onClose, isAdmin }: { id: string; rank: number; onClose: () => void; isAdmin: boolean }) {
  const qc = useQueryClient()
  const { toast } = useToast()
  const [tab, setTab] = useState<DetailTab>('positions')
  const detailQ = useQuery({ queryKey: ['pf-detail', id], queryFn: () => getPortfolioDetail(id) })
  const [resetting, setResetting] = useState(false)

  const resetOne = async () => {
    if (resetting || !window.confirm(`Reset ${id} back to $10,000? Wipes its paper positions & history.`)) return
    setResetting(true)
    try {
      await resetOnePortfolio(id)
      toast.success(`${id} reset to $10,000`)
      ;['pf-detail', 'pf-accounts', 'pf-leaderboard'].forEach(k => qc.invalidateQueries({ queryKey: k === 'pf-detail' ? [k, id] : [k] }))
    } catch { toast.error('Reset failed') }
    finally { setResetting(false) }
  }

  const d = detailQ.data
  const isMulti = Array.isArray(d?.config.source_strategies) && (d!.config.source_strategies as string[]).length > 0
  return (
    <Modal open onClose={onClose} size="xl" title={d?.name ?? id}>
      {detailQ.isLoading && <LoadingState />}
      {detailQ.error && <ErrorState message="Failed to load portfolio detail" />}
      {d && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, fontSize: 13 }}>
          {/* hero band */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', padding: '14px 16px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 12 }}>
            <Rank n={rank} size={34} />
            <div style={{ marginRight: 'auto' }}>
              <Badge variant={BADGE_VARIANT[d.card.badge] ?? 'default'}>{d.card.badge}</Badge>
              <div style={{ ...mono, fontSize: 11, color: 'var(--ink-faint)', marginTop: 4 }}>{fmt$(d.card.settled_cash)} settled · {fmt$(d.card.unsettled_cash)} unsettled</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ ...microLabel }}>All-time ROR</div>
              <div style={{ fontSize: 34, fontWeight: 700, lineHeight: 1.05, color: rorColor(d.card.all_time_ror), ...mono }}>
                <AnimatedNumber value={d.card.all_time_ror} format={fmtRor} animateOnMount={false} />
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ ...microLabel }}>Equity</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)', ...mono }}>{fmt$(d.card.current_equity)}</div>
            </div>
          </div>

          {/* two-column body */}
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(280px, 1.4fr)', gap: 16, alignItems: 'start' }}>
            {/* left: metrics + config */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 8 }}>
                {[
                  ['Sharpe', d.card.sharpe_ratio.toFixed(2)],
                  ['Max DD', `${d.card.max_drawdown.toFixed(1)}%`],
                  ['Profit factor', d.card.profit_factor >= 999 ? '∞' : d.card.profit_factor.toFixed(2)],
                  ['Win rate', `${(d.card.win_rate * 100).toFixed(0)}%`],
                  ['Daily ROR', fmtRor(d.card.daily_ror)],
                  ['Trades', String(d.card.total_trades)],
                ].map(([label, val]) => (
                  <div key={label} style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: '9px 11px' }}>
                    <div style={microLabel}>{label}</div>
                    <div style={{ fontWeight: 700, marginTop: 2, color: 'var(--ink)', ...mono }}>{val}</div>
                  </div>
                ))}
              </div>
              <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 12 }}>
                <div style={{ ...microLabel, marginBottom: 6 }}>Strategy config</div>
                <div style={{ ...mono, fontSize: 11.5, color: 'var(--ink-muted)', lineHeight: 1.8 }}>
                  {isMulti
                    ? <>tools = [{(d.config.source_strategies as string[]).join(', ')}]<br />mode = {String(d.config.combine_mode)}<br /></>
                    : <>source = {String(d.config.source_strategy)}<br /></>}
                  stop ×{String(d.config.stop_mult)} · target ×{String(d.config.target_mult)}<br />
                  hold {String(d.config.max_hold_days)}d · risk {String(d.config.risk_per_trade_pct)}%
                  {d.config.trailing_stop_atr_mult != null && <> · trail ×{String(d.config.trailing_stop_atr_mult)}</>}
                </div>
              </div>
            </div>

            {/* right: tabbed activity */}
            <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 10, overflow: 'hidden' }}>
              <div role="tablist" aria-label="Portfolio activity" style={{ display: 'flex', borderBottom: '1px solid var(--surface-rule)' }}>
                {([
                  ['positions', `Positions ${d.positions.length}`],
                  ['trades', `Trades ${d.trades.length}`],
                  ['compliance', `Skips ${d.compliance_log.length}`],
                ] as [DetailTab, string][]).map(([t, label]) => (
                  <button key={t} onClick={() => setTab(t)} role="tab" aria-selected={tab === t} className="pf-tab"
                    style={{
                      flex: 1, padding: '10px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer', border: 'none', background: 'transparent',
                      transition: 'color .12s var(--ease-out), box-shadow .12s var(--ease-out)',
                      color: tab === t ? 'var(--ink)' : 'var(--ink-faint)',
                      boxShadow: tab === t ? 'inset 0 -2px 0 var(--accent)' : 'none',
                    }}>{label}</button>
                ))}
              </div>
              <div style={{ maxHeight: 320, overflowY: 'auto', padding: '6px 12px 12px' }}>
                {tab === 'positions' && (d.positions.length === 0 ? <Empty>No open positions</Empty> : d.positions.map(p => (
                  <ActRow key={p.ticker} a={p.ticker} b={`${p.shares}sh @ ${fmt$(p.entry_price)}`} c={fmtRor(p.unrealized_pct)} cColor={rorColor(p.unrealized_pnl)} />
                )))}
                {tab === 'trades' && (d.trades.length === 0 ? <Empty>No closed trades</Empty> : [...d.trades].reverse().slice(0, 30).map((t, i) => (
                  <ActRow key={i} a={t.ticker} b={t.exit_reason ?? 'open'} c={fmt$(t.realized_pnl)} cColor={rorColor(t.realized_pnl)} />
                )))}
                {tab === 'compliance' && (d.compliance_log.length === 0 ? <Empty>No skips logged</Empty> : [...d.compliance_log].reverse().slice(0, 30).map((c, i) => (
                  <ActRow key={i} a={c.ticker} b="" c={c.reason} cColor="var(--warning)" cMono />
                )))}
              </div>
            </div>
          </div>

          {isAdmin && <button onClick={resetOne} disabled={resetting} className="btn-secondary" style={{ fontSize: 12, alignSelf: 'flex-start' }}>{resetting ? 'Resetting…' : 'Reset this portfolio'}</button>}
        </div>
      )}
    </Modal>
  )
}
const ActRow = ({ a, b, c, cColor, cMono }: { a: string; b: string; c: string; cColor: string; cMono?: boolean }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, padding: '7px 0', borderBottom: '1px solid var(--surface-rule)' }}>
    <span style={{ fontWeight: 600, minWidth: 52 }}>{a}</span>
    <span style={{ color: 'var(--ink-faint)', fontSize: 11, flex: 1, textAlign: 'left' }}>{b}</span>
    <span style={{ color: cColor, fontSize: cMono ? 10.5 : 13, ...mono }}>{c}</span>
  </div>
)
const Empty = ({ children }: { children: React.ReactNode }) => (
  <div style={{ color: 'var(--ink-faint)', fontSize: 12, fontStyle: 'italic', padding: '10px 0' }}>{children}</div>
)

// ── Main ─────────────────────────────────────────────────────────────────────
type Filter = 'all' | 'single' | 'multi'
type View = 'board' | 'cards'

export function PortfolioCompetition() {
  const qc = useQueryClient()
  const { toast } = useToast()
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin' || user?.is_admin === true
  const [sortBy, setSortBy] = useState('all_time_ror')
  const [filter, setFilter] = useState<Filter>('all')
  const [view, setView] = useState<View>('board')
  const [selected, setSelected] = useState<string | null>(null)
  const [grid] = useAutoAnimate<HTMLDivElement>()
  const [rows] = useAutoAnimate<HTMLTableSectionElement>()

  const accountsQ = useQuery({ queryKey: ['pf-accounts'], queryFn: getPortfolioAccounts, refetchInterval: 30_000 })
  const lbQ = useQuery({ queryKey: ['pf-leaderboard', sortBy], queryFn: () => getPortfolioLeaderboard(sortBy), refetchInterval: 30_000, placeholderData: keepPreviousData })
  const rorQ = useQuery({ queryKey: ['pf-ror'], queryFn: getPortfolioRorChart, refetchInterval: 60_000 })

  const [resettingAll, setResettingAll] = useState(false)
  const resetAll = async () => {
    if (resettingAll || !window.confirm('Reset ALL 30 paper portfolios back to $10,000? Wipes every paper position & trade. Live accounts untouched.')) return
    setResettingAll(true)
    try {
      await resetAllPortfolios()
      toast.success('All 30 portfolios reset to $10,000')
      ;['pf-accounts', 'pf-leaderboard', 'pf-ror'].forEach(k => qc.invalidateQueries({ queryKey: [k] }))
    } catch { toast.error('Reset failed (admin only)') }
    finally { setResettingAll(false) }
  }

  const sparkById = useMemo(() => {
    const m = new Map<string, number[]>()
    for (const s of rorQ.data ?? []) m.set(s.portfolio_id, s.points.map(p => p.ror))
    return m
  }, [rorQ.data])
  const rankById = useMemo(() => new Map((lbQ.data?.entries ?? []).map(e => [e.portfolio_id, e.rank])), [lbQ.data])
  const topIds = useMemo(() => new Set((lbQ.data?.entries ?? []).slice(0, 3).map(e => e.portfolio_id)), [lbQ.data])

  // podium leaders — always by ROR, independent of the table sort/filter
  const leaders = useMemo(() => [...(accountsQ.data ?? [])].sort((a, b) => b.all_time_ror - a.all_time_ror).slice(0, 3), [accountsQ.data])

  const matchFilter = useCallback((badge: string) => filter === 'all' || (filter === 'multi') === MULTI_BADGES.has(badge), [filter])
  const cards = useMemo(() => {
    const list = (accountsQ.data ?? []).filter(c => matchFilter(c.badge))
    return [...list].sort((a, b) => (rankById.get(a.portfolio_id) ?? 99) - (rankById.get(b.portfolio_id) ?? 99))
  }, [accountsQ.data, rankById, matchFilter])
  const boardRows = useMemo(() => (lbQ.data?.entries ?? []).filter(e => matchFilter(e.badge)), [lbQ.data, matchFilter])

  if (accountsQ.isLoading) return <LoadingState />
  if (accountsQ.error) return <ErrorState message="Failed to load portfolios" />

  const counts = { all: accountsQ.data?.length ?? 0, multi: (accountsQ.data ?? []).filter(c => MULTI_BADGES.has(c.badge)).length }
  const filters: { id: Filter; label: string }[] = [
    { id: 'all', label: `All ${counts.all}` },
    { id: 'single', label: `Single ${counts.all - counts.multi}` },
    { id: 'multi', label: `Multi ${counts.multi}` },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ fontSize: 19, fontWeight: 700, color: 'var(--ink)', margin: 0 }}>30-Portfolio Competition</h2>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>$10,000 each · paper-only · ranked by all-time ROR</div>
        </div>
        {isAdmin && <button onClick={resetAll} disabled={resettingAll} className="btn-secondary" style={{ fontSize: 12 }}>{resettingAll ? 'Resetting…' : 'Reset all'}</button>}
      </div>

      <Podium leaders={leaders} spark={sparkById} onOpen={setSelected} />
      <Summary cards={accountsQ.data ?? []} />

      <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 12, padding: 16 }}>
        <div style={{ ...microLabel, marginBottom: 10 }}>All-time ROR · top 3 highlighted</div>
        {rorQ.data && <RorChart series={rorQ.data} topIds={topIds} />}
      </div>

      {/* controls */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
        <div role="group" aria-label="Filter portfolios" style={{ display: 'flex', gap: 4 }}>
          {filters.map(f => (
            <button key={f.id} onClick={() => setFilter(f.id)} aria-pressed={filter === f.id}
              className={filter === f.id ? 'btn-primary' : 'btn-secondary'} style={ctrlBtn}>{f.label}</button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div role="group" aria-label="Sort by" style={{ display: 'flex', gap: 4 }}>
            {SORTS.map(s => (
              <button key={s.key} onClick={() => setSortBy(s.key)} aria-pressed={sortBy === s.key}
                className={sortBy === s.key ? 'btn-primary' : 'btn-secondary'} style={ctrlBtn}>{s.label}</button>
            ))}
          </div>
          <div role="group" aria-label="View" style={{ display: 'flex', gap: 4, borderLeft: '1px solid var(--surface-rule)', paddingLeft: 10 }}>
            {(['board', 'cards'] as View[]).map(v => (
              <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
                className={view === v ? 'btn-primary' : 'btn-secondary'} style={ctrlBtn}>{v === 'board' ? 'Board' : 'Gallery'}</button>
            ))}
          </div>
        </div>
      </div>

      {view === 'board' && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 12, overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, minWidth: 640 }}>
              <thead>
                <tr style={{ ...microLabel }}>
                  {['#', 'Portfolio', 'Type', 'ROR', '', 'Equity', 'Sharpe', 'DD', 'Trend'].map((h, i) => (
                    <th key={i} style={{ textAlign: i === 1 || i === 2 ? 'left' : i >= 3 ? 'right' : 'center', padding: '9px 12px', fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody ref={rows}>
                {boardRows.map(e => {
                  const spark = sparkById.get(e.portfolio_id) ?? []
                  const mag = Math.min(Math.abs(e.all_time_ror) / 10, 1)
                  return (
                    <tr key={e.portfolio_id} onClick={() => setSelected(e.portfolio_id)} className="pf-row"
                      role="button" tabIndex={0} aria-label={`Open ${e.name}`}
                      onKeyDown={ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setSelected(e.portfolio_id) } }}
                      style={{ cursor: 'pointer', borderTop: '1px solid var(--surface-rule)' }}>
                      <td style={{ padding: '7px 12px', textAlign: 'center' }}><Rank n={e.rank} /></td>
                      <td style={{ padding: '7px 12px', fontWeight: 600, color: 'var(--ink)' }}>{e.name}</td>
                      <td style={{ padding: '7px 12px' }}><Badge variant={BADGE_VARIANT[e.badge] ?? 'default'}>{e.badge}</Badge></td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 700, color: rorColor(e.all_time_ror), ...mono }}>{fmtRor(e.all_time_ror)}</td>
                      <td style={{ padding: '7px 6px', width: 60 }}>
                        <div style={{ height: 5, borderRadius: 3, background: 'var(--surface-rule)', overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${mag * 100}%`, marginLeft: e.all_time_ror < 0 ? `${(1 - mag) * 100}%` : 0, background: rorColor(e.all_time_ror), borderRadius: 3 }} />
                        </div>
                      </td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', ...mono }}>{fmt$(e.current_equity)}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', color: 'var(--ink-faint)', ...mono }}>{e.sharpe_ratio.toFixed(2)}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', color: 'var(--ink-faint)', ...mono }}>{e.max_drawdown.toFixed(1)}%</td>
                      <td style={{ padding: '3px 12px', textAlign: 'right' }}><div style={{ display: 'inline-block' }}><Sparkline data={spark.length > 1 ? spark : [0, 0]} color={rorColor(e.all_time_ror)} width={64} height={20} /></div></td>
                    </tr>
                  )
                })}
                {boardRows.length === 0 && (
                  <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--ink-faint)', fontSize: 12 }}>
                    {lbQ.isLoading ? 'Loading…' : lbQ.error ? 'Failed to load leaderboard' : 'No portfolios match this filter'}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {view === 'cards' && (
        <div ref={grid} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 12 }}>
          {cards.map(c => (
            <GalleryCard key={c.portfolio_id} c={c} rank={rankById.get(c.portfolio_id) ?? 0} spark={sparkById.get(c.portfolio_id) ?? []} onClick={() => setSelected(c.portfolio_id)} />
          ))}
        </div>
      )}

      {selected && <PortfolioModal id={selected} rank={rankById.get(selected) ?? 0} onClose={() => setSelected(null)} isAdmin={isAdmin} />}
    </div>
  )
}
