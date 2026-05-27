/**
 * HIL Toast — bottom-right notification for pending Human-in-the-Loop approvals.
 * Polls /api/paper/hil/pending every 30 seconds.
 */
import { useState, useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'

interface HilPending {
  id: string
  ticker: string
  action: 'BUY' | 'SELL'
  shares: number
  price: number
  reason?: string
  strategy?: string
}

const toast: React.CSSProperties = {
  position: 'fixed', right: 18, bottom: 18, zIndex: 45,
  background: 'var(--surface)', border: '1px solid var(--surface-rule)',
  borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,.18)',
  maxWidth: 380, width: 'min(380px, calc(100vw - 36px))',
  overflow: 'hidden', pointerEvents: 'auto',
}
const btnPrimary: React.CSSProperties = {
  flex: 1, padding: 10, background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 6, fontWeight: 600, fontSize: 13, cursor: 'pointer',
}
const btnSecondary: React.CSSProperties = {
  flex: 1, padding: 10, background: 'var(--surface-raised)', color: 'var(--ink)',
  border: '1px solid var(--surface-rule)', borderRadius: 6, fontWeight: 600, fontSize: 13, cursor: 'pointer',
}

export function HilToast() {
  const qc = useQueryClient()
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [visible, setVisible] = useState(false)
  const tradeRef = useRef<HilPending | null>(null)

  const { data } = useQuery<{ pending: boolean; trade?: HilPending }>({
    queryKey: ['hil-pending-toast'],
    queryFn: () => api.get('/paper/hil/pending').then(r => r.data),
    refetchInterval: 30_000,
    retry: false,
  })

  useEffect(() => {
    if (data?.pending && data.trade) {
      tradeRef.current = data.trade
      if (dismissed === data.trade.id) {
        setVisible(false)
      } else {
        setVisible(true)
      }
    } else {
      tradeRef.current = null
      setDismissed(null)
      setVisible(false)
    }
  }, [data, dismissed])

  const resolve = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'reject' }) =>
      api.post('/paper/hil/resolve', { id, action }),
    onSuccess: () => {
      setVisible(false)
      qc.invalidateQueries({ queryKey: ['hil-pending-toast'] })
      qc.invalidateQueries({ queryKey: ['hil-pending'] })
    },
  })

  if (!visible || !tradeRef.current) return null
  const t = tradeRef.current

  return (
    <div style={toast} role="dialog" aria-modal={false as unknown as boolean} aria-live="polite">
      <div style={{ padding: '18px 22px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#F59E0B', flexShrink: 0, animation: 'blink 1.2s ease-in-out infinite' }} />
        <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', margin: 0, flex: 1 }}>Trade Requires Approval</h3>
        <button
          type="button"
          aria-label="Dismiss for now"
          onClick={() => { setDismissed(t.id); setVisible(false) }}
          style={{ background: 'none', border: 'none', color: 'var(--ink-faint)', fontSize: 18, lineHeight: 1, cursor: 'pointer', minWidth: 36, minHeight: 36 }}
        >×</button>
      </div>
      <div style={{ padding: '20px 22px' }}>
        <p style={{ fontSize: 13, color: 'var(--ink-muted)', margin: '0 0 16px', lineHeight: 1.5 }}>
          A trade signal was generated and needs your authorization before execution.
        </p>
        <div style={{ background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16, marginBottom: 18 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', fontWeight: 600, marginBottom: 3 }}>Ticker</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--accent)', fontFamily: 'monospace', letterSpacing: '.02em' }}>{t.ticker}</div>
            </div>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', fontWeight: 600, marginBottom: 3 }}>Action</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: t.action === 'BUY' ? '#10b981' : '#ef4444' }}>{t.action}</div>
            </div>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', fontWeight: 600, marginBottom: 3 }}>Shares</div>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>{t.shares}</div>
            </div>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', fontWeight: 600, marginBottom: 3 }}>Limit Price</div>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>${parseFloat(String(t.price)).toFixed(2)}</div>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button style={btnSecondary} onClick={() => { setDismissed(t.id); setVisible(false) }}>Later</button>
          <button style={{ ...btnSecondary }} onClick={() => resolve.mutate({ id: t.id, action: 'reject' })} disabled={resolve.isPending}>Reject</button>
          <button style={btnPrimary} onClick={() => resolve.mutate({ id: t.id, action: 'approve' })} disabled={resolve.isPending}>
            {resolve.isPending ? '…' : 'Approve Trade'}
          </button>
        </div>
      </div>
      <style>{`@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }`}</style>
    </div>
  )
}
