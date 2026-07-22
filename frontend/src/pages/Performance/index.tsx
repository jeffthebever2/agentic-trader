import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell,
} from 'recharts'
import { Card } from '@/components/ui/Card'
import { AnimatedNumber } from '@/components/ui/AnimatedNumber'
import { Tabs } from '@/components/ui/Tabs'
import { Badge } from '@/components/ui/Badge'
import { Modal } from '@/components/ui/Modal'
import { Drawer } from '@/components/ui/Drawer'
import { DataTable, type ColDef } from '@/components/ui/DataTable'
import { LoadingState, ErrorState } from '@/components/shared/LoadingState'
import {
  getPerfSummary, getPerfHistory, getPerfPositions, getPerfValidate,
  getPerfSyncLog, syncPerf, getCashFlows, addCashFlow, deleteCashFlow,
  getPerfDay, exportUrl, type PerfRange,
} from '@/api/performance'
import type { PerfRow, PerfCashFlow, PerfMetrics, PerfPosition } from '@/types'

const GREEN = '#4ade80', RED = '#f87171', ACCENT = 'var(--accent)'
const usd = (n?: number | null) =>
  n == null ? '—' : (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const pct = (n?: number | null) => n == null ? '—' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
const sgn = (n?: number | null) => n == null ? 'var(--ink)' : n > 0 ? GREEN : n < 0 ? RED : 'var(--ink-muted)'
const num = (v: unknown) => Number(v ?? 0)
const Rec = (r: unknown) => r as Record<string, unknown>

const RANGES: { id: PerfRange; label: string }[] = [
  { id: '1w', label: '1W' }, { id: '1m', label: '1M' }, { id: '3m', label: '3M' },
  { id: 'ytd', label: 'YTD' }, { id: '1y', label: '1Y' }, { id: 'all', label: 'All' },
]
const TABS = [{ id: 'overview', label: 'Overview' }, { id: 'calendar', label: 'Calendar' }, { id: 'holdings', label: 'Holdings' }]

export default function PerformancePage() {
  const qc = useQueryClient()
  const [tab, setTab] = useState('overview')
  const [range, setRange] = useState<PerfRange>('all')
  const [manageOpen, setManageOpen] = useState(false)

  const summaryQ = useQuery({ queryKey: ['perf', 'summary', range], queryFn: () => getPerfSummary(range), refetchInterval: 60_000 })
  const histQ = useQuery({ queryKey: ['perf', 'history', range], queryFn: () => getPerfHistory(range) })
  const syncMut = useMutation({ mutationFn: syncPerf, onSuccess: () => qc.invalidateQueries({ queryKey: ['perf'] }) })

  const rows: PerfRow[] = histQ.data?.rows ?? []
  const m = summaryQ.data

  if (summaryQ.isLoading) return <LoadingState message="Loading performance…" />
  if (summaryQ.error) return <ErrorState message="Could not load performance data." />

  return (
    <div id="panel-performance" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 1280, margin: '0 auto', width: '100%' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-.01em' }}>Performance</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Real Fidelity account · saved daily</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', background: 'var(--surface-soft)', borderRadius: 8, padding: 3, gap: 2 }}>
            {RANGES.map(r => (
              <button key={r.id} onClick={() => setRange(r.id)} style={{
                padding: '4px 11px', fontSize: 11, fontWeight: 700, borderRadius: 6, cursor: 'pointer', border: 'none',
                background: range === r.id ? 'var(--surface)' : 'transparent',
                color: range === r.id ? 'var(--ink)' : 'var(--ink-faint)',
                boxShadow: range === r.id ? 'var(--shadow-1, 0 1px 2px rgba(0,0,0,.1))' : 'none',
              }}>{r.label}</button>
            ))}
          </div>
          <button onClick={() => syncMut.mutate()} disabled={syncMut.isPending} className="btn-accent" style={btn(true, syncMut.isPending)}>
            {syncMut.isPending ? 'Syncing…' : '↻ Sync'}
          </button>
          <button onClick={() => setManageOpen(true)} style={btn(false)}>Manage</button>
        </div>
      </div>

      {!m?.has_data ? (
        <Card style={{ textAlign: 'center', padding: 48 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', marginBottom: 8 }}>No history yet</div>
          <div style={{ fontSize: 13, color: 'var(--ink-faint)', marginBottom: 16 }}>Capture your first Fidelity snapshot — daily snapshots then save automatically.</div>
          <button onClick={() => syncMut.mutate()} disabled={syncMut.isPending} style={btn(true, syncMut.isPending)}>{syncMut.isPending ? 'Syncing…' : '↻ Sync now'}</button>
        </Card>
      ) : (
        <>
          <Hero m={m} />
          <Tabs tabs={TABS} active={tab} onChange={setTab} />
          {tab === 'overview' && <Overview m={m} rows={rows} />}
          {tab === 'calendar' && <CalendarTab rows={rows} />}
          {tab === 'holdings' && <Holdings />}
        </>
      )}

      <Drawer open={manageOpen} onClose={() => setManageOpen(false)} title="Manage & export" width="440px">
        <ManagePanel range={range} />
      </Drawer>
    </div>
  )
}

const btn = (accent: boolean, busy = false): React.CSSProperties => ({
  padding: '7px 14px', fontSize: 12, fontWeight: 700, borderRadius: 8, cursor: busy ? 'default' : 'pointer',
  border: '1px solid ' + (accent ? 'transparent' : 'var(--surface-rule)'),
  background: accent ? ACCENT : 'var(--surface)', color: accent ? '#fff' : 'var(--ink)',
})

// ── Hero ──────────────────────────────────────────────────────────────────────
function Hero({ m }: { m: PerfMetrics }) {
  const up = (m.today_pnl ?? 0) >= 0
  return (
    <Card style={{ padding: 22 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 28, flexWrap: 'wrap' }}>
        <div>
          <div style={lbl}>Account value</div>
          <div style={{ fontSize: 38, fontWeight: 800, color: 'var(--ink)', lineHeight: 1, letterSpacing: '-.02em', fontVariantNumeric: 'tabular-nums' }}>{Number.isFinite(m.current_value as number) ? <AnimatedNumber value={m.current_value as number} format={usd} flash={false} /> : usd(m.current_value)}</div>
        </div>
        <div style={{ paddingBottom: 4 }}>
          <div style={lbl}>Today</div>
          {m.today_pnl == null ? <span style={{ color: 'var(--ink-faint)', fontSize: 14 }}>Baseline day</span> : (
            <Badge variant={up ? 'success' : 'danger'}>{up ? '▲' : '▼'} {usd(m.today_pnl)} · {pct(m.today_pct)}</Badge>
          )}
        </div>
        <div style={{ paddingBottom: 4 }}>
          <div style={lbl}>Total return</div>
          <div style={{ fontSize: 20, fontWeight: 800, color: sgn(m.total_pnl), fontVariantNumeric: 'tabular-nums' }}>{Number.isFinite(m.total_pnl as number) ? <AnimatedNumber value={m.total_pnl as number} format={usd} flash /> : usd(m.total_pnl)} <span style={{ fontSize: 13, color: sgn(m.total_return_pct) }}>{pct(m.total_return_pct)}</span></div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 22, paddingBottom: 4 }}>
          <Mini label="MTD" value={pct(m.mtd_pct)} color={sgn(m.mtd_pct)} />
          <Mini label="YTD" value={pct(m.ytd_pct)} color={sgn(m.ytd_pct)} />
          <Mini label="Win rate" value={(m.win_rate ?? 0).toFixed(0) + '%'} />
        </div>
      </div>
    </Card>
  )
}
const lbl: React.CSSProperties = { fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.07em', fontWeight: 700, marginBottom: 5 }
function Mini({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div><div style={lbl}>{label}</div><div style={{ fontSize: 16, fontWeight: 800, color: color ?? 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>{value}</div></div>
}

// ── Overview (metrics + charts) ────────────────────────────────────────────────
function Overview({ m, rows }: { m: PerfMetrics; rows: PerfRow[] }) {
  const ok = rows.filter(r => r.ok)
  const stats: { label: string; value: string; color?: string }[] = [
    { label: 'Best day', value: m.best_day ? usd(m.best_day.pnl) : '—', color: GREEN },
    { label: 'Worst day', value: m.worst_day ? usd(m.worst_day.pnl) : '—', color: RED },
    { label: 'Avg green', value: usd(m.avg_green), color: GREEN },
    { label: 'Avg red', value: usd(m.avg_red), color: RED },
    { label: 'Max drawdown', value: pct(m.max_drawdown_pct), color: RED },
    { label: 'Win rate', value: (m.win_rate ?? 0).toFixed(1) + '%' },
    { label: 'Trading days', value: String(m.trading_days ?? 0) },
    { label: 'Cash', value: usd(m.cash) },
    { label: 'Invested', value: usd(m.invested_value) },
    { label: 'Deposits', value: usd(m.total_deposits) },
  ]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card style={{ padding: 18 }}>
        <SectionTitle>Account value</SectionTitle>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={ok} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <defs><linearGradient id="eqg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={ACCENT} stopOpacity={0.25} /><stop offset="100%" stopColor={ACCENT} stopOpacity={0.02} /></linearGradient></defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,.1)" />
            <XAxis dataKey="date" {...xa} minTickGap={44} />
            <YAxis {...ya} width={62} tickFormatter={(v: number) => '$' + (v / 1000).toFixed(1) + 'k'} domain={['auto', 'auto']} />
            <Tooltip {...tip} formatter={(v: unknown) => usd(num(v))} />
            <Area type="monotone" dataKey="ending_value" name="Value" stroke={ACCENT} fill="url(#eqg)" strokeWidth={2.5} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card style={{ padding: 18 }}>
        <SectionTitle>Key metrics</SectionTitle>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 18, marginTop: 4 }}>
          {stats.map(s => <Mini key={s.label} label={s.label} value={s.value} color={s.color} />)}
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16 }}>
        <ChartCard title="Daily $ P/L"><BarChart data={ok}><CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,.1)" /><XAxis dataKey="date" {...xa} /><YAxis {...ya} tickFormatter={(v: number) => '$' + v.toFixed(0)} /><Tooltip {...tip} formatter={(v: unknown) => usd(num(v))} /><Bar dataKey="daily_pnl" radius={[3, 3, 0, 0]}>{ok.map((r, i) => <Cell key={i} fill={(r.daily_pnl ?? 0) >= 0 ? GREEN : RED} />)}</Bar></BarChart></ChartCard>
        <ChartCard title="Cumulative return"><AreaChart data={ok}><CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,.1)" /><XAxis dataKey="date" {...xa} /><YAxis {...ya} tickFormatter={(v: number) => v.toFixed(0) + '%'} /><Tooltip {...tip} formatter={(v: unknown) => pct(num(v))} /><Area type="monotone" dataKey="cumulative_pct" stroke={ACCENT} fill={ACCENT} fillOpacity={0.1} strokeWidth={2} dot={false} /></AreaChart></ChartCard>
        <ChartCard title="Realized vs unrealized"><LineChart data={ok}><CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,.1)" /><XAxis dataKey="date" {...xa} /><YAxis {...ya} tickFormatter={(v: number) => '$' + v.toFixed(0)} /><Tooltip {...tip} formatter={(v: unknown) => usd(num(v))} /><Legend wrapperStyle={{ fontSize: 11 }} /><Line type="monotone" dataKey="realized_gl" name="Realized" stroke="#60a5fa" strokeWidth={2} dot={false} /><Line type="monotone" dataKey="unrealized_gl" name="Unrealized" stroke={ACCENT} strokeWidth={2} dot={false} /></LineChart></ChartCard>
        <ChartCard title="Cash vs invested"><AreaChart data={ok}><CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,.1)" /><XAxis dataKey="date" {...xa} /><YAxis {...ya} tickFormatter={(v: number) => '$' + (v / 1000).toFixed(0) + 'k'} /><Tooltip {...tip} formatter={(v: unknown) => usd(num(v))} /><Legend wrapperStyle={{ fontSize: 11 }} /><Area type="monotone" stackId="1" dataKey="invested_value" name="Invested" stroke={ACCENT} fill={ACCENT} fillOpacity={0.16} strokeWidth={1.5} dot={false} /><Area type="monotone" stackId="1" dataKey="cash" name="Cash" stroke="#94a3b8" fill="#94a3b8" fillOpacity={0.16} strokeWidth={1.5} dot={false} /></AreaChart></ChartCard>
      </div>
    </div>
  )
}
function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-muted)', marginBottom: 12 }}>{children}</div>
}
function ChartCard({ title, children }: { title: string; children: React.ReactElement }) {
  return <Card style={{ padding: 16 }}><SectionTitle>{title}</SectionTitle><ResponsiveContainer width="100%" height={190}>{children}</ResponsiveContainer></Card>
}
const xa = { tick: { fontSize: 9, fill: 'var(--ink-faint)' }, tickLine: false, axisLine: false, minTickGap: 30 }
const ya = { tick: { fontSize: 9, fill: 'var(--ink-faint)' }, tickLine: false, axisLine: false, width: 50 }
const tip = { contentStyle: { background: 'var(--surface)', border: '1px solid var(--surface-rule)', fontSize: 12, borderRadius: 8, boxShadow: 'var(--shadow-3)' }, labelStyle: { color: 'var(--ink-faint)', fontSize: 11 } }

