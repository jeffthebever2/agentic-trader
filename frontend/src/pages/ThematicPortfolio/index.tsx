import { useState, useMemo } from 'react'
import type { CSSProperties } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Modal } from '@/components/ui/Modal'
import { openStepUp, stepUpHeaders } from '@/components/modals/StepUpModal'

// ── Types ─────────────────────────────────────────────────────────────────────
interface Scores {
  theme_score: number; catalyst_score: number; momentum_score: number
  fundamental_score: number; supply_chain_score: number; social_score: number
  entry_quality: number; risk_score: number; chase_risk: number; final_score: number
}

interface Position {
  ticker: string; name: string; theme: string; theme_name: string
  theme_color: string; theme_emoji: string
  entry_price: number; shares: number; conviction: number
  risk_level: string; category: string
  thesis: string; catalyst: string; thesis_bull: string; thesis_bear: string
  risk_warning: string; review_date: string; tags: string[]
  current_price: number | null; gain_pct: number | null; gain_usd: number | null
  market_value: number | null; scores: Scores; added_at: string
}

interface ThemeSummary {
  name: string; color: string; emoji: string; count: number
  market_value: number; allocation_pct: number
}

interface PortfolioData {
  ok: boolean
  positions: Position[]
  themes: Record<string, { name: string; color: string; emoji: string }>
  theme_groups: Record<string, Position[]>
  theme_summary: Record<string, ThemeSummary>
  summary: {
    position_count: number; total_market_value: number; total_cost_basis: number
    total_gain_usd: number; total_gain_pct: number
    winners_count: number; losers_count: number
    best_winner: string | null; worst_loser: string | null; data_note: string
  }
  notes: string
}

const CATEGORIES = ['core','growth','satellite','speculative','watchlist','avoid']
const RISK_LEVELS = ['low','medium','high','very_high']

// ── Helpers ───────────────────────────────────────────────────────────────────
const fmt$ = (n: number | null | undefined) => n == null ? '—' : n < 0 ? `-$${Math.abs(n) >= 1000 ? (Math.abs(n)/1000).toFixed(1)+'k' : Math.abs(n).toFixed(2)}` : n >= 1000 ? `$${(n/1000).toFixed(1)}k` : `$${n.toFixed(2)}`
const fmtPct = (n: number | null | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
const gainColor = (n: number | null | undefined): string => !n ? 'var(--ink-muted)' : n > 0 ? 'var(--success)' : 'var(--danger)'

const categoryVariant = (c: string): 'success'|'info'|'warning'|'danger'|'default' => {
  const m: Record<string, 'success'|'info'|'warning'|'danger'|'default'> = {
    core:'success', growth:'info', satellite:'default', speculative:'warning', watchlist:'default', avoid:'danger'
  }
  return m[c] ?? 'default'
}

const riskVariant = (r: string): 'success'|'info'|'warning'|'danger'|'default' => {
  const m: Record<string, 'success'|'info'|'warning'|'danger'|'default'> = {
    low:'success', medium:'info', high:'warning', very_high:'danger'
  }
  return m[r] ?? 'default'
}

const scoreColor = (s: number) => s >= 7 ? 'var(--success)' : s >= 5 ? 'var(--warning)' : 'var(--danger)'

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div style={{ textAlign: 'center', padding: '64px 24px' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>
        No positions yet
      </div>
      <div style={{ color: 'var(--ink-muted)', marginBottom: 24, maxWidth: 400, margin: '0 auto 24px' }}>
        Add your first thematic position to track thesis, conviction, risk, and performance by theme.
      </div>
      <button className="btn btn-primary" onClick={onAdd}>+ Add First Position</button>
    </div>
  )
}

// ── Score bar ─────────────────────────────────────────────────────────────────
function ScoreBar({ label, value, max = 10 }: { label: string; value: number; max?: number }) {
  const pct = Math.min((value / max) * 100, 100)
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--ink-muted)', marginBottom: 2 }}>
        <span>{label}</span><span style={{ color: scoreColor(value), fontWeight: 600 }}>{value.toFixed(1)}</span>
      </div>
      <div style={{ height: 4, background: 'var(--surface-rule)', borderRadius: 2 }}>
        <div style={{ height: 4, width: `${pct}%`, background: scoreColor(value), borderRadius: 2, transition: 'width .3s' }} />
      </div>
    </div>
  )
}

// ── Position form ─────────────────────────────────────────────────────────────
const EMPTY_FORM = {
  ticker:'', name:'', theme:'ai_leaders', entry_price:'', shares:'',
  conviction:7, risk_level:'medium', category:'watchlist',
  thesis:'', catalyst:'', thesis_bull:'', thesis_bear:'',
  risk_warning:'', review_date:'', tags:''
}

function PositionForm({
  initial, themes, onSave, onCancel, saving
}: {
  initial?: Partial<typeof EMPTY_FORM>
  themes: Record<string, { name: string; emoji: string }>
  onSave: (data: typeof EMPTY_FORM) => void
  onCancel: () => void
  saving: boolean
}) {
  const [f, setF] = useState({ ...EMPTY_FORM, ...initial })
  const set = (k: string, v: unknown) => setF(prev => ({ ...prev, [k]: v }))

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '7px 10px', background: 'var(--surface-soft)',
    border: '1px solid var(--surface-rule)', borderRadius: 'var(--radius-sm)',
    color: 'var(--ink)', fontSize: 13
  }
  const labelStyle: React.CSSProperties = {
    display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--ink-muted)',
    textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: 4
  }
  const row2: React.CSSProperties = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }
  const fieldBox: React.CSSProperties = { marginBottom: 12 }

  return (
    <div style={{ maxHeight: '70vh', overflowY: 'auto', paddingRight: 4 }}>
      <div style={row2}>
        <div style={fieldBox}>
          <label style={labelStyle}>Ticker *</label>
          <input style={inputStyle} value={f.ticker} onChange={e => set('ticker', e.target.value.toUpperCase())} placeholder="NVDA" />
        </div>
        <div style={fieldBox}>
          <label style={labelStyle}>Company Name</label>
          <input style={inputStyle} value={f.name} onChange={e => set('name', e.target.value)} placeholder="NVIDIA Corporation" />
        </div>
      </div>

      <div style={fieldBox}>
        <label style={labelStyle}>Theme</label>
        <select style={inputStyle} value={f.theme} onChange={e => set('theme', e.target.value)}>
          {Object.entries(themes).map(([k, v]) => (
            <option key={k} value={k}>{v.emoji} {v.name}</option>
          ))}
        </select>
      </div>

      <div style={row2}>
        <div style={fieldBox}>
          <label style={labelStyle}>Entry Price ($)</label>
          <input style={inputStyle} type="number" step="0.01" value={f.entry_price} onChange={e => set('entry_price', e.target.value)} placeholder="0.00" />
        </div>
        <div style={fieldBox}>
          <label style={labelStyle}>Shares / Units</label>
          <input style={inputStyle} type="number" step="0.0001" value={f.shares} onChange={e => set('shares', e.target.value)} placeholder="0" />
        </div>
      </div>

      <div style={row2}>
        <div style={fieldBox}>
          <label style={labelStyle}>Category</label>
          <select style={inputStyle} value={f.category} onChange={e => set('category', e.target.value)}>
            {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div style={fieldBox}>
          <label style={labelStyle}>Risk Level</label>
          <select style={inputStyle} value={f.risk_level} onChange={e => set('risk_level', e.target.value)}>
            {RISK_LEVELS.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      </div>

      <div style={fieldBox}>
        <label style={labelStyle}>Conviction (1–10)</label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <input type="range" min={1} max={10} value={f.conviction} onChange={e => set('conviction', Number(e.target.value))} style={{ flex: 1 }} />
          <span style={{ fontWeight: 700, color: scoreColor(f.conviction), minWidth: 20 }}>{f.conviction}</span>
        </div>
      </div>

      <div style={fieldBox}>
        <label style={labelStyle}>Thesis — Why We Own/Watch</label>
        <textarea style={{ ...inputStyle, minHeight: 72, resize: 'vertical' }} value={f.thesis} onChange={e => set('thesis', e.target.value)} placeholder="The core reason this stock belongs in the portfolio..." />
      </div>

      <div style={row2}>
        <div style={fieldBox}>
          <label style={labelStyle}>Bull Case</label>
          <textarea style={{ ...inputStyle, minHeight: 56, resize: 'vertical' }} value={f.thesis_bull} onChange={e => set('thesis_bull', e.target.value)} placeholder="What needs to happen for thesis to work..." />
        </div>
        <div style={fieldBox}>
          <label style={labelStyle}>Bear / Thesis Breaker</label>
          <textarea style={{ ...inputStyle, minHeight: 56, resize: 'vertical' }} value={f.thesis_bear} onChange={e => set('thesis_bear', e.target.value)} placeholder="What would break the thesis..." />
        </div>
      </div>

      <div style={fieldBox}>
        <label style={labelStyle}>Main Catalyst</label>
        <input style={inputStyle} value={f.catalyst} onChange={e => set('catalyst', e.target.value)} placeholder="Earnings, product launch, government contract..." />
      </div>

      <div style={fieldBox}>
        <label style={labelStyle}>Risk Warning</label>
        <input style={inputStyle} value={f.risk_warning} onChange={e => set('risk_warning', e.target.value)} placeholder="Key risk to monitor..." />
      </div>

      <div style={row2}>
        <div style={fieldBox}>
          <label style={labelStyle}>Review Date</label>
          <input style={inputStyle} type="date" value={f.review_date} onChange={e => set('review_date', e.target.value)} />
        </div>
        <div style={fieldBox}>
          <label style={labelStyle}>Tags (comma separated)</label>
          <input style={inputStyle} value={f.tags} onChange={e => set('tags', e.target.value)} placeholder="AI, semis, datacenter..." />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 16 }}>
        <button className="btn" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn btn-primary" onClick={() => onSave(f)} disabled={saving || !f.ticker.trim()}>
          {saving ? 'Saving…' : 'Save Position'}
        </button>
      </div>
    </div>
  )
}

