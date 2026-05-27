import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { getQuoteDetail, getMarketNews, getMarketChart } from '@/api/market'
import { CandlestickChart } from '@/components/charts/CandlestickChart'
import { Skeleton } from '@/components/ui/Skeleton'
import type { CandidateRow } from '@/types'

interface CandidatePanelProps {
  candidate: CandidateRow | null
  open: boolean
  onClose: () => void
  strategyColor?: string
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

function fmtVol(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K'
  return String(n)
}


export function CandidatePanel({ candidate, open, onClose, strategyColor = '#94a3b8' }: CandidatePanelProps) {
  const [showChart, setShowChart] = useState(false)

  const ticker = candidate?.ticker ?? ''

  // Reset chart when candidate changes
  useEffect(() => {
    setShowChart(false)
  }, [ticker])

  // Escape key handler
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  const quoteQ = useQuery({
    queryKey: ['market', 'quote-detail', ticker],
    queryFn: () => getQuoteDetail(ticker),
    enabled: open && !!ticker,
    staleTime: 60_000,
  })

  const newsQ = useQuery({
    queryKey: ['market', 'news', ticker],
    queryFn: () => getMarketNews(ticker),
    enabled: open && !!ticker,
    staleTime: 900_000,
  })

  const chartQ = useQuery({
    queryKey: ['market', 'chart', ticker, '5d'],
    queryFn: () => getMarketChart(ticker, '5d', '1d'),
    enabled: showChart && !!ticker,
    staleTime: 300_000,
  })

  const q = quoteQ.data
  const entry  = Number(candidate?.entry  ?? 0)
  const target = Number(candidate?.target ?? 0)
  const stop   = Number(candidate?.stop   ?? 0)
  const range  = target - stop
  const stopX   = 0
  const entryX  = range > 0 ? Math.min(100, Math.max(0, ((entry - stop) / range) * 100)) : 50

  const rr = (entry > 0 && entry > stop && target > entry)
    ? ((target - entry) / (entry - stop))
    : null
  const rrStr   = rr != null ? rr.toFixed(2) : '—'
  const rrColor = rr == null ? 'var(--ink-faint)' : rr >= 2 ? '#4ade80' : rr >= 1 ? '#fbbf24' : '#f87171'

  const mlPct = candidate?.ml_probability != null
    ? (Number(candidate.ml_probability) * 100).toFixed(1) + '%'
    : '—'

  const expRet = candidate?.expected_return != null
    ? (Number(candidate.expected_return) >= 0 ? '+' : '') +
      (Number(candidate.expected_return) * 100).toFixed(2) + '%'
    : '—'
  const expRetColor = candidate?.expected_return != null && Number(candidate.expected_return) >= 0
    ? '#4ade80'
    : '#f87171'

  const llp = Number(candidate?.large_loss_probability ?? 0)
  const score = candidate?.score != null ? Math.round(Number(candidate.score)) : null
  const atr = candidate?.atr != null ? Number(candidate.atr).toFixed(2) : '—'
  const tbsp = candidate?.target_before_stop_probability != null
    ? (Number(candidate.target_before_stop_probability) * 100).toFixed(1) + '%'
    : null

  const changePositive = (q?.change ?? 0) >= 0
  const changeColor = changePositive ? '#4ade80' : '#f87171'

  const gateStatus = candidate?.gate_status ?? ''
  const gateBg = gateStatus === 'PASS' ? '#4ade8022' : '#fbbf2422'
  const gateText = gateStatus === 'PASS' ? '#4ade80' : '#fbbf24'

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,.55)',
          zIndex: 1060,
          opacity: open ? 1 : 0,
          pointerEvents: open ? 'auto' : 'none',
          transition: 'opacity .2s',
        }}
      />

      {/* Panel */}
      <div
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          zIndex: 1070,
          width: 'min(560px, calc(100vw - 20px))',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--surface)',
          borderLeft: '1px solid var(--surface-rule)',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.25s ease',
          boxShadow: open ? '-8px 0 40px rgba(0,0,0,.35)' : 'none',
          overflowY: 'auto',
        }}
      >
        {/* ── Header ── */}
        <div
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 10,
            background: 'var(--surface)',
            borderBottom: '1px solid var(--surface-rule)',
            padding: '0 16px',
            height: 56,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexShrink: 0,
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{
                fontSize: 20,
                fontWeight: 700,
                fontFamily: 'var(--font-mono)',
                color: 'var(--ink)',
              }}>
                {ticker}
              </span>
              {candidate?._stratLabel && (
                <span style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: '2px 8px',
                  borderRadius: 999,
                  background: strategyColor + '33',
                  color: strategyColor,
                  border: `1px solid ${strategyColor}55`,
                }}>
                  {candidate._stratLabel}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 1 }}>
              {quoteQ.isLoading
                ? <Skeleton width={160} height={12} />
                : (q?.short_name ?? '')}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--ink-faint)',
              fontSize: 24,
              cursor: 'pointer',
              lineHeight: 1,
              padding: '4px 8px',
              borderRadius: 4,
            }}
          >×</button>
        </div>

        {/* ── Price Strip ── */}
        <div style={{
          background: 'var(--surface-soft)',
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 20,
          flexWrap: 'wrap',
          minHeight: 48,
          flexShrink: 0,
        }}>
          {quoteQ.isLoading ? (
            <>
              <Skeleton width={90} height={22} />
              <Skeleton width={70} height={16} />
              <Skeleton width={140} height={14} />
            </>
          ) : (
            <>
              <span style={{ fontSize: 22, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>
                {q?.price != null ? `$${q.price.toFixed(2)}` : '—'}
              </span>
              <span style={{ fontSize: 14, fontWeight: 600, color: changeColor, fontVariantNumeric: 'tabular-nums' }}>
                {q?.change != null
                  ? `${q.change >= 0 ? '+' : ''}${q.change.toFixed(2)} (${q.change_pct != null ? (q.change_pct >= 0 ? '+' : '') + q.change_pct.toFixed(2) + '%' : ''})`
                  : ''}
              </span>
              <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
                {q?.day_low != null && q?.day_high != null
                  ? `L $${q.day_low.toFixed(2)} — H $${q.day_high.toFixed(2)}`
                  : ''}
              </span>
              <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
                {q?.volume != null ? `Vol ${fmtVol(q.volume)}` : ''}
              </span>
            </>
          )}
        </div>

        {/* ── Trade Setup Card ── */}
        <div style={{
          background: 'var(--surface-raised, var(--surface))',
          borderRadius: 8,
          padding: 16,
          margin: 16,
          border: '1px solid var(--surface-rule)',
          flexShrink: 0,
        }}>
          {/* Price level bar */}
          <div style={{ marginBottom: 20 }}>
            {/* Track */}
            <div style={{
              height: 6,
              borderRadius: 3,
              background: `linear-gradient(to right,
                #f87171 0%,
                #f87171 ${stopX}%,
                var(--surface-rule) ${stopX}%,
                var(--surface-rule) ${entryX}%,
                #4ade8044 ${entryX}%,
                #4ade80 100%)`,
              marginBottom: 8,
              position: 'relative',
            }}>
              {/* Entry pin on track */}
              <div style={{
                position: 'absolute',
                left: `${entryX}%`,
                top: -3,
                width: 12,
                height: 12,
                borderRadius: '50%',
                background: 'var(--accent, #fb923c)',
                border: '2px solid var(--surface)',
                transform: 'translateX(-50%)',
              }} />
            </div>
            {/* Labels */}
            <div style={{ position: 'relative', height: 28 }}>
              {/* STOP label — left */}
              <div style={{
                position: 'absolute',
                left: 0,
                top: 0,
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
                color: '#f87171',
                textAlign: 'left',
              }}>
                <div style={{ fontSize: 9, opacity: 0.7, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Stop</div>
                <div>${stop.toFixed(2)}</div>
              </div>
              {/* ENTRY label — centered at entryX */}
              <div style={{
                position: 'absolute',
                left: `${entryX}%`,
                top: 0,
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
                color: 'var(--accent, #fb923c)',
                textAlign: 'center',
                transform: 'translateX(-50%)',
              }}>
                <div style={{ fontSize: 9, opacity: 0.7, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Entry</div>
                <div>${entry.toFixed(2)}</div>
              </div>
              {/* TARGET label — right */}
              <div style={{
                position: 'absolute',
                right: 0,
                top: 0,
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
                color: '#4ade80',
                textAlign: 'right',
              }}>
                <div style={{ fontSize: 9, opacity: 0.7, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Target</div>
                <div>${target.toFixed(2)}</div>
              </div>
            </div>
          </div>

          {/* Stat chips */}
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{
              flex: 1,
              background: 'var(--canvas)',
              borderRadius: 6,
              padding: '8px 10px',
              border: '1px solid var(--surface-rule)',
            }}>
              <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>R:R</div>
              <div style={{ fontSize: 15, fontWeight: 700, fontFamily: 'var(--font-mono)', color: rrColor }}>{rrStr}</div>
            </div>
            <div style={{
              flex: 1,
              background: 'var(--canvas)',
              borderRadius: 6,
              padding: '8px 10px',
              border: '1px solid var(--surface-rule)',
            }}>
              <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>ML%</div>
              <div style={{ fontSize: 15, fontWeight: 700, fontFamily: 'var(--font-mono)', color: '#67e8f9' }}>{mlPct}</div>
            </div>
            <div style={{
              flex: 1,
              background: 'var(--canvas)',
              borderRadius: 6,
              padding: '8px 10px',
              border: '1px solid var(--surface-rule)',
            }}>
              <div style={{ fontSize: 9, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 3 }}>E.Return</div>
              <div style={{ fontSize: 15, fontWeight: 700, fontFamily: 'var(--font-mono)', color: expRetColor }}>{expRet}</div>
            </div>
          </div>
        </div>

        {/* ── Risk Metrics Row ── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 10,
          padding: '0 16px',
          flexShrink: 0,
        }}>
          {[
            { label: 'Score', value: score != null ? `${score}/100` : '—', color: 'var(--ink)' },
            {
              label: 'Large Loss%',
              value: candidate?.large_loss_probability != null ? (llp * 100).toFixed(1) + '%' : '—',
              color: llp > 0.3 ? '#f87171' : 'var(--ink)',
            },
            { label: 'ATR', value: atr, color: 'var(--ink)' },
            { label: 'Target→Stop Prob', value: tbsp ?? '—', color: 'var(--ink)' },
          ].map(({ label, value, color }) => (
            <div key={label} style={{
              background: 'var(--canvas)',
              border: '1px solid var(--surface-rule)',
              borderRadius: 6,
              padding: '10px 12px',
            }}>
              <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--ink-faint)', marginBottom: 4 }}>
                {label}
              </div>
              <div style={{ fontSize: 17, fontWeight: 700, fontFamily: 'var(--font-mono)', color }}>
                {value}
              </div>
            </div>
          ))}
        </div>

        {/* ── Gate & Reasoning ── */}
        <div style={{
          background: 'var(--canvas)',
          borderRadius: 6,
          padding: 12,
          margin: '16px 16px 0',
          border: '1px solid var(--surface-rule)',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span style={{
              fontSize: 11,
              fontWeight: 700,
              padding: '2px 8px',
              borderRadius: 4,
              background: gateBg,
              color: gateText,
            }}>
              {gateStatus || 'UNKNOWN'}
            </span>
            {q?.sector && (
              <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                {q.sector}{q.industry ? ` · ${q.industry}` : ''}
              </span>
            )}
          </div>
          {candidate?.decision_reason && (
            <div style={{ fontSize: 12, color: 'var(--ink-muted)', lineHeight: 1.6 }}>
              {candidate.decision_reason}
            </div>
          )}
          {candidate?.ai_reason && (
            <details style={{ marginTop: 10 }}>
              <summary style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--ink-faint)',
                cursor: 'pointer',
                userSelect: 'none',
                listStyle: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}>
                ▶ AI Reasoning
              </summary>
              <div style={{
                marginTop: 8,
                fontSize: 11,
                color: 'var(--ink-muted)',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                fontFamily: 'var(--font-mono)',
                background: 'var(--surface)',
                borderRadius: 4,
                padding: '8px 10px',
                border: '1px solid var(--surface-rule)',
              }}>
                {candidate.ai_reason}
              </div>
            </details>
          )}
        </div>

        {/* ── Action Buttons ── */}
        <div style={{
          display: 'flex',
          gap: 8,
          padding: '16px 16px 0',
          flexWrap: 'wrap',
          flexShrink: 0,
        }}>
          <a
            href={`https://www.tradingview.com/chart/?symbol=${ticker}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              padding: '7px 14px',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 6,
              background: 'var(--surface-soft)',
              border: '1px solid var(--surface-rule)',
              color: 'var(--ink)',
              textDecoration: 'none',
              cursor: 'pointer',
            }}
          >
            TradingView ↗
          </a>
          <a
            href={`https://finance.yahoo.com/quote/${ticker}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              padding: '7px 14px',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 6,
              background: 'var(--surface-soft)',
              border: '1px solid var(--surface-rule)',
              color: 'var(--ink)',
              textDecoration: 'none',
              cursor: 'pointer',
            }}
          >
            Yahoo Finance ↗
          </a>
          <button
            onClick={() => setShowChart(v => !v)}
            style={{
              padding: '7px 14px',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 6,
              background: showChart ? 'var(--accent, #fb923c)' : 'var(--surface-soft)',
              border: `1px solid ${showChart ? 'var(--accent, #fb923c)' : 'var(--surface-rule)'}`,
              color: showChart ? '#fff' : 'var(--ink)',
              cursor: 'pointer',
            }}
          >
            5d Chart
          </button>
        </div>

        {/* ── Mini Chart ── */}
        {showChart && (
          <div style={{ padding: '12px 16px 0', flexShrink: 0 }}>
            {chartQ.isLoading ? (
              <Skeleton height={180} borderRadius={6} />
            ) : chartQ.data ? (
              <div style={{ borderRadius: 6, overflow: 'hidden', border: '1px solid var(--surface-rule)' }}>
                <CandlestickChart data={chartQ.data} height={180} showVolume={false} />
              </div>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', padding: '20px 0', textAlign: 'center' }}>
                Chart unavailable
              </div>
            )}
          </div>
        )}

        {/* ── News Section ── */}
        <div style={{ padding: '16px 16px 24px', flexShrink: 0 }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 12,
          }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink)' }}>Recent News</span>
            {newsQ.data?.news?.length ? (
              <span style={{
                fontSize: 10,
                fontWeight: 600,
                padding: '1px 6px',
                borderRadius: 999,
                background: 'var(--surface-soft)',
                color: 'var(--ink-faint)',
                border: '1px solid var(--surface-rule)',
              }}>
                {newsQ.data.news.length}
              </span>
            ) : null}
          </div>

          {newsQ.isLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{ padding: '10px 0', borderBottom: '1px solid var(--surface-rule)' }}>
                  <Skeleton width="40%" height={10} />
                  <div style={{ height: 6 }} />
                  <Skeleton width="90%" height={13} />
                  <div style={{ height: 4 }} />
                  <Skeleton width="70%" height={11} />
                </div>
              ))}
            </div>
          ) : !newsQ.data?.news?.length ? (
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', padding: '12px 0' }}>
              No recent news available.
            </div>
          ) : (
            <div>
              {newsQ.data.news.map((item, i) => (
                <a
                  key={i}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: 'block',
                    padding: '10px 8px',
                    borderBottom: i < newsQ.data!.news.length - 1 ? '1px solid var(--surface-rule)' : 'none',
                    textDecoration: 'none',
                    borderRadius: 4,
                    transition: 'background .12s',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--surface-raised, var(--surface-soft))' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                >
                  <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 4 }}>
                    {item.source && <span>{item.source}</span>}
                    {item.source && item.published && <span> · </span>}
                    {item.published && <span>{relativeTime(item.published)}</span>}
                  </div>
                  <div style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: 'var(--ink)',
                    lineHeight: 1.4,
                    marginBottom: 3,
                  }}>
                    {item.title}
                  </div>
                  {item.summary && (
                    <div style={{
                      fontSize: 11,
                      color: 'var(--ink-faint)',
                      lineHeight: 1.5,
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical' as const,
                      overflow: 'hidden',
                    }}>
                      {item.summary}
                    </div>
                  )}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}
