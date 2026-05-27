import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '@/api/client'
import { LoadingState, ErrorState, EmptyState } from '@/components/shared/LoadingState'

// ── Types ─────────────────────────────────────────────────────────────────────

interface HistoryItem {
  id: number | string
  ticker: string
  date: string
  decision: string
  summary: string
}

interface HistoryResponse {
  entries: HistoryItem[]
  total: number
  page: number
  pages: number
}

interface StatsResponse {
  total: number
  tickers: number
  by_decision: Record<string, number>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function decisionColor(decision: string): string {
  const d = decision.toLowerCase()
  if (d === 'buy' || d === 'overweight') return '#4ade80'
  if (d === 'hold') return '#fbbf24'
  if (d === 'underweight' || d === 'sell') return '#f87171'
  return 'var(--ink-muted)'
}

function DecisionBadge({ decision }: { decision: string }) {
  const color = decisionColor(decision)
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: '0.04em',
      color: '#0a0a0a',
      background: color,
      textTransform: 'uppercase',
    }}>
      {decision}
    </span>
  )
}

// ── Stats Row ─────────────────────────────────────────────────────────────────

function StatsRow() {
  const { data, isLoading, isError } = useQuery<StatsResponse>({
    queryKey: ['history-stats'],
    queryFn: () => api.get<StatsResponse>('/history/stats').then(r => r.data),
    staleTime: 30_000,
  })

  if (isLoading) return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
      {[0, 1, 2].map(i => (
        <div key={i} style={{
          flex: 1, background: 'var(--surface)', border: '1px solid var(--surface-rule)',
          borderRadius: 8, padding: '14px 18px', minHeight: 68,
        }} />
      ))}
    </div>
  )

  if (isError || !data) return null

  const breakdownEntries = Object.entries(data.by_decision ?? {})

  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
      {/* Total */}
      <div style={{
        flex: '1 1 140px', background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 8, padding: '14px 18px',
      }}>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
          Total Analyses
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)' }}>{data.total.toLocaleString()}</div>
      </div>

      {/* Tickers */}
      <div style={{
        flex: '1 1 140px', background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 8, padding: '14px 18px',
      }}>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
          Tickers Covered
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)' }}>{data.tickers.toLocaleString()}</div>
      </div>

      {/* Breakdown */}
      <div style={{
        flex: '2 1 260px', background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 8, padding: '14px 18px',
      }}>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
          Decision Breakdown
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {breakdownEntries.length === 0
            ? <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No data</span>
            : breakdownEntries.map(([dec, count]) => (
              <span key={dec} style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                background: 'var(--canvas)', border: '1px solid var(--surface-rule)',
                borderRadius: 4, padding: '2px 8px', fontSize: 12,
              }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: decisionColor(dec), display: 'inline-block',
                }} />
                <span style={{ color: 'var(--ink-muted)', textTransform: 'capitalize' }}>{dec}</span>
                <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{count}</span>
              </span>
            ))
          }
        </div>
      </div>
    </div>
  )
}

// ── Expanded Summary Row ──────────────────────────────────────────────────────

