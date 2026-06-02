import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getPaperStatus } from '@/api/paper'
import type { CandidateRow, PaperAccount } from '@/types'

// ── Constants ─────────────────────────────────────────────────────────────────

const STRATEGY_COLORS: Record<string, string> = {
  algorithm:        '#22d3ee',
  machine_learning: '#a78bfa',
  ml_new:           '#60a5fa',
  combined:         '#34d399',
  pure_ai:          '#fb923c',
  long_hold:        '#f59e0b',
  unified_brain:    '#e879f9',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function mlColor(p: number) {
  if (p >= 0.7) return '#4ade80'
  if (p >= 0.6) return '#facc15'
  return '#f87171'
}
function rrColor(rr: number) {
  if (rr >= 2) return '#4ade80'
  if (rr >= 1.2) return '#facc15'
  return '#f87171'
}

// ── Signal Card ───────────────────────────────────────────────────────────────

interface EnrichedCandidate extends CandidateRow {
  _strategy: string
  _stratLabel: string
  _rr: number | null
  _mlPct: number
}

function SignalCard({ c, onClick }: { c: EnrichedCandidate; onClick: () => void }) {
  const stratColor = STRATEGY_COLORS[c._strategy] ?? '#94a3b8'
  const entry  = Number(c.entry)
  const target = Number(c.target)
  const stop   = Number(c.stop)
  const atr    = Number(c.atr)
  const score  = Number(c.score)
  const llProb = c.large_loss_probability != null ? Number(c.large_loss_probability) : null
  const gateOk = (c.gate_status ?? '') === 'PASS'
  const reason = c.decision_reason ?? c.ai_reason

  const upside = entry > 0 ? ((target - entry) / entry * 100).toFixed(1) : '—'
  const downside = entry > 0 && stop > 0 ? ((stop - entry) / entry * 100).toFixed(1) : '—'

  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--surface-rule)',
        borderLeft: `3px solid ${stratColor}`,
        borderRadius: 8,
        padding: 16,
        cursor: 'pointer',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = stratColor)}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--surface-rule)'; e.currentTarget.style.borderLeftColor = stratColor }}
    >
      {/* Top row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{c.ticker}</span>
          <span style={{ fontSize: 9, color: stratColor, background: `${stratColor}18`, padding: '2px 6px', borderRadius: 3, fontWeight: 700 }}>
            {c._stratLabel}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {c.gate_status && (
            <span style={{
              fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
              background: gateOk ? 'rgba(74,222,128,0.12)' : 'rgba(251,191,36,0.12)',
              color: gateOk ? '#4ade80' : '#fbbf24',
            }}>
              {c.gate_status}
            </span>
          )}
        </div>
      </div>

      {/* Price grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 12 }}>
        {[
          { label: 'Entry',  value: entry > 0 ? `$${entry.toFixed(2)}` : '—',  color: 'var(--ink)' },
          { label: 'Target', value: target > 0 ? `$${target.toFixed(2)}` : '—', color: '#4ade80' },
          { label: 'Stop',   value: stop > 0 ? `$${stop.toFixed(2)}` : '—',   color: '#f87171' },
        ].map(p => (
          <div key={p.label} style={{ background: 'var(--surface-soft)', borderRadius: 6, padding: '8px 10px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 3 }}>{p.label}</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: p.color, fontFamily: 'var(--font-mono)' }}>{p.value}</div>
          </div>
        ))}
      </div>

      {/* Upside / Downside */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        <div style={{ background: 'rgba(74,222,128,0.08)', borderRadius: 6, padding: '6px 10px' }}>
          <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>Upside</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#4ade80', fontFamily: 'var(--font-mono)' }}>+{upside}%</div>
        </div>
        <div style={{ background: 'rgba(248,113,113,0.08)', borderRadius: 6, padding: '6px 10px' }}>
          <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>Downside</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#f87171', fontFamily: 'var(--font-mono)' }}>{downside}%</div>
        </div>
      </div>

      {/* ML + R:R + Score row */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: reason ? 10 : 0 }}>
        {/* ML probability */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>ML Win Prob</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: mlColor(c._mlPct / 100), fontFamily: 'var(--font-mono)' }}>
            {c._mlPct.toFixed(0)}%
          </div>
        </div>

        {/* R:R */}
        {c._rr != null && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>R:R</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: rrColor(c._rr), fontFamily: 'var(--font-mono)' }}>
              {c._rr.toFixed(2)}
            </div>
          </div>
        )}

        {/* Score */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>Alpha Score</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--accent)', fontFamily: 'var(--font-mono)' }}>
            {score.toFixed(0)}
          </div>
        </div>

        {/* ATR */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>ATR</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
            {atr.toFixed(2)}%
          </div>
        </div>

        {/* Large-loss probability */}
        {llProb != null && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--ink-faint)', marginBottom: 2 }}>LL Risk</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: llProb > 0.25 ? '#f87171' : 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
              {(llProb * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </div>

      {/* Why this signal */}
      {reason && (
        <div style={{
          background: 'var(--surface-soft)', borderRadius: 6, padding: '8px 10px',
          borderLeft: `2px solid ${stratColor}`,
          fontSize: 11, color: 'var(--ink-muted)', lineHeight: 1.5,
        }}>
          <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 3 }}>
            Why this signal
          </span>
          {reason}
        </div>
      )}

      {/* Click hint */}
      <div style={{ marginTop: 10, fontSize: 10, color: 'var(--ink-faint)', textAlign: 'right' }}>
        Click to analyze →
      </div>
    </div>
  )
}