// ── Position card ─────────────────────────────────────────────────────────────
function PositionCard({ pos, onEdit, onRemove, onTrade }: { pos: Position; onEdit: () => void; onRemove: () => void; onTrade: () => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <Card style={{ padding: 0, overflow: 'hidden' }}>
      {/* Color bar */}
      <div style={{ height: 3, background: pos.theme_color }} />
      <div style={{ padding: '14px 16px' }}>
        {/* Header row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.02em' }}>{pos.ticker}</span>
            <span style={{ fontSize: 12, color: 'var(--ink-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pos.name}</span>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
            <button onClick={onTrade} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)', fontSize: 12, padding: '2px 6px', fontWeight: 700 }}>Trade</button>
            <button onClick={onEdit} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-faint)', fontSize: 12, padding: '2px 6px' }}>Edit</button>
            <button onClick={onRemove} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)', fontSize: 12, padding: '2px 6px' }}>Remove</button>
          </div>
        </div>

        {/* Theme + badges */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          <span style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 'var(--radius-full)',
            background: pos.theme_color + '22', color: pos.theme_color,
            fontWeight: 600, border: `1px solid ${pos.theme_color}44`
          }}>
            {pos.theme_emoji} {pos.theme_name}
          </span>
          <Badge variant={categoryVariant(pos.category)}>{pos.category}</Badge>
          <Badge variant={riskVariant(pos.risk_level)}>{pos.risk_level} risk</Badge>
          <Badge variant={pos.conviction >= 7 ? 'success' : pos.conviction >= 5 ? 'warning' : 'danger'}>
            conviction {pos.conviction}/10
          </Badge>
        </div>

        {/* Price row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gap: 8, marginBottom: 10 }}>
          {[
            { label: 'Current', val: pos.current_price != null ? `$${pos.current_price.toFixed(2)}` : '—' },
            { label: 'Entry', val: pos.entry_price > 0 ? `$${pos.entry_price.toFixed(2)}` : '—' },
            { label: 'Gain %', val: fmtPct(pos.gain_pct), color: gainColor(pos.gain_pct) },
            { label: 'Gain $', val: fmt$(pos.gain_usd), color: gainColor(pos.gain_usd) },
            { label: 'Mkt Val', val: fmt$(pos.market_value) },
            { label: 'Shares', val: pos.shares > 0 ? pos.shares.toLocaleString() : '—' },
          ].map(({ label, val, color }) => (
            <div key={label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 10, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: color ?? 'var(--ink)', marginTop: 1 }}>{val}</div>
            </div>
          ))}
        </div>

        {/* Score pill */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--ink-muted)' }}>
            {pos.catalyst && <span>⚡ {pos.catalyst}</span>}
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '2px 10px', borderRadius: 'var(--radius-full)',
            background: scoreColor(pos.scores.final_score) + '18',
            border: `1px solid ${scoreColor(pos.scores.final_score)}44`,
            color: scoreColor(pos.scores.final_score), fontWeight: 700, fontSize: 12
          }}>
            Score {pos.scores.final_score.toFixed(1)}/10
          </div>
        </div>

        {/* Thesis (collapsed) */}
        {pos.thesis && (
          <div style={{ fontSize: 12, color: 'var(--ink-muted)', fontStyle: 'italic', borderTop: '1px solid var(--surface-rule)', paddingTop: 8, marginTop: 4 }}>
            "{pos.thesis.slice(0, 120)}{pos.thesis.length > 120 ? '…' : ''}"
          </div>
        )}

        {/* Expand toggle */}
        <button
          onClick={() => setExpanded(v => !v)}
          style={{ marginTop: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)', fontSize: 12 }}
        >
          {expanded ? '▲ Less detail' : '▼ Full thesis & scores'}
        </button>

        {expanded && (
          <div style={{ marginTop: 10, borderTop: '1px solid var(--surface-rule)', paddingTop: 12 }}>
            {pos.thesis_bull && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--success)', marginBottom: 2 }}>✅ Bull Case</div>
                <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{pos.thesis_bull}</div>
              </div>
            )}
            {pos.thesis_bear && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--danger)', marginBottom: 2 }}>❌ Bear / Thesis Breaker</div>
                <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{pos.thesis_bear}</div>
              </div>
            )}
            {pos.risk_warning && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--warning)', marginBottom: 2 }}>⚠️ Risk Warning</div>
                <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{pos.risk_warning}</div>
              </div>
            )}
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.04em' }}>Portfolio Scores</div>
              <ScoreBar label="Theme Strength" value={pos.scores.theme_score} />
              <ScoreBar label="Catalyst Quality" value={pos.scores.catalyst_score} />
              <ScoreBar label="Thesis / Bull+Bear" value={pos.scores.fundamental_score} />
              <ScoreBar label="Momentum" value={pos.scores.momentum_score} />
              <ScoreBar label="Entry Quality" value={pos.scores.entry_quality} />
              <ScoreBar label="Supply Chain Fit" value={pos.scores.supply_chain_score} />
              <ScoreBar label="Risk Score" value={pos.scores.risk_score} />
              <ScoreBar label="Not Chasing (higher=better)" value={10 - pos.scores.chase_risk} />
            </div>
            {pos.review_date && (
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 8 }}>Review by: {pos.review_date}</div>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

// ── Summary cards ─────────────────────────────────────────────────────────────
function SummaryCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <Card style={{ padding: '16px 20px' }}>
      <div style={{ fontSize: 11, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color ?? 'var(--ink)', letterSpacing: '-0.02em' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 3 }}>{sub}</div>}
    </Card>
  )
}