// ── Calendar + History ─────────────────────────────────────────────────────────
function CalendarTab({ rows }: { rows: PerfRow[] }) {
  const byDate = useMemo(() => new Map(rows.map(r => [r.date, r])), [rows])
  const latest = rows.length ? rows[rows.length - 1].date : new Date().toISOString().slice(0, 10)
  const [ym, setYm] = useState(() => latest.slice(0, 7))
  const [openDay, setOpenDay] = useState<string | null>(null)
  const [yy, mm] = ym.split('-').map(Number)
  const first = new Date(yy, mm - 1, 1)
  const cells: (string | null)[] = []
  for (let i = 0; i < first.getDay(); i++) cells.push(null)
  for (let d = 1; d <= new Date(yy, mm, 0).getDate(); d++) cells.push(`${ym}-${String(d).padStart(2, '0')}`)
  const shift = (delta: number) => { const dt = new Date(yy, mm - 1 + delta, 1); setYm(`${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`) }
  const bg = (r?: PerfRow) => !r || r.daily_pnl == null ? 'var(--surface-soft)' : r.color === 'green' ? 'rgba(74,222,128,.14)' : r.color === 'red' ? 'rgba(248,113,113,.14)' : 'var(--surface-soft)'

  const histCols: ColDef<Record<string, unknown>>[] = [
    { key: 'date', label: 'Date', sortable: true, align: 'left' },
    { key: 'ending_value', label: 'Value', align: 'right', render: v => usd(num(v)) },
    { key: 'daily_pnl', label: 'Daily $', sortable: true, align: 'right', render: v => <span style={{ color: sgn(v as number), fontWeight: 700 }}>{usd(num(v))}</span> },
    { key: 'daily_pct', label: 'Daily %', sortable: true, align: 'right', render: v => <span style={{ color: sgn(v as number) }}>{pct(v as number)}</span> },
    { key: 'cumulative_pct', label: 'Cumul.', align: 'right', render: v => <span style={{ color: sgn(v as number) }}>{pct(v as number)}</span> },
    { key: 'deposits', label: 'Flows', align: 'right', render: (_v, row) => { const d = num(row.deposits), w = num(row.withdrawals); return d || w ? <span style={{ color: 'var(--ink-faint)' }}>{d ? '+' + usd(d) : ''}{w ? '-' + usd(w) : ''}</span> : '—' } },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card style={{ padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <button onClick={() => shift(-1)} style={navBtn}>‹</button>
          <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--ink)', minWidth: 150, textAlign: 'center' }}>{first.toLocaleString('en-US', { month: 'long', year: 'numeric' })}</div>
          <button onClick={() => shift(1)} style={navBtn}>›</button>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, fontSize: 10, color: 'var(--ink-faint)' }}>
            <Dot c={GREEN} t="Up" /><Dot c={RED} t="Down" /><Dot c="#6b7280" t="No data" />
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 5 }}>
          {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => <div key={i} style={{ fontSize: 9, fontWeight: 700, color: 'var(--ink-faint)', textAlign: 'center', padding: 2 }}>{d}</div>)}
          {cells.map((date, i) => {
            if (!date) return <div key={i} />
            const r = byDate.get(date)
            return (
              <button key={i} onClick={() => r && setOpenDay(date)} disabled={!r} style={{
                aspectRatio: '1', borderRadius: 8, padding: 5, cursor: r ? 'pointer' : 'default',
                border: '1px solid var(--surface-rule)', background: bg(r), display: 'flex', flexDirection: 'column',
                alignItems: 'flex-start', justifyContent: 'space-between', textAlign: 'left', minHeight: 50, transition: 'transform .08s',
              }}>
                <span style={{ fontSize: 10, color: 'var(--ink-faint)', fontWeight: 700 }}>{Number(date.slice(8))}</span>
                {r && r.daily_pct != null && <span style={{ fontSize: 11, fontWeight: 800, color: sgn(r.daily_pct), lineHeight: 1.1 }}>{pct(r.daily_pct)}</span>}
              </button>
            )
          })}
        </div>
      </Card>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '14px 16px 0' }}><SectionTitle>Daily history</SectionTitle></div>
        <div style={{ overflowX: 'auto' }}>
          <DataTable data={[...rows].reverse().map(Rec)} columns={histCols} rowKey={r => String(r.date)} emptyMessage="No history yet." maxHeight={420} />
        </div>
      </Card>
      <Modal open={!!openDay} onClose={() => setOpenDay(null)} title={openDay ?? ''} size="md">
        {openDay && <DayBreakdown date={openDay} />}
      </Modal>
    </div>
  )
}
const navBtn: React.CSSProperties = { width: 30, height: 30, borderRadius: 8, border: '1px solid var(--surface-rule)', background: 'var(--surface)', color: 'var(--ink)', cursor: 'pointer', fontSize: 16, fontWeight: 700 }
function Dot({ c, t }: { c: string; t: string }) { return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 3, background: c }} />{t}</span> }