// ── Tier badge ────────────────────────────────────────────────────────────────

function TierBadge({ tier, count }: { tier: string; count: number }) {
  const colors: Record<string, string> = { 'A+': '#4ade80', A: '#22d3ee', B: '#a78bfa', C: '#94a3b8' }
  const c = colors[tier] ?? '#94a3b8'
  return (
    <div style={{
      background: 'var(--surface)', border: `1px solid ${c}44`, borderRadius: 8,
      padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10,
    }}>
      <span style={{ fontSize: 20, fontWeight: 900, color: c, fontFamily: 'var(--font-mono)' }}>{tier}</span>
      <div>
        <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--ink)' }}>{count}</div>
        <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>signal{count !== 1 ? 's' : ''}</div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

type SortKey = 'ml' | 'score' | 'rr' | 'upside'
type FilterStrategy = 'all' | string

export default function SignalsPage() {
  const navigate = useNavigate()
  const [sortBy, setSortBy] = useState<SortKey>('score')
  const [filterStrategy, setFilterStrategy] = useState<FilterStrategy>('all')
  const [search, setSearch] = useState('')

  const paperQ = useQuery({
    queryKey: ['paper', 'status'],
    queryFn: getPaperStatus,
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  const allCandidates: EnrichedCandidate[] = useMemo(() => {
    return ((paperQ.data?.accounts ?? []) as PaperAccount[]).flatMap(a =>
      (a.candidates?.rows ?? []).map(r => {
        const entry = Number(r.entry)
        const target = Number(r.target)
        const stop = Number(r.stop)
        const rr = (entry > 0 && entry > stop && stop > 0)
          ? (target - entry) / (entry - stop)
          : null
        const mlPct = r.ml_probability != null ? Number(r.ml_probability) * 100 : 0
        return {
          ...r,
          _strategy: a.strategy,
          _stratLabel: a.label,
          _rr: rr,
          _mlPct: mlPct,
        }
      })
    )
  }, [paperQ.data?.accounts])

  const strategies = useMemo(() => [...new Set(allCandidates.map(c => c._strategy))], [allCandidates])

  const filtered = useMemo(() => {
    let items = allCandidates
    if (filterStrategy !== 'all') items = items.filter(c => c._strategy === filterStrategy)
    if (search) {
      const s = search.toLowerCase()
      items = items.filter(c => c.ticker.toLowerCase().includes(s))
    }
    return [...items].sort((a, b) => {
      if (sortBy === 'ml') return b._mlPct - a._mlPct
      if (sortBy === 'score') return Number(b.score) - Number(a.score)
      if (sortBy === 'rr') return (b._rr ?? 0) - (a._rr ?? 0)
      if (sortBy === 'upside') {
        const ua = Number(a.target) - Number(a.entry)
        const ub = Number(b.target) - Number(b.entry)
        return ub - ua
      }
      return 0
    })
  }, [allCandidates, filterStrategy, search, sortBy])

  // Tier breakdown
  const tierCounts = useMemo(() => {
    const counts: Record<string, number> = { 'A+': 0, A: 0, B: 0, C: 0 }
    allCandidates.forEach(c => {
      const ml = c._mlPct
      const rr = c._rr ?? 0
      if (ml >= 70 && rr >= 2) counts['A+']++
      else if (ml >= 65 && rr >= 1.5) counts.A++
      else if (ml >= 55 && rr >= 1.2) counts.B++
      else counts.C++
    })
    return counts
  }, [allCandidates])

  const isLoading = paperQ.isLoading
  const runnerRunning = paperQ.data?.process?.running ?? false

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
            Live Signals
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-muted)', marginTop: 4 }}>
            Active paper trading candidates with ML scores
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 4,
            background: runnerRunning ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
            color: runnerRunning ? '#4ade80' : '#f87171',
          }}>
            {runnerRunning ? '● LIVE' : '○ STOPPED'}
          </span>
          <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
            {allCandidates.length} signal{allCandidates.length !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* Tier breakdown */}
      {allCandidates.length > 0 && (
        <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
          {(['A+', 'A', 'B', 'C'] as const).map(t => (
            <TierBadge key={t} tier={t} count={tierCounts[t]} />
          ))}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          type="text"
          placeholder="Search ticker…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="input"
          style={{ width: 160 }}
        />
        {strategies.length > 1 && (
          <select
            value={filterStrategy}
            onChange={e => setFilterStrategy(e.target.value)}
            className="input"
            style={{ width: 180 }}
          >
            <option value="all">All Strategies</option>
            {strategies.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
          {([
            { key: 'score', label: 'Score' },
            { key: 'ml',    label: 'ML %' },
            { key: 'rr',    label: 'R:R' },
            { key: 'upside', label: 'Upside' },
          ] as { key: SortKey; label: string }[]).map(s => (
            <button
              key={s.key}
              onClick={() => setSortBy(s.key)}
              style={{
                padding: '4px 10px', borderRadius: 4, border: '1px solid var(--surface-rule)',
                background: sortBy === s.key ? 'var(--accent)' : 'var(--surface-raised)',
                color: sortBy === s.key ? '#fff' : 'var(--ink-muted)',
                fontSize: 11, fontWeight: 600, cursor: 'pointer',
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Cards grid */}
      {isLoading ? (
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13 }}>
          Loading signals…
        </div>
      ) : filtered.length === 0 ? (
        <div style={{
          padding: 48, textAlign: 'center', background: 'var(--surface)',
          border: '1px solid var(--surface-rule)', borderRadius: 8,
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', marginBottom: 8 }}>
            {allCandidates.length === 0
              ? 'No signals today'
              : 'No signals match filters'}
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-faint)', maxWidth: 360, margin: '0 auto' }}>
            {allCandidates.length === 0
              ? runnerRunning
                ? 'The paper runner is active. Candidates appear here after each scan cycle (every 15 min during market hours).'
                : 'Start the paper runner from Paper Trading to begin generating signals.'
              : 'Clear your filters to see all signals.'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
          {filtered.map((c, i) => (
            <SignalCard
              key={`${c.ticker}-${c._strategy}-${i}`}
              c={c}
              onClick={() => navigate(`/analyze?ticker=${encodeURIComponent(c.ticker)}`)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