// ── Theme allocation bar ───────────────────────────────────────────────────────
function ThemeAllocationBar({ summary }: { summary: Record<string, ThemeSummary> }) {
  const sorted = Object.entries(summary).sort(([,a],[,b]) => b.market_value - a.market_value)
  return (
    <div>
      <div style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', gap: 2, marginBottom: 10 }}>
        {sorted.filter(([,v]) => v.market_value > 0).map(([key, th]) => (
          <div key={key} title={`${th.name}: ${th.allocation_pct.toFixed(1)}%`}
            style={{ flex: th.allocation_pct, background: th.color, minWidth: 4 }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 16px' }}>
        {sorted.map(([key, th]) => (
          <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: th.color, display: 'inline-block' }} />
            <span style={{ color: 'var(--ink-muted)' }}>{th.emoji} {th.name}</span>
            <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{th.allocation_pct.toFixed(1)}%</span>
            <span style={{ color: 'var(--ink-faint)' }}>({th.count})</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Auto-scan types ───────────────────────────────────────────────────────────
interface AutoSignal {
  id: string; ticker: string; name: string; conviction: number; theme: string
  thesis: string; catalyst: string; bull_case: string; bear_case: string
  crowd_view?: string; sentiment?: number
  target_pct: number; stop_pct: number; hold_days: number
  status: string; source: string; ts: string; raw_score: number
  score?: number
  source_breakdown?: Record<string, number>
  will_buy?: boolean
  score_threshold?: number
  buzz_tier?: string
  is_spike?: boolean
  confirmed?: boolean
  scan_appearances?: number
  // Portfolio-policy tags
  policy_kind?: string            // NEW | REPLACE
  replace_target?: string | null  // weakest holding to free up (REPLACE)
  size_factor?: number
  capacity_note?: string
  policy_reason?: string
}

interface PolicyInfo {
  capacity_note?: string
  suppress_generation?: boolean
  reason?: string
  n_existing?: number
  account_value?: number
  decisions?: { kind: string; ticker: string; add_target?: string | null; replace_target?: string | null; reason?: string }[]
}

interface SignalsData { ok: boolean; signals: AutoSignal[]; last_scan: string | null; policy?: PolicyInfo }
interface ScanStatus { status: string; detail: string; ts: number; last_scan?: string }

// ── AI Signals panel ──────────────────────────────────────────────────────────
// ── Holdings Brain (AI management of real broker holdings, human-in-the-loop) ───
interface BrainAction {
  kind: string
  reason?: string
  fraction?: number
  stop?: number | null
  target?: number | null
  conviction?: number
  risk_flags?: string[]
  source?: string
}
interface BrainHolding {
  ticker: string
  shares: number
  avg_cost: number
  last: number
  market_value: number
  pct_of_account: number
  unrealized_pct: number
  name?: string
}
interface BrainRow { holding: BrainHolding; action: BrainAction }
interface BrainPortfolio {
  posture: string
  risk_flags: string[]
  regime?: string
  n_positions?: number
  total_market_value?: number
}
interface BrainHoldingsResp {
  ok: boolean
  broker: string
  count: number
  regime: { regime?: string; crash_risk_score?: number; no_trade?: boolean }
  portfolio: BrainPortfolio
  holdings: BrainRow[]
  to_adopt: string[]
  closed: string[]
  ai_used: boolean
}
interface BrainProposal {
  id: string
  ticker: string
  broker: string
  action: BrainAction
  holding: BrainHolding
  status: string
  created_at: string
  priority?: boolean
}
interface BrainProposalsResp { ok: boolean; pending: BrainProposal[]; count: number; history: BrainProposal[] }

const brainActionColor = (k: string): string =>
  (({ EXIT: '#ef4444', TRIM: '#f59e0b', ADD: '#22c55e', SET_STOP: '#3b82f6', ADOPT: '#8b5cf6', HOLD: '#94a3b8' } as Record<string, string>)[k]) ?? '#94a3b8'

const brainNotice: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', marginBottom: 14,
  borderRadius: 7, background: 'rgba(99,102,241,.10)', border: '1px solid rgba(99,102,241,.30)',
  fontSize: 12, color: '#a5b4fc',
}
const brainChip: CSSProperties = { fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: '#fff' }

function brainErrText(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string } }; message?: string }
  return ax?.response?.data?.detail || ax?.message || 'error'
}

function HoldingsBrainPanel() {
  const qc = useQueryClient()
  const [execLive, setExecLive] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<string>('')

  const fidelityQ = useQuery<{ connected?: boolean; status?: string }>({
    queryKey: ['fidelity', 'status', 'brain'],
    queryFn: () => api.get('/fidelity/status').then(r => r.data),
    staleTime: 60_000, retry: false,
  })
  const connected = fidelityQ.data?.connected === true || fidelityQ.data?.status === 'connected'

  const holdingsQ = useQuery<BrainHoldingsResp>({
    queryKey: ['brain-holdings'],
    queryFn: () => api.get('/thematic/brain/holdings', { params: { use_ai: false } }).then(r => r.data),
    enabled: connected, staleTime: 30_000, retry: false,
  })

  const proposalsQ = useQuery<BrainProposalsResp>({
    queryKey: ['brain-proposals'],
    queryFn: () => api.get('/thematic/brain/proposals').then(r => r.data),
    enabled: connected, staleTime: 15_000,
  })

  const assessMut = useMutation({
    mutationFn: () => api.post('/thematic/brain/assess', null, { params: { use_ai: true } }).then(r => r.data),
    onSuccess: (d: { proposals_pending?: number }) => {
      setMsg(`Assessment complete — ${d?.proposals_pending ?? 0} new proposal(s).`)
      qc.invalidateQueries({ queryKey: ['brain-holdings'] })
      qc.invalidateQueries({ queryKey: ['brain-proposals'] })
    },
    onError: (e: unknown) => setMsg(`Assess failed: ${brainErrText(e)}`),
  })

  const approveMut = useMutation({
    mutationFn: ({ id, execute }: { id: string; execute: boolean }) =>
      api.post(`/thematic/brain/proposals/${id}/approve`, { execute }).then(r => r.data),
    onSuccess: (d: { previewed_only?: boolean; kind?: string; ticker?: string }) => {
      setMsg(d?.previewed_only
        ? `Preview OK for ${d.ticker} (${d.kind}). Tick "Execute live" then approve to place the order.`
        : `Order submitted for ${d?.ticker} (${d?.kind}).`)
      qc.invalidateQueries({ queryKey: ['brain-proposals'] })
      qc.invalidateQueries({ queryKey: ['brain-holdings'] })
    },
    onError: (e: unknown) => setMsg(`Approve blocked: ${brainErrText(e)}`),
  })

  const skipMut = useMutation({
    mutationFn: (id: string) => api.post(`/thematic/brain/proposals/${id}/skip`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['brain-proposals'] }),
  })

  const portfolio = holdingsQ.data?.portfolio
  const pending = proposalsQ.data?.pending ?? []
  const rows = holdingsQ.data?.holdings ?? []

  return (
    <Card style={{ padding: 20, marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 800, fontSize: 16, color: 'var(--ink)' }}>🧠 Holdings Brain</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
            AI reviews your real Fidelity holdings (including ones already in the account) and proposes hold / trim / add / exit. You approve every trade.
          </div>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => assessMut.mutate()}
          disabled={!connected || assessMut.isPending}
          style={{ fontSize: 13, padding: '7px 16px' }}
        >
          {assessMut.isPending ? '⏳ Assessing…' : '🧠 Assess Holdings Now'}
        </button>
      </div>

      {!connected && (
        <div style={brainNotice}>
          <span style={{ fontSize: 16 }}>🔌</span>
          <span><strong>Fidelity not connected.</strong> Connect in the Broker tab to let the brain read and manage your real holdings.</span>
        </div>
      )}

      {msg && <div style={brainNotice}>{msg}</div>}

      {connected && portfolio && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14, fontSize: 12, color: 'var(--ink-muted)' }}>
          <span>Regime: <strong>{portfolio.regime ?? '—'}</strong></span>
          <span>Posture: <strong style={{ color: portfolio.posture === 'reduce_risk' ? '#ef4444' : portfolio.posture === 'rebalance' ? '#f59e0b' : '#22c55e' }}>{portfolio.posture}</strong></span>
          <span>Positions: <strong>{portfolio.n_positions ?? rows.length}</strong></span>
          {(portfolio.risk_flags ?? []).map((f, i) => <Badge key={i} variant="warning">{f}</Badge>)}
        </div>
      )}

      {pending.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8, color: 'var(--ink)' }}>Pending proposals ({pending.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {pending.map(p => (
              <div key={p.id} style={{
                border: '1px solid var(--surface-rule)', borderRadius: 10, padding: 12,
                background: 'var(--surface-soft)', borderLeft: `4px solid ${brainActionColor(p.action.kind)}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: 15 }}>{p.ticker}</strong>
                  <span style={{ ...brainChip, background: brainActionColor(p.action.kind) }}>{p.action.kind}</span>
                  {p.priority && <Badge variant="danger">⚠ stop guard</Badge>}
                  {p.action.fraction ? <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{Math.round(p.action.fraction * 100)}%</span> : null}
                  <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{p.action.source}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-muted)', margin: '6px 0' }}>{p.action.reason}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <input type="checkbox" checked={!!execLive[p.id]} onChange={e => setExecLive(s => ({ ...s, [p.id]: e.target.checked }))} />
                    Execute live (real order)
                  </label>
                  <button className="btn btn-primary" style={{ fontSize: 12, padding: '5px 12px' }}
                    disabled={approveMut.isPending}
                    onClick={() => approveMut.mutate({ id: p.id, execute: !!execLive[p.id] })}>
                    {execLive[p.id] ? 'Approve & place' : 'Preview'}
                  </button>
                  <button className="btn" style={{ fontSize: 12, padding: '5px 12px' }} disabled={skipMut.isPending}
                    onClick={() => skipMut.mutate(p.id)}>Skip</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {connected && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th><th className="num">Shares</th><th className="num">Last</th>
                <th className="num">Unreal.</th><th className="num">% Acct</th><th>Brain</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.holding.ticker}>
                  <td className="sym">{r.holding.ticker}</td>
                  <td className="num font-mono">{r.holding.shares}</td>
                  <td className="num font-mono">${r.holding.last.toFixed(2)}</td>
                  <td className="num font-mono" style={{ color: r.holding.unrealized_pct >= 0 ? '#22c55e' : '#ef4444' }}>{r.holding.unrealized_pct.toFixed(1)}%</td>
                  <td className="num font-mono">{r.holding.pct_of_account.toFixed(1)}%</td>
                  <td><span style={{ ...brainChip, background: brainActionColor(r.action.kind) }}>{r.action.kind}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {connected && holdingsQ.isLoading && (
        <div style={{ textAlign: 'center', padding: '18px 0', color: 'var(--ink-faint)', fontSize: 13 }}>Loading holdings…</div>
      )}
      {connected && !holdingsQ.isLoading && rows.length === 0 && pending.length === 0 && (
        <div style={{ textAlign: 'center', padding: '18px 0', color: 'var(--ink-faint)', fontSize: 13 }}>No holdings found. Hit "Assess Holdings Now".</div>
      )}
    </Card>
  )
}

function AutoScanPanel({ onApproved }: { onApproved: () => void }) {
  const qc = useQueryClient()
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [dollarAmounts, setDollarAmounts] = useState<Record<string, string>>({})

  const statusQ = useQuery<ScanStatus>({
    queryKey: ['thematic-scan-status'],
    queryFn: () => api.get('/thematic/auto/status').then(r => r.data),
    refetchInterval: (q) => q.state.data?.status === 'running' ? 2000 : false,
  })

  const signalsQ = useQuery<SignalsData>({
    queryKey: ['thematic-auto-signals'],
    queryFn: () => api.get('/thematic/auto/signals').then(r => r.data),
    staleTime: 30_000,
  })

  const fidelityQ = useQuery<{ connected?: boolean; status?: string }>({
    queryKey: ['fidelity', 'status', 'thematic'],
    queryFn: () => api.get('/fidelity/status').then(r => r.data),
    staleTime: 60_000,
    retry: false,
  })
  const fidelityConnected = fidelityQ.data?.connected === true || fidelityQ.data?.status === 'connected'

  const scanMut = useMutation({
    mutationFn: () => api.post('/thematic/auto/scan'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thematic-scan-status'] })
      const poll = setInterval(() => {
        qc.invalidateQueries({ queryKey: ['thematic-scan-status'] })
        qc.invalidateQueries({ queryKey: ['thematic-auto-signals'] })
      }, 3000)
      setTimeout(() => clearInterval(poll), 120_000)
    },
  })

  const hilQ = useQuery<{ hil?: { dollar_amount?: number; conviction_scale?: boolean; fidelity_trade?: boolean } }>({
    queryKey: ['thematic-hil-settings'],
    queryFn: () => api.get('/thematic/auto/hil-settings').then(r => r.data),
    staleTime: 60_000,
  })
  const baseSize = hilQ.data?.hil?.dollar_amount ?? 500
  const convScale = hilQ.data?.hil?.conviction_scale !== false
  const autoSize = (conv: number) => convScale
    ? Math.round(baseSize * (0.4 + (Math.min(Math.max(conv, 1), 10) - 1) / 9 * 1.1))
    : Math.round(baseSize)

  // Route approved signals to Fidelity (real money). Persisted HIL pref — set
  // once, then every approve mirrors to Fidelity (behind a per-trade 2FA tap).
  // Only actually "live" when Fidelity is also connected.
  const routeFidelity = hilQ.data?.hil?.fidelity_trade === true
  const liveRouting = routeFidelity && fidelityConnected
  const hilMut = useMutation({
    mutationFn: (fidelity_trade: boolean) =>
      api.post('/thematic/auto/hil-settings', { fidelity_trade }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['thematic-hil-settings'] }),
  })

  const approveMut = useMutation({
    mutationFn: async (
      { id, dollar, live, ticker, approxUsd }:
      { id: string; dollar?: number; live: boolean; ticker: string; approxUsd: number },
    ) => {
      const body: Record<string, unknown> = dollar != null ? { dollar_amount: dollar } : {}
      let cfg: { headers: Record<string, string> } | undefined
      if (live) {
        // Real-money leg: explicit confirm, then a fresh 2FA tap whose token
        // rides along as X-Step-Up-Token (the server re-verifies it before the
        // order). Paper-only approvals skip all of this.
        const ok = window.confirm(
          `Place a REAL Fidelity limit order for ${ticker} (~$${approxUsd.toLocaleString()})?\n\n` +
          `This also books the paper position. You'll confirm with 2FA next.`,
        )
        if (!ok) throw new Error('cancelled')
        const verified = await openStepUp({
          title: `Authorize ${ticker} order`,
          copy: `Enter your 6-digit code to place a REAL ~$${approxUsd.toLocaleString()} limit order for ${ticker}.`,
        })
        if (!verified) throw new Error('cancelled')
        body.fidelity_trade = true
        body.execute_fidelity = true
        cfg = { headers: stepUpHeaders() }
      }
      return api.post(`/thematic/auto/signals/${id}/approve`, body, cfg).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thematic-auto-signals'] })
      qc.invalidateQueries({ queryKey: ['thematic-portfolio'] })
      setApprovingId(null)
      onApproved()
    },
    onError: (e: Error) => { if (e.message !== 'cancelled') console.error('approve failed:', e.message) },
  })

  const skipMut = useMutation({
    mutationFn: (id: string) => api.post(`/thematic/auto/signals/${id}/skip`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['thematic-auto-signals'] }),
  })

  const status = statusQ.data
  const signals = signalsQ.data?.signals ?? []
  const policy = signalsQ.data?.policy
  const isRunning = status?.status === 'running'

  const convColor = (c: number) => c >= 8 ? '#22c55e' : c >= 6 ? '#f59e0b' : '#94a3b8'

  return (
    <Card style={{ padding: 20, marginBottom: 20 }}>
      {/* Routing-state banner: paper-only / connected-but-paper / live */}
      {liveRouting ? (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '8px 12px', marginBottom: 14, borderRadius: 7,
          background: 'rgba(245,158,11,.12)', border: '1px solid rgba(245,158,11,.40)',
          fontSize: 12, color: '#fbbf24',
        }}>
          <span style={{ fontSize: 16 }}>⚡</span>
          <span><strong>Live routing ON.</strong> Approving a signal books the paper position <em>and</em> places a REAL Fidelity limit order — auto-sized ${baseSize} base × conviction, one 2FA tap per trade.</span>
        </div>
      ) : (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '8px 12px', marginBottom: 14, borderRadius: 7,
          background: 'rgba(99,102,241,.10)', border: '1px solid rgba(99,102,241,.30)',
          fontSize: 12, color: '#a5b4fc',
        }}>
          <span style={{ fontSize: 16 }}>📋</span>
          <span>{fidelityConnected
            ? <><strong>Paper only.</strong> Fidelity is connected — flip “Route to Fidelity” to mirror approved trades with real money.</>
            : <><strong>Paper trading active.</strong> All approved signals go to the paper book. Connect Fidelity in the Broker tab to enable real-money routing.</>}</span>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div style={{ fontWeight: 800, fontSize: 16, color: 'var(--ink)' }}>🤖 AI Auto-Picker</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
            Reddit · 🦁 Brave · 🧠 Memory · PR Releases · Finviz · Yahoo Movers · RSS · Google News · SA · Twitter · Insider → AI
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {status?.last_scan && (
            <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
              Last: {new Date((status.ts || 0) * 1000).toLocaleTimeString()}
            </span>
          )}
          {policy?.capacity_note && (
            <span
              title={policy.reason || ''}
              style={{
                fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 999,
                background: policy.suppress_generation ? 'rgba(245,158,11,.15)' : 'var(--surface-raised)',
                color: policy.suppress_generation ? '#fbbf24' : 'var(--ink-muted)',
                border: `1px solid ${policy.suppress_generation ? 'rgba(245,158,11,.4)' : 'var(--surface-rule)'}`,
              }}
            >
              📊 {policy.capacity_note}
            </span>
          )}
          {fidelityConnected && (
            <label
              style={{
                display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer',
                color: liveRouting ? '#fbbf24' : 'var(--ink-faint)', fontWeight: liveRouting ? 700 : 500,
              }}
              title="Mirror approved signals to Fidelity (real money). Each trade still needs a 2FA tap."
            >
              <input
                type="checkbox"
                checked={routeFidelity}
                disabled={hilMut.isPending}
                onChange={e => hilMut.mutate(e.target.checked)}
              />
              {liveRouting ? '⚡ Route to Fidelity (LIVE)' : 'Route to Fidelity'}
            </label>
          )}
          <button
            className="btn btn-primary"
            onClick={() => scanMut.mutate()}
            disabled={isRunning || scanMut.isPending}
            style={{ fontSize: 13, padding: '7px 16px' }}
          >
            {isRunning ? '⏳ Scanning…' : '🔍 Auto-Scan Now'}
          </button>
        </div>
      </div>

      {isRunning && (
        <div style={{ background: 'var(--surface-soft)', borderRadius: 8, padding: '10px 14px', marginBottom: 14, fontSize: 13, color: 'var(--ink-muted)' }}>
          <span style={{ marginRight: 8 }}>⏳</span>{status?.detail || 'Scanning social sources...'}
        </div>
      )}

      {signals.length === 0 && !isRunning && (
        <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--ink-faint)', fontSize: 13 }}>
          {policy?.suppress_generation
            ? <><div style={{ fontWeight: 700, color: '#fbbf24', marginBottom: 4 }}>🛡️ Managing existing positions</div>{policy.reason || 'Holding — no new trades this cycle.'}</>
            : 'No pending signals. Hit Auto-Scan to find momentum plays.'}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {signals.map(sig => (
          <div key={sig.id} style={{
            border: '1px solid var(--surface-rule)', borderRadius: 10, padding: 14,
            background: 'var(--surface-soft)', position: 'relative',
            borderLeft: `4px solid ${convColor(sig.conviction)}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
              {/* Left: ticker info */}
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 800, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.02em' }}>
                    {sig.ticker}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{sig.name}</span>
                  {/* Single unified 0-100 signal score (replaces conviction/10 + buzz). */}
                  {(() => {
                    const sc = sig.score ?? Math.round((sig.conviction ?? 7) * 9)
                    const col = sc >= 80 ? '#22c55e' : sc >= 60 ? '#eab308' : sc >= 40 ? '#f59e0b' : '#f87171'
                    return (
                      <span style={{
                        fontSize: 12, fontWeight: 800, padding: '2px 9px', borderRadius: 999,
                        background: col + '22', color: col, border: `1px solid ${col}55`,
                      }} title={`Unified signal score (conviction + momentum). Buzz tier: ${sig.buzz_tier ?? '—'}`}>
                        {sc}/100
                      </span>
                    )
                  })()}
                  {sig.confirmed && (
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#22c55e22', color: '#22c55e', border: '1px solid #22c55e44' }}>
                      ✓ {sig.scan_appearances}× confirmed
                    </span>
                  )}
                  {sig.is_spike && (
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#f59e0b22', color: '#f59e0b', border: '1px solid #f59e0b44' }}>
                      ⚡ 1× spike — unconfirmed
                    </span>
                  )}
                  {!sig.is_spike && !sig.confirmed && (
                    <span style={{ fontSize: 10, color: 'var(--ink-faint)', padding: '1px 5px', background: 'var(--surface-raised)', borderRadius: 3 }}>
                      {sig.scan_appearances ?? 1}× seen
                    </span>
                  )}
                  {sig.policy_kind === 'REPLACE' && sig.replace_target && (
                    <span title={sig.policy_reason || ''} style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#a855f722', color: '#c084fc', border: '1px solid #a855f744' }}>
                      ♻ replace {sig.replace_target}
                    </span>
                  )}
                  {sig.will_buy === false && !sig.is_spike && (
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#f8717122', color: '#f87171', border: '1px solid #f8717144' }}>
                      below min {sig.score_threshold ?? 40}
                    </span>
                  )}
                </div>
                {sig.source_breakdown && Object.keys(sig.source_breakdown).length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                    {Object.entries(sig.source_breakdown)
                      .filter(([k]) => !k.includes('bonus') && !k.includes('combo'))
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 6)
                      .map(([src, pts]) => {
                        const icons: Record<string, string> = {
                          trusted_twitter: '🐦', reddit: '🔴', marketaux: '📰',
                          seeking_alpha: '🔬', insider: '👔', stockanalysis: '📈',
                          google_news: '🌐', ddg: '🦆', yahoo: '📊',
                          press_releases: '📢', finviz: '🏆', rss_news: '📡',
                          av_movers: '⚡', yahoo_movers: '🚀', brave: '🦁',
                          scan_memory: '🧠',
                        }
                        return (
                          <span key={src} style={{
                            fontSize: 10, padding: '1px 5px', borderRadius: 4,
                            background: 'var(--surface-raised)', color: 'var(--ink-muted)',
                            border: '1px solid var(--surface-rule)',
                          }}>
                            {icons[src] ?? '·'} {src.replaceAll('_', ' ')} {pts.toFixed(0)}
                          </span>
                        )
                      })}
                    {(sig.source_breakdown['multi_source_bonus'] ?? 0) > 0 && (
                      <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 4, background: '#22c55e22', color: '#22c55e', border: '1px solid #22c55e44' }}>
                        ✓ {Object.keys(sig.source_breakdown).filter(k => !k.includes('bonus') && !k.includes('combo')).length} sources
                      </span>
                    )}
                    {(sig.source_breakdown['insider_social_combo'] ?? 0) > 0 && (
                      <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 4, background: '#f59e0b22', color: '#f59e0b', border: '1px solid #f59e0b44' }}>
                        🔥 insider+social
                      </span>
                    )}
                  </div>
                )}
                <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 4 }}>
                  <strong>Thesis:</strong> {sig.thesis}
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 4 }}>
                  <strong>Catalyst:</strong> {sig.catalyst}
                </div>
                {sig.crowd_view && (
                  <div style={{ fontSize: 12, marginBottom: 4, color: (sig.sentiment ?? 0) < -0.2 ? '#f87171' : (sig.sentiment ?? 0) > 0.2 ? '#22c55e' : 'var(--ink-muted)' }}>
                    <strong>🗣 Crowd{sig.sentiment != null ? ` (${sig.sentiment > 0 ? '+' : ''}${sig.sentiment.toFixed(1)})` : ''}:</strong> {sig.crowd_view}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--ink-faint)', flexWrap: 'wrap' }}>
                  <span>🎯 +{sig.target_pct}% target</span>
                  <span>🛑 -{sig.stop_pct}% stop</span>
                  <span>📅 ~{sig.hold_days}d hold</span>
                  <span style={{ color: '#22c55e', fontSize: 11 }}>▲ {sig.bull_case}</span>
                  <span style={{ color: '#f87171', fontSize: 11 }}>▼ {sig.bear_case}</span>
                </div>
              </div>

              {/* Right: actions */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 180 }}>
                {approvingId === sig.id ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <label style={{ fontSize: 11, color: 'var(--ink-faint)' }}>Dollar amount (override auto-size)</label>
                    <input
                      type="number" min="100" step="100"
                      value={dollarAmounts[sig.id] ?? String(baseSize)}
                      onChange={e => setDollarAmounts(prev => ({ ...prev, [sig.id]: e.target.value }))}
                      style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'var(--surface)', color: 'var(--ink)', fontSize: 13, width: '100%' }}
                    />
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-primary" style={{ fontSize: 12, flex: 1, padding: '5px 0' }}
                        disabled={approveMut.isPending}
                        onClick={() => {
                          const d = parseFloat(dollarAmounts[sig.id] ?? String(baseSize)) || baseSize
                          approveMut.mutate({ id: sig.id, dollar: d, live: liveRouting, ticker: sig.ticker, approxUsd: d })
                        }}>
                        {approveMut.isPending ? '…' : liveRouting ? 'Confirm · Fidelity' : 'Confirm'}
                      </button>
                      <button className="btn" style={{ fontSize: 12, padding: '5px 10px' }} onClick={() => setApprovingId(null)}>✕</button>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {sig.will_buy === false && (
                      <div style={{ fontSize: 10, color: '#f87171', padding: '3px 6px', background: '#f8717111', borderRadius: 4, border: '1px solid #f8717133' }}>
                        ⚠ Low buzz — below buy threshold
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        className="btn btn-primary"
                        style={{
                          fontSize: 12, flex: 1, padding: '6px 0',
                          background: sig.confirmed && sig.will_buy ? '#16a34a'
                            : sig.is_spike ? '#92400e' : '#1d4ed8',
                          borderColor: sig.confirmed && sig.will_buy ? '#16a34a'
                            : sig.is_spike ? '#92400e' : '#1d4ed8',
                        }}
                        disabled={approveMut.isPending}
                        onClick={() => approveMut.mutate({ id: sig.id, live: liveRouting, ticker: sig.ticker, approxUsd: autoSize(sig.conviction) })}
                        title={liveRouting
                          ? `LIVE: books paper + places a REAL ~$${autoSize(sig.conviction)} Fidelity order (2FA required)`
                          : `Auto-sized ~$${autoSize(sig.conviction)} (HIL base $${baseSize}${convScale ? ' × conviction' : ''})`}
                      >
                        {approveMut.isPending ? '…'
                          : sig.confirmed && sig.will_buy ? `${liveRouting ? '⚡' : '✓'} Trade It · ~$${autoSize(sig.conviction)}${liveRouting ? ' · LIVE' : ''}`
                          : sig.is_spike ? '⚡ Spike — Trade Anyway?'
                          : `↗ Trade It · ~$${autoSize(sig.conviction)}${liveRouting ? ' · LIVE' : ''}`}
                      </button>
                      <button
                        className="btn"
                        style={{ fontSize: 12, padding: '6px 8px' }}
                        onClick={() => setApprovingId(sig.id)}
                        title="Custom dollar amount"
                      >
                        $
                      </button>
                      <button
                        className="btn"
                        style={{ fontSize: 12, padding: '6px 10px', color: 'var(--danger)' }}
                        onClick={() => skipMut.mutate(sig.id)}
                        disabled={skipMut.isPending}
                      >
                        Skip
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ThematicPortfolioPage() {
  const qc = useQueryClient()

  // View state
  const [view, setView] = useState<'grid'|'table'|'theme'>('theme')
  const [filterTheme, setFilterTheme] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [filterRisk, setFilterRisk] = useState('')
  const [search, setSearch] = useState('')

  // Modal state
  const [addOpen, setAddOpen] = useState(false)
  const [editPos, setEditPos] = useState<Position | null>(null)
  const [removePos, setRemovePos] = useState<Position | null>(null)
  const [tradePos, setTradePos] = useState<Position | null>(null)
  const [tradeDollar, setTradeDollar] = useState('500')
  const [tradeStop, setTradeStop] = useState('8')
  const [tradeTarget, setTradeTarget] = useState('25')
  const [tradeMode, setTradeMode] = useState<'paper' | 'fidelity'>('paper')
  const [fidelityExecute, setFidelityExecute] = useState(false)
  const [fidelityAlsoPaper, setFidelityAlsoPaper] = useState(true)

  // Data
  const { data, isLoading, error, refetch } = useQuery<PortfolioData>({
    queryKey: ['thematic-portfolio'],
    queryFn: () => api.get('/thematic/portfolio').then(r => r.data),
    staleTime: 60_000,
  })

  // Mutations
  const addMut = useMutation({
    mutationFn: (body: object) => api.post('/thematic/portfolio/position', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['thematic-portfolio'] }); setAddOpen(false) },
  })
  const editMut = useMutation({
    mutationFn: ({ ticker, body }: { ticker: string; body: object }) =>
      api.put(`/thematic/portfolio/position/${ticker}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['thematic-portfolio'] }); setEditPos(null) },
  })
  const removeMut = useMutation({
    mutationFn: (ticker: string) => api.delete(`/thematic/portfolio/position/${ticker}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['thematic-portfolio'] }); setRemovePos(null) },
  })
  const tradeMut = useMutation({
    mutationFn: (body: object) => api.post('/thematic/trade', body).then(r => r.data),
    onSuccess: () => { setTradePos(null) },
  })
  const fidelityTradeMut = useMutation({
    mutationFn: (body: object) => api.post('/fidelity/thematic-trade', body).then(r => r.data),
    onSuccess: () => { if (fidelityExecute) setTradePos(null) },
  })

  // Form → API body
  function formToBody(f: Record<string, unknown>) {
    return {
      ...f,
      ticker: String(f.ticker).toUpperCase().trim(),
      entry_price: parseFloat(String(f.entry_price)) || 0,
      shares: parseFloat(String(f.shares)) || 0,
      conviction: Number(f.conviction),
      tags: String(f.tags || '').split(',').map((t: string) => t.trim()).filter(Boolean),
    }
  }

  // Filtered positions
  const positions = useMemo(() => {
    if (!data) return []
    return (data.positions ?? []).filter(p => {
      if (filterTheme && p.theme !== filterTheme) return false
      if (filterCategory && p.category !== filterCategory) return false
      if (filterRisk && p.risk_level !== filterRisk) return false
      if (search) {
        const q = search.toLowerCase()
        if (!p.ticker.toLowerCase().includes(q) &&
            !p.name.toLowerCase().includes(q) &&
            !p.thesis.toLowerCase().includes(q) &&
            !p.catalyst.toLowerCase().includes(q)) return false
      }
      return true
    })
  }, [data, filterTheme, filterCategory, filterRisk, search])

  const s = data?.summary
  const themes = data?.themes ?? {}
  const themeSummary = data?.theme_summary ?? {}

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div style={{ maxWidth: 1280, margin: '0 auto' }}>
      {/* Trade modal — Paper or Fidelity */}
      {tradePos && (
        <Modal open title={`Trade — ${tradePos.ticker}`} onClose={() => { setTradePos(null); tradeMut.reset(); fidelityTradeMut.reset() }}>
          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: 0, marginBottom: 16, border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden', width: 'fit-content' }}>
            {(['paper', 'fidelity'] as const).map(mode => (
              <button key={mode} onClick={() => { setTradeMode(mode); tradeMut.reset(); fidelityTradeMut.reset() }}
                style={{ padding: '6px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer', border: 'none', borderRadius: 0,
                  background: tradeMode === mode ? 'var(--accent)' : 'var(--surface-soft)',
                  color: tradeMode === mode ? '#fff' : 'var(--ink-muted)' }}>
                {mode === 'paper' ? '📋 Paper' : '🏦 Fidelity'}
              </button>
            ))}
          </div>

          {tradeMode === 'paper' && (
            <p style={{ color: 'var(--ink-muted)', marginBottom: 12, fontSize: 13 }}>
              Injects buy into unified brain paper account. Paper trader manages exits.
            </p>
          )}
          {tradeMode === 'fidelity' && (
            <p style={{ color: '#f59e0b', marginBottom: 12, fontSize: 13, background: '#fef3c7', padding: '8px 12px', borderRadius: 6, border: '1px solid #f59e0b55' }}>
              ⚠️ <strong>Real money.</strong> Places a Limit order in your Fidelity account via browser automation. Requires Fidelity login. Step-up 2FA required.
            </p>
          )}

          {/* Success / error */}
          {(tradeMut.isSuccess || fidelityTradeMut.isSuccess) && (
            <div style={{ background: '#dcfce7', border: '1px solid #16a34a', borderRadius: 6, padding: '10px 14px', marginBottom: 14, fontSize: 13, color: '#15803d' }}>
              {tradeMode === 'paper'
                ? 'Paper trade placed! Check Paper Trading for status.'
                : fidelityExecute
                  ? `Fidelity order placed! ${(fidelityTradeMut.data as { shares?: number; cost?: number })?.shares ?? ''} shares @ $${(fidelityTradeMut.data as { entry_price?: number })?.entry_price?.toFixed(2) ?? ''}`
                  : `Preview: ${(fidelityTradeMut.data as { shares?: number; cost?: number })?.shares ?? ''} shares, cost ~$${(fidelityTradeMut.data as { cost?: number })?.cost?.toFixed(2) ?? '—'}. Toggle "Execute" to place.`
              }
            </div>
          )}
          {(tradeMut.isError || fidelityTradeMut.isError) && (
            <div style={{ background: '#fee2e2', border: '1px solid #dc2626', borderRadius: 6, padding: '10px 14px', marginBottom: 14, fontSize: 13, color: '#dc2626' }}>
              {((tradeMut.error || fidelityTradeMut.error) as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Trade failed'}
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 600 }}>
              Dollar Amount
              <input type="number" min="1" step="100" value={tradeDollar}
                onChange={e => setTradeDollar(e.target.value)}
                style={{ display: 'block', width: '100%', marginTop: 4, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'var(--surface-soft)', color: 'var(--ink)', fontSize: 13 }} />
            </label>
            <div style={{ display: 'flex', gap: 12 }}>
              <label style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 600, flex: 1 }}>
                Stop % Below Entry
                <input type="number" min="1" max="30" step="0.5" value={tradeStop}
                  onChange={e => setTradeStop(e.target.value)}
                  style={{ display: 'block', width: '100%', marginTop: 4, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'var(--surface-soft)', color: 'var(--ink)', fontSize: 13 }} />
              </label>
              <label style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 600, flex: 1 }}>
                Target % Above Entry
                <input type="number" min="1" max="100" step="0.5" value={tradeTarget}
                  onChange={e => setTradeTarget(e.target.value)}
                  style={{ display: 'block', width: '100%', marginTop: 4, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'var(--surface-soft)', color: 'var(--ink)', fontSize: 13 }} />
              </label>
            </div>

            {tradePos.current_price && (
              <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
                Current price: <strong>${tradePos.current_price?.toFixed(2)}</strong> ·
                ~{Math.floor(parseFloat(tradeDollar || '0') / tradePos.current_price)} shares ·
                Stop ~${(tradePos.current_price * (1 - parseFloat(tradeStop || '5') / 100)).toFixed(2)} ·
                Target ~${(tradePos.current_price * (1 + parseFloat(tradeTarget || '10') / 100)).toFixed(2)}
              </div>
            )}

            {tradeMode === 'fidelity' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px', background: 'var(--surface-soft)', borderRadius: 8, border: '1px solid var(--surface-rule)' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', fontWeight: 600 }}>
                  <input type="checkbox" checked={fidelityAlsoPaper} onChange={e => setFidelityAlsoPaper(e.target.checked)} />
                  Also mirror in paper account (for P&L tracking)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', color: fidelityExecute ? '#dc2626' : 'var(--ink-muted)', fontWeight: fidelityExecute ? 700 : 400 }}>
                  <input type="checkbox" checked={fidelityExecute} onChange={e => setFidelityExecute(e.target.checked)} />
                  {fidelityExecute ? '🔴 Execute = ON — will place REAL ORDER' : 'Execute (off = preview only)'}
                </label>
                {fidelityExecute && (
                  <div style={{ fontSize: 12, color: '#dc2626', marginTop: 2 }}>
                    Make sure Fidelity session is active (Broker tab → Fidelity Login). Requires step-up 2FA.
                  </div>
                )}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
            <button className="btn" onClick={() => { setTradePos(null); tradeMut.reset(); fidelityTradeMut.reset() }}>Cancel</button>
            {tradeMode === 'paper' ? (
              <button className="btn btn-primary"
                disabled={tradeMut.isPending || tradeMut.isSuccess}
                onClick={() => tradeMut.mutate({
                  ticker: tradePos.ticker,
                  dollar_amount: parseFloat(tradeDollar) || 500,
                  stop_pct: parseFloat(tradeStop) || 5,
                  target_pct: parseFloat(tradeTarget) || 10,
                })}>
                {tradeMut.isPending ? 'Trading…' : 'Place Paper Trade'}
              </button>
            ) : (
              <button
                className="btn btn-primary"
                disabled={fidelityTradeMut.isPending}
                style={{ background: fidelityExecute ? '#dc2626' : undefined, borderColor: fidelityExecute ? '#dc2626' : undefined }}
                onClick={() => fidelityTradeMut.mutate({
                  ticker: tradePos.ticker,
                  dollar_amount: parseFloat(tradeDollar) || 500,
                  stop_pct: parseFloat(tradeStop) || 5,
                  target_pct: parseFloat(tradeTarget) || 10,
                  theme: tradePos.theme || 'future_tech',
                  thesis: tradePos.thesis || '',
                  catalyst: tradePos.catalyst || '',
                  execute: fidelityExecute,
                  also_paper_trade: fidelityAlsoPaper,
                })}>
                {fidelityTradeMut.isPending
                  ? (fidelityExecute ? 'Placing order…' : 'Previewing…')
                  : fidelityExecute ? '🔴 Place Fidelity Order' : '👁 Preview Fidelity Order'}
              </button>
            )}
          </div>
        </Modal>
      )}

      {/* Remove confirmation modal */}
      {removePos && (
        <Modal open title={`Remove ${removePos.ticker}?`} onClose={() => setRemovePos(null)}>
          <p style={{ color: 'var(--ink-muted)', marginBottom: 16 }}>
            Remove <strong>{removePos.ticker}</strong> ({removePos.name}) from your thematic portfolio?
            Thesis and notes will be permanently deleted.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button className="btn" onClick={() => setRemovePos(null)}>Cancel</button>
            <button className="btn btn-danger" onClick={() => removeMut.mutate(removePos.ticker)} disabled={removeMut.isPending}>
              {removeMut.isPending ? 'Removing…' : 'Remove'}
            </button>
          </div>
        </Modal>
      )}

      {/* Add modal */}
      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="Add Position" size="lg">
        <PositionForm
          themes={themes}
          onSave={f => addMut.mutate(formToBody(f))}
          onCancel={() => setAddOpen(false)}
          saving={addMut.isPending}
        />
      </Modal>

      {/* Edit modal */}
      {editPos && (
        <Modal open onClose={() => setEditPos(null)} title={`Edit ${editPos.ticker}`} size="lg">
          <PositionForm
            themes={themes}
            initial={{
              ticker: editPos.ticker, name: editPos.name, theme: editPos.theme,
              entry_price: String(editPos.entry_price), shares: String(editPos.shares),
              conviction: editPos.conviction, risk_level: editPos.risk_level,
              category: editPos.category, thesis: editPos.thesis,
              catalyst: editPos.catalyst, thesis_bull: editPos.thesis_bull,
              thesis_bear: editPos.thesis_bear, risk_warning: editPos.risk_warning,
              review_date: editPos.review_date, tags: (editPos.tags || []).join(', ')
            }}
            onSave={f => editMut.mutate({ ticker: editPos.ticker, body: formToBody(f) })}
            onCancel={() => setEditPos(null)}
            saving={editMut.isPending}
          />
        </Modal>
      )}

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h1 style={{ margin: 0, fontWeight: 800, fontSize: 22 }}>Thematic Portfolio</h1>
          <p style={{ margin: '4px 0 0', color: 'var(--ink-muted)', fontSize: 13 }}>
            High-conviction positions grouped by long-term theme
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={() => refetch()}>↻ Refresh</button>
          <button className="btn btn-primary" onClick={() => setAddOpen(true)}>+ Add Position</button>
        </div>
      </div>

      {/* Data note */}
      {s && (
        <div style={{
          padding: '8px 14px', marginBottom: 16, borderRadius: 'var(--radius-sm)',
          background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)',
          fontSize: 12, color: 'var(--ink-faint)'
        }}>
          ℹ️ {s.data_note} Fundamental/social scores are placeholders pending live data integrations.
        </div>
      )}

      {isLoading && (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--ink-muted)' }}>Loading portfolio…</div>
      )}
      {error && (
        <div style={{ padding: 20, color: 'var(--danger)', background: 'var(--surface-soft)', borderRadius: 'var(--radius)' }}>
          Error loading portfolio: {String(error)}
        </div>
      )}

      {/* AI Auto-Scan Panel — always visible */}
      <AutoScanPanel onApproved={() => refetch()} />

      <HoldingsBrainPanel />

      {data && (
        <>
          {/* Summary cards */}
          {s && s.position_count > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 20 }}>
              <SummaryCard label="Positions" value={String(s.position_count)} sub={`${s.winners_count}W / ${s.losers_count}L`} />
              <SummaryCard label="Market Value" value={fmt$(s.total_market_value)} sub="at current prices" />
              <SummaryCard label="Total Gain $" value={fmt$(s.total_gain_usd)} color={gainColor(s.total_gain_usd)} sub={fmtPct(s.total_gain_pct)} />
              {s.best_winner && <SummaryCard label="Best Winner" value={s.best_winner} color="var(--success)" />}
              {s.worst_loser && <SummaryCard label="Worst Loser" value={s.worst_loser} color="var(--danger)" />}
            </div>
          )}

          {/* Theme allocation */}
          {s && s.position_count > 0 && (
            <Card style={{ padding: '16px 20px', marginBottom: 20 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: 10 }}>
                Theme Allocation
              </div>
              <ThemeAllocationBar summary={data.theme_summary ?? {}} />
            </Card>
          )}

          {/* Empty state */}
          {s?.position_count === 0 && <EmptyState onAdd={() => setAddOpen(true)} />}

          {s && s.position_count > 0 && (
            <>
              {/* Filters + view toggle */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16, alignItems: 'center' }}>
                <input
                  value={search} onChange={e => setSearch(e.target.value)}
                  placeholder="Search ticker, thesis, catalyst…"
                  style={{ padding: '7px 12px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 'var(--radius-sm)', color: 'var(--ink)', fontSize: 13, minWidth: 200 }}
                />
                <select value={filterTheme} onChange={e => setFilterTheme(e.target.value)}
                  style={{ padding: '7px 10px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 'var(--radius-sm)', color: 'var(--ink)', fontSize: 13 }}>
                  <option value="">All themes</option>
                  {Object.entries(themes).map(([k,v]) => <option key={k} value={k}>{v.emoji} {v.name}</option>)}
                </select>
                <select value={filterCategory} onChange={e => setFilterCategory(e.target.value)}
                  style={{ padding: '7px 10px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 'var(--radius-sm)', color: 'var(--ink)', fontSize: 13 }}>
                  <option value="">All categories</option>
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <select value={filterRisk} onChange={e => setFilterRisk(e.target.value)}
                  style={{ padding: '7px 10px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 'var(--radius-sm)', color: 'var(--ink)', fontSize: 13 }}>
                  <option value="">All risk levels</option>
                  {RISK_LEVELS.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                  {(['theme','grid','table'] as const).map(v => (
                    <button key={v} onClick={() => setView(v)}
                      className={`btn ${view === v ? 'btn-primary' : ''}`}
                      style={{ padding: '6px 12px', fontSize: 12 }}>
                      {v === 'theme' ? '📁 By Theme' : v === 'grid' ? '⊞ Grid' : '≡ Table'}
                    </button>
                  ))}
                </div>
              </div>

              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>
                {positions.length} position{positions.length !== 1 ? 's' : ''} shown
              </div>

              {/* Grid view */}
              {view === 'grid' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 14 }}>
                  {positions.map(p => (
                    <PositionCard key={p.ticker} pos={p} onEdit={() => setEditPos(p)} onRemove={() => setRemovePos(p)} onTrade={() => { setTradePos(p); setTradeDollar('500'); setTradeStop('8'); setTradeTarget('25'); tradeMut.reset() }} />
                  ))}
                </div>
              )}

              {/* Theme view */}
              {view === 'theme' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
                  {Object.entries(data.theme_groups ?? {})
                    .filter(([, ps]) => ps.some(p => positions.includes(p)))
                    .map(([themeKey, themePositions]) => {
                      const filtered = themePositions.filter(p => positions.includes(p))
                      if (!filtered.length) return null
                      const th = themes[themeKey] ?? { name: themeKey, color: '#6366f1', emoji: '📊' }
                      const thSummary = themeSummary[themeKey]
                      return (
                        <div key={themeKey}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                            <div style={{ width: 4, height: 20, borderRadius: 2, background: th.color }} />
                            <span style={{ fontWeight: 700, fontSize: 15 }}>{th.emoji} {th.name}</span>
                            <Badge variant="default">{filtered.length} positions</Badge>
                            {thSummary && <span style={{ fontSize: 12, color: 'var(--ink-muted)', marginLeft: 4 }}>
                              {fmt$(thSummary.market_value)} · {thSummary.allocation_pct.toFixed(1)}% of portfolio
                            </span>}
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
                            {filtered.map(p => (
                              <PositionCard key={p.ticker} pos={p} onEdit={() => setEditPos(p)} onRemove={() => setRemovePos(p)} onTrade={() => { setTradePos(p); setTradeDollar('500'); setTradeStop('8'); setTradeTarget('25'); tradeMut.reset() }} />
                            ))}
                          </div>
                        </div>
                      )
                    })}
                </div>
              )}

              {/* Table view */}
              {view === 'table' && (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom: '2px solid var(--surface-rule)' }}>
                        {['Ticker','Name','Theme','Price','Entry','Gain %','Gain $','Category','Risk','Conv','Score','Catalyst'].map(h => (
                          <th key={h} style={{ padding: '8px 10px', textAlign: 'left', color: 'var(--ink-muted)', fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em', whiteSpace: 'nowrap' }}>{h}</th>
                        ))}
                        <th style={{ padding: '8px 10px' }} />
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((p, i) => (
                        <tr key={p.ticker} style={{ borderBottom: '1px solid var(--surface-rule)', background: i % 2 === 0 ? 'var(--surface)' : 'var(--surface-soft)' }}>
                          <td style={{ padding: '8px 10px', fontWeight: 700 }}>{p.ticker}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-muted)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</td>
                          <td style={{ padding: '8px 10px' }}>
                            <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 'var(--radius-full)', background: p.theme_color + '22', color: p.theme_color, fontWeight: 600, whiteSpace: 'nowrap' }}>
                              {p.theme_emoji} {p.theme_name}
                            </span>
                          </td>
                          <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)' }}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : '—'}</td>
                          <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: 'var(--ink-muted)' }}>{p.entry_price > 0 ? `$${p.entry_price.toFixed(2)}` : '—'}</td>
                          <td style={{ padding: '8px 10px', fontWeight: 700, color: gainColor(p.gain_pct) }}>{fmtPct(p.gain_pct)}</td>
                          <td style={{ padding: '8px 10px', fontWeight: 600, color: gainColor(p.gain_usd) }}>{fmt$(p.gain_usd)}</td>
                          <td style={{ padding: '8px 10px' }}><Badge variant={categoryVariant(p.category)}>{p.category}</Badge></td>
                          <td style={{ padding: '8px 10px' }}><Badge variant={riskVariant(p.risk_level)}>{p.risk_level}</Badge></td>
                          <td style={{ padding: '8px 10px', fontWeight: 700, color: scoreColor(p.conviction) }}>{p.conviction}</td>
                          <td style={{ padding: '8px 10px', fontWeight: 700, color: scoreColor(p.scores.final_score) }}>{p.scores.final_score.toFixed(1)}</td>
                          <td style={{ padding: '8px 10px', color: 'var(--ink-muted)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.catalyst}</td>
                          <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                            <button onClick={() => setEditPos(p)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)', fontSize: 12, marginRight: 8 }}>Edit</button>
                            <button onClick={() => setRemovePos(p)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--danger)', fontSize: 12 }}>Remove</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