function DayBreakdown({ date }: { date: string }) {
  const dayQ = useQuery({ queryKey: ['perf', 'day', date], queryFn: () => getPerfDay(date) })
  const d = dayQ.data
  if (dayQ.isLoading) return <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 12 }}>Loading…</div>
  if (!d) return null
  const contribs = [...d.attribution.top_winners, ...d.attribution.top_losers].slice(0, 8)
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        <Mini label="Daily P/L" value={usd(d.row.daily_pnl)} color={sgn(d.row.daily_pnl)} />
        <Mini label="Daily %" value={pct(d.row.daily_pct)} color={sgn(d.row.daily_pct)} />
        <Mini label="Ending value" value={usd(d.row.ending_value)} />
      </div>
      {contribs.length > 0 && <Block title="Top contributors">{contribs.map(c => <Line2 key={c.symbol} l={c.symbol} r={usd(c.contribution)} c={sgn(c.contribution)} />)}</Block>}
      {d.attribution.trades.length > 0 && <Block title={`Trades (${d.attribution.trades.length})`}>{d.attribution.trades.map((t, i) => <Line2 key={i} l={`${String(t.side ?? '').toUpperCase()} ${String(t.ticker ?? '')}`} r={t.realized_gl != null ? usd(Number(t.realized_gl)) : ''} c={sgn(t.realized_gl as number)} />)}</Block>}
      <Block title={`Positions (${d.positions.length})`}>{d.positions.map(p => <Line2 key={p.symbol} l={`${p.symbol} · ${usd(p.market_value)}`} r={usd(p.unrealized_gl)} c={sgn(p.unrealized_gl)} />)}</Block>
    </div>
  )
}
function Block({ title, children }: { title: string; children: React.ReactNode }) { return <div><div style={lbl}>{title}</div><div style={{ marginTop: 4 }}>{children}</div></div> }
function Line2({ l, r, c }: { l: string; r: string; c?: string }) { return <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--surface-rule)' }}><span style={{ color: 'var(--ink-muted)' }}>{l}</span><span style={{ color: c ?? 'var(--ink)', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{r}</span></div> }

