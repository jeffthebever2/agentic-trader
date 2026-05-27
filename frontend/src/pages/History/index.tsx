import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
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

function decisionColor(decision: string | null | undefined): string {
  const d = (decision ?? '').toLowerCase()
  if (d === 'buy' || d === 'overweight') return '#4ade80'
  if (d === 'hold') return '#fbbf24'
  if (d === 'underweight' || d === 'sell') return '#f87171'
  return 'var(--ink-muted)'
}

function DecisionBadge({ decision }: { decision: string | null | undefined }) {
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

// ── Virtual Table ─────────────────────────────────────────────────────────────

interface VirtualTableProps {
  items: HistoryItem[]
  expandedIds: Set<string | number>
  onToggleExpand: (id: string | number) => void
}

function VirtualTable({ items, expandedIds, onToggleExpand }: VirtualTableProps) {
  const parentRef = useRef<HTMLDivElement>(null)

  // Build a flat list of rows: each item → 1 or 2 rows (main + optional expanded)
  type FlatRow =
    | { type: 'item'; item: HistoryItem }
    | { type: 'summary'; item: HistoryItem }

  const flatRows: FlatRow[] = []
  for (const item of items) {
    flatRows.push({ type: 'item', item })
    if (expandedIds.has(item.id)) {
      flatRows.push({ type: 'summary', item })
    }
  }

  const rowVirtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => {
      const row = flatRows[i]
      if (row.type === 'summary') return 60
      return 44
    },
    overscan: 5,
  })

  return (
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
        <tr style={{ padding: 0, margin: 0 }}>
          <td colSpan={5} style={{ padding: 0 }}>
            <div
              ref={parentRef}
              style={{ maxHeight: 'calc(100vh - 340px)', overflowY: 'auto' }}
            >
              <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
                {rowVirtualizer.getVirtualItems().map(virtualRow => {
                  const flatRow = flatRows[virtualRow.index]
                  const { item } = flatRow

                  if (flatRow.type === 'summary') {
                    return (
                      <div
                        key={`${item.id}-summary`}
                        data-index={virtualRow.index}
                        ref={rowVirtualizer.measureElement}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          transform: `translateY(${virtualRow.start}px)`,
                        }}
                      >
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                          <tbody>
                            <tr>
                              <td colSpan={5} style={{
                                padding: '10px 16px 14px 40px',
                                background: 'var(--canvas)',
                                borderBottom: '1px solid var(--surface-rule)',
                                fontSize: 12,
                                color: 'var(--ink-muted)',
                                lineHeight: 1.6,
                              }}>
                                {item.summary || <em style={{ color: 'var(--ink-faint)' }}>No summary available.</em>}
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    )
                  }

                  const expanded = expandedIds.has(item.id)
                  return (
                    <div
                      key={item.id}
                      data-index={virtualRow.index}
                      ref={rowVirtualizer.measureElement}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                    >
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <tbody>
                          <tr
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
                            <td style={{ padding: '10px 16px', textAlign: 'center', width: 40 }}>
                              {item.summary ? (
                                <button
                                  onClick={() => onToggleExpand(item.id)}
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
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )
                })}
              </div>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // URL-derived filter values
  const ticker    = searchParams.get('ticker')    ?? ''
  const decision  = searchParams.get('decision')  ?? ''
  const date_from = searchParams.get('date_from') ?? ''
  const date_to   = searchParams.get('date_to')   ?? ''
  const page      = parseInt(searchParams.get('page') ?? '1', 10)

  // Local controlled input state (initialized from URL)
  const [tickerInput,   setTickerInput]   = useState(ticker)
  const [decisionInput, setDecisionInput] = useState(decision)
  const [dateFrom,      setDateFrom]      = useState(date_from)
  const [dateTo,        setDateTo]        = useState(date_to)

  const [expandedIds, setExpandedIds] = useState<Set<string | number>>(new Set())

  const PER_PAGE = 25

  const { data, isLoading, isError } = useQuery<HistoryResponse>({
    queryKey: ['history', ticker, decision, date_from, date_to, page],
    queryFn: () =>
      api.get<HistoryResponse>('/history', {
        params: {
          ticker:    ticker    || undefined,
          decision:  decision  || undefined,
          date_from: date_from || undefined,
          date_to:   date_to   || undefined,
          page,
          per_page: PER_PAGE,
        },
      }).then(r => r.data),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  })

  function applyFilters() {
    setSearchParams(p => {
      p.set('ticker',    tickerInput.trim().toUpperCase())
      p.set('decision',  decisionInput)
      p.set('date_from', dateFrom)
      p.set('date_to',   dateTo)
      p.set('page',      '1')
      return p
    })
  }

  function clearFilters() {
    setTickerInput('')
    setDecisionInput('')
    setDateFrom('')
    setDateTo('')
    setSearchParams({})
  }

  function downloadCsv(items: HistoryItem[]) {
    const header = 'Ticker,Date,Decision,Summary'
    const rows = items.map(it => {
      const summary = (it.summary ?? '').replace(/"/g, '""')
      return `${it.ticker},${it.date},${it.decision},"${summary}"`
    })
    const csv = [header, ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `analysis-history-${Date.now()}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  function setPage(newPage: number) {
    setSearchParams(p => {
      p.set('page', String(newPage))
      return p
    })
  }

  function toggleExpand(id: string | number) {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const items        = data?.entries    ?? []
  const totalRecords = data?.total      ?? 0
  const totalPages   = data?.pages      ?? 1

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

        <input
          type="date"
          value={dateFrom}
          onChange={e => setDateFrom(e.target.value)}
          style={{
            background: 'var(--canvas)', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 10px', fontSize: 13,
            color: 'var(--ink)', outline: 'none',
          }}
        />

        <input
          type="date"
          value={dateTo}
          onChange={e => setDateTo(e.target.value)}
          style={{
            background: 'var(--canvas)', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 10px', fontSize: 13,
            color: 'var(--ink)', outline: 'none',
          }}
        />

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

        <button
          onClick={() => downloadCsv(items)}
          disabled={items.length === 0}
          style={{
            background: 'transparent', border: '1px solid var(--surface-rule)',
            borderRadius: 6, padding: '6px 14px', fontSize: 13,
            color: items.length === 0 ? 'var(--ink-faint)' : 'var(--ink-muted)',
            cursor: items.length === 0 ? 'default' : 'pointer',
          }}
        >
          Export CSV
        </button>
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
          <VirtualTable
            items={items}
            expandedIds={expandedIds}
            onToggleExpand={toggleExpand}
          />
        )}
      </div>

      {/* Pagination */}
      {!isLoading && !isError && totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12,
        }}>
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
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
            onClick={() => setPage(Math.min(totalPages, page + 1))}
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