function SummaryRow({ summary }: { summary: string }) {
  return (
    <tr>
      <td colSpan={5} style={{
        padding: '10px 16px 14px 40px',
        background: 'var(--canvas)',
        borderBottom: '1px solid var(--surface-rule)',
        fontSize: 12,
        color: 'var(--ink-muted)',
        lineHeight: 1.6,
      }}>
        {summary || <em style={{ color: 'var(--ink-faint)' }}>No summary available.</em>}
      </td>
    </tr>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function HistoryPage() {
  const [tickerInput, setTickerInput] = useState('')
  const [decisionInput, setDecisionInput] = useState('')
  const [appliedTicker, setAppliedTicker] = useState('')
  const [appliedDecision, setAppliedDecision] = useState('')
  const [page, setPage] = useState(1)
  const [expandedIds, setExpandedIds] = useState<Set<string | number>>(new Set())

  const PER_PAGE = 25

  const { data, isLoading, isError } = useQuery<HistoryResponse>({
    queryKey: ['history', appliedTicker, appliedDecision, page],
    queryFn: () =>
      api.get<HistoryResponse>('/history', {
        params: {
          ticker: appliedTicker || undefined,
          decision: appliedDecision || undefined,
          page,
          per_page: PER_PAGE,
        },
      }).then(r => r.data),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  })

  function applyFilters() {
    setPage(1)
    setAppliedTicker(tickerInput.trim().toUpperCase())
    setAppliedDecision(decisionInput)
  }

  function clearFilters() {
    setTickerInput('')
    setDecisionInput('')
    setAppliedTicker('')
    setAppliedDecision('')
    setPage(1)
  }

  function toggleExpand(id: string | number) {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const items = data?.entries ?? []
  const totalRecords = data?.total ?? 0
  const totalPages = data?.pages ?? 1

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--ink)', marginBottom: 4 }}>Analysis History</div>
        <div style={{ fontSize: 13, color: 'var(--ink-faint)' }}>Browse all past AI-generated trade analyses.</div>
      </div>

      {/* Stats */}
      <StatsRow />

      {/* Filter bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
        background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 8, padding: '12px 16px', marginBottom: 16,
      }}>
        <input
          type="text"
          placeholder="Ticker (e.g. AAPL)"
          value={tickerInput}
          onChange={e => setTickerInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyFilters()}
          style={{
            background: 'var(--canvas)', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 10px', fontSize: 13,
            color: 'var(--ink)', outline: 'none', width: 160,
          }}
        />

        <select
          value={decisionInput}
          onChange={e => setDecisionInput(e.target.value)}
          style={{
            background: 'var(--canvas)', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 10px', fontSize: 13,
            color: 'var(--ink)', outline: 'none', cursor: 'pointer',
          }}
        >
          <option value="">All Decisions</option>
          <option value="Buy">Buy</option>
          <option value="Overweight">Overweight</option>
          <option value="Hold">Hold</option>
          <option value="Underweight">Underweight</option>
          <option value="Sell">Sell</option>
        </select>

        <button
          onClick={applyFilters}
          style={{
            background: 'var(--accent)', border: 'none', borderRadius: 6,
            padding: '6px 16px', fontSize: 13, fontWeight: 600,
            color: '#fff', cursor: 'pointer',
          }}
        >
          Filter
        </button>

        <button
          onClick={clearFilters}
          style={{
            background: 'transparent', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 14px', fontSize: 13,
            color: 'var(--ink-muted)', cursor: 'pointer',
          }}
        >
          Clear
        </button>

        {!isLoading && (
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-faint)' }}>
            {totalRecords.toLocaleString()} result{totalRecords !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Table */}
      <div style={{
        background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 8, overflow: 'hidden', marginBottom: 16,
      }}>
        {isLoading ? (
          <LoadingState message="Loading history…" />
        ) : isError ? (
          <ErrorState message="Failed to load history." />
        ) : items.length === 0 ? (
          <EmptyState message="No analyses found." />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--canvas)', borderBottom: '1px solid var(--surface-rule)' }}>
                {(['Ticker', 'Date', 'Decision', 'Summary', ''] as const).map((col, i) => (
                  <th key={i} style={{
                    padding: '10px 16px', textAlign: 'left', fontSize: 11,
                    fontWeight: 700, color: 'var(--ink-faint)',
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                    whiteSpace: 'nowrap',
                    width: col === '' ? 40 : col === 'Summary' ? '100%' : 'auto',
                  }}>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(item => {
                const expanded = expandedIds.has(item.id)
                return [
                  <tr
                    key={item.id}
                    style={{
                      borderBottom: expanded ? 'none' : '1px solid var(--surface-rule)',
                      transition: 'background 0.1s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--canvas)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '10px 16px', fontSize: 13, fontWeight: 700, color: 'var(--ink)', whiteSpace: 'nowrap' }}>
                      {item.ticker}
                    </td>
                    <td style={{ padding: '10px 16px', fontSize: 12, color: 'var(--ink-muted)', whiteSpace: 'nowrap' }}>
                      {item.date}
                    </td>
                    <td style={{ padding: '10px 16px', whiteSpace: 'nowrap' }}>
                      <DecisionBadge decision={item.decision} />
                    </td>
                    <td style={{
                      padding: '10px 16px', fontSize: 12, color: 'var(--ink-muted)',
                      maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {item.summary || <span style={{ color: 'var(--ink-faint)', fontStyle: 'italic' }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 16px', textAlign: 'center' }}>
                      {item.summary ? (
                        <button
                          onClick={() => toggleExpand(item.id)}
                          title={expanded ? 'Collapse' : 'Expand'}
                          style={{
                            background: 'transparent', border: '1px solid var(--surface-rule)',
                            borderRadius: 4, width: 26, height: 22, cursor: 'pointer',
                            color: 'var(--ink-muted)', fontSize: 12, lineHeight: 1,
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                          }}
                        >
                          {expanded ? '▲' : '▼'}
                        </button>
                      ) : null}
                    </td>
                  </tr>,
                  expanded ? <SummaryRow key={`${item.id}-summary`} summary={item.summary} /> : null,
                ]
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && !isError && totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12,
        }}>
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
            style={{
              background: 'var(--surface)', border: '1px solid var(--surface-rule)',
              borderRadius: 6, padding: '6px 14px', fontSize: 13,
              color: page <= 1 ? 'var(--ink-faint)' : 'var(--ink)',
              cursor: page <= 1 ? 'default' : 'pointer',
            }}
          >
            ← Prev
          </button>

          <span style={{ fontSize: 13, color: 'var(--ink-muted)' }}>
            Page {page} of {totalPages}
          </span>

          <button
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            style={{
              background: 'var(--surface)', border: '1px solid var(--surface-rule)',
              borderRadius: 6, padding: '6px 14px', fontSize: 13,
              color: page >= totalPages ? 'var(--ink-faint)' : 'var(--ink)',
              cursor: page >= totalPages ? 'default' : 'pointer',
            }}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