// ── Holdings ────────────────────────────────────────────────────────────────────
function Holdings() {
  const q = useQuery({ queryKey: ['perf', 'positions'], queryFn: getPerfPositions })
  if (q.isLoading) return <LoadingState message="Loading positions…" />
  const poss = (q.data?.positions ?? []) as PerfPosition[]
  const cols: ColDef<Record<string, unknown>>[] = [
    { key: 'symbol', label: 'Symbol', sortable: true, align: 'left', render: v => <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{String(v)}</span> },
    { key: 'qty', label: 'Qty', align: 'right', render: v => String(v) },
    { key: 'last_price', label: 'Last', align: 'right', render: v => usd(num(v)) },
    { key: 'market_value', label: 'Mkt Value', sortable: true, align: 'right', render: v => usd(num(v)) },
    { key: 'cost_basis', label: 'Cost Basis', align: 'right', render: v => usd(num(v)) },
    { key: 'unrealized_gl', label: 'Unreal. $', sortable: true, align: 'right', render: v => <span style={{ color: sgn(v as number), fontWeight: 700 }}>{usd(num(v))}</span> },
    { key: 'unrealized_gl_pct', label: 'Unreal. %', align: 'right', render: v => <span style={{ color: sgn(v as number) }}>{pct(v as number)}</span> },
  ]
  return (
    <Card style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <SectionTitle>Holdings</SectionTitle>
        {q.data?.date && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-faint)' }}>as of {q.data.date} · {poss.length} positions</span>}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <DataTable data={poss.map(Rec)} columns={cols} rowKey={r => String(r.symbol)} defaultSortKey="market_value" defaultSortDir="desc" emptyMessage="No positions in the latest snapshot." />
      </div>
    </Card>
  )
}

// ── Manage drawer (export · cash flows · checks · sync log) ─────────────────────
function ManagePanel({ range }: { range: PerfRange }) {
  const qc = useQueryClient()
  const flowsQ = useQuery({ queryKey: ['perf', 'cashflows'], queryFn: getCashFlows })
  const logQ = useQuery({ queryKey: ['perf', 'synclog'], queryFn: getPerfSyncLog })
  const issuesQ = useQuery({ queryKey: ['perf', 'validate'], queryFn: getPerfValidate })
  const [form, setForm] = useState<PerfCashFlow>({ date: new Date().toISOString().slice(0, 10), kind: 'deposit', amount: 0, note: '' })
  const addMut = useMutation({ mutationFn: addCashFlow, onSuccess: () => qc.invalidateQueries({ queryKey: ['perf'] }) })
  const delMut = useMutation({ mutationFn: deleteCashFlow, onSuccess: () => qc.invalidateQueries({ queryKey: ['perf'] }) })
  const flows = flowsQ.data?.cashflows ?? []
  const issues = (issuesQ.data?.issues ?? []).filter(i => i.severity !== 'info')
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 22 }}>
      <div>
        <div style={lbl}>Export ({range})</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <a href={exportUrl('json', range)} style={dlBtn}>⬇ JSON</a>
          <a href={exportUrl('csv', range)} style={dlBtn}>⬇ CSV</a>
        </div>
      </div>
      <div>
        <div style={lbl}>Data checks</div>
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {issues.length === 0 ? <Badge variant="success">All clear</Badge> :
            issues.slice(0, 8).map((i, idx) => <div key={idx} style={{ fontSize: 11, color: 'var(--ink-muted)' }}><Badge variant={i.severity === 'error' ? 'danger' : 'warning'}>{String(i.type)}</Badge> {i.date ? String(i.date) : ''}</div>)}
        </div>
      </div>
      <div>
        <div style={lbl}>Cash-flow ledger</div>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)', margin: '6px 0 10px' }}>Record deposits / withdrawals so added cash isn't counted as profit.</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input type="date" value={form.date} onChange={e => setForm({ ...form, date: e.target.value })} style={inp} />
            <select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })} style={inp}><option value="deposit">Deposit</option><option value="withdrawal">Withdrawal</option><option value="dividend">Dividend</option></select>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input type="number" placeholder="Amount" value={form.amount || ''} onChange={e => setForm({ ...form, amount: Number(e.target.value) })} style={{ ...inp, width: 110 }} />
            <input type="text" placeholder="Note" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} style={{ ...inp, flex: 1 }} />
            <button onClick={() => form.amount > 0 && addMut.mutate(form)} disabled={addMut.isPending} style={btn(true, addMut.isPending)}>Add</button>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          {flows.length === 0 ? <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>No cash flows recorded.</span> : flows.map((c, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '5px 0', borderTop: '1px solid var(--surface-rule)' }}>
              <span style={{ color: 'var(--ink-faint)', minWidth: 84 }}>{c.date}</span>
              <Badge variant={c.kind === 'withdrawal' ? 'danger' : 'success'}>{c.kind}</Badge>
              <span style={{ color: 'var(--ink)' }}>{usd(c.amount)}</span>
              <button onClick={() => delMut.mutate(c)} style={{ ...navBtn, width: 22, height: 22, fontSize: 13, marginLeft: 'auto' }}>×</button>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div style={lbl}>Sync log</div>
        <div style={{ marginTop: 8 }}>
          {(logQ.data?.log ?? []).slice(0, 10).map((l, i) => (
            <div key={i} style={{ fontSize: 10.5, color: l.ok ? 'var(--ink-faint)' : RED, padding: '2px 0', fontFamily: 'var(--font-mono)' }}>{String(l.ts ?? '')} · {l.ok ? 'ok' : 'FAIL'} {l.total_value != null ? '· ' + usd(Number(l.total_value)) : ''}{l.error ? ' · ' + String(l.error) : ''}</div>
          ))}
          {(logQ.data?.log ?? []).length === 0 && <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>No syncs logged yet.</span>}
        </div>
      </div>
    </div>
  )
}
const dlBtn: React.CSSProperties = { padding: '7px 16px', fontSize: 12, fontWeight: 700, borderRadius: 8, border: '1px solid var(--surface-rule)', background: 'var(--surface-soft)', color: 'var(--ink)', textDecoration: 'none' }
const inp: React.CSSProperties = { padding: '7px 9px', fontSize: 12, borderRadius: 7, border: '1px solid var(--surface-rule)', background: 'var(--surface)', color: 'var(--ink)' }
