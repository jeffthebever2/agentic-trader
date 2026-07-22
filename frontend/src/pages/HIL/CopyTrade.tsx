import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { openStepUp, stepUpHeaders } from '@/components/modals/StepUpModal'
import { Button, EmptyState } from '@/components/ui'

// ── Types ─────────────────────────────────────────────────────────────────────

interface CopySettings {
  enabled: boolean
  follow_portfolio_id: string | null
  mode: 'hil' | 'auto'
  account: string | null
  stop_pct: number
  target_pct: number
  min_weight: number
  max_weight: number
  max_new_buys_per_sync: number
  owned: Record<string, unknown>
  last_sync: string | null
  last_error: string | null
  last_actions: CopyAction[]
  pending_count: number
  autonomous_unlocked: boolean
  live_trading_enabled: boolean
}

interface FollowablePortfolio {
  portfolio_id: string
  name: string
  source_strategy: string
  all_time_ror: number
  current_equity: number
  open_positions: number
  win_rate: number
  total_trades: number
}

interface CopyAction {
  id?: string
  action: 'buy' | 'sell'
  ticker: string
  target_pct: number
  paper_weight_raw?: number
  reason: string
  status?: string
  created_at?: string
}

const SLOW_MS = 180_000

// ── Styles (mirror HIL page vocabulary) ─────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 10,
  padding: '18px 20px',
  marginBottom: 16,
}
const sectionTitle: React.CSSProperties = {
  fontSize: 14, fontWeight: 700, color: 'var(--ink)', marginBottom: 14,
  paddingBottom: 10, borderBottom: '1px solid var(--surface-rule)',
  display: 'flex', alignItems: 'center', gap: 7,
}
const label: React.CSSProperties = {
  display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--ink-muted)',
  marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em',
}
const input: React.CSSProperties = {
  width: '100%', padding: '8px 10px', fontSize: 13, borderRadius: 7,
  border: '1px solid var(--surface-rule)', background: 'var(--surface-raised)',
  color: 'var(--ink)',
}
const badge = (color: 'green' | 'amber' | 'grey' | 'red'): React.CSSProperties => {
  const map = {
    green: ['rgba(34,197,94,0.14)', '#16a34a'],
    amber: ['rgba(245,158,11,0.14)', '#d97706'],
    grey: ['var(--surface-soft)', 'var(--ink-muted)'],
    red: ['rgba(239,68,68,0.14)', '#dc2626'],
  }[color]
  return {
    display: 'inline-block', padding: '2px 9px', fontSize: 11, fontWeight: 600,
    borderRadius: 999, background: map[0], color: map[1],
  }
}

// ── Component ───────────────────────────────────────────────────────────────────

export default function CopyTrade({ disclosureAccepted }: { disclosureAccepted: boolean }) {
  const qc = useQueryClient()
  const [override, setOverride] = useState<Partial<CopySettings> | null>(null)
  const [msg, setMsg] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)

  const { data: settings, refetch: refetchSettings } = useQuery<CopySettings>({
    queryKey: ['copytrade-settings'],
    queryFn: () => api.get<CopySettings>('/copytrade/settings').then(r => r.data),
    refetchInterval: 30_000,
  })
  const { data: portfoliosRaw } = useQuery<{ portfolios: FollowablePortfolio[] }>({
    queryKey: ['copytrade-portfolios'],
    queryFn: () => api.get('/copytrade/portfolios').then(r => r.data),
    staleTime: 60_000,
  })
  const { data: pendingRaw, refetch: refetchPending } = useQuery<{ pending: CopyAction[] }>({
    queryKey: ['copytrade-pending'],
    queryFn: () => api.get('/copytrade/pending').then(r => r.data),
    refetchInterval: 15_000,
  })

  const s: CopySettings = { ...(settings ?? defaultSettings()), ...(override ?? {}) }
  const portfolios = portfoliosRaw?.portfolios ?? []
  const pending = pendingRaw?.pending ?? []

  function patch(p: Partial<CopySettings>) { setOverride({ ...(override ?? {}), ...p }) }

  const saveMut = useMutation({
    mutationFn: (body: Partial<CopySettings>) =>
      api.post('/copytrade/settings', body).then(r => r.data),
    onSuccess: () => {
      setMsg('Saved.'); setOverride(null)
      qc.invalidateQueries({ queryKey: ['copytrade-settings'] })
      setTimeout(() => setMsg(''), 3000)
    },
    onError: (e: unknown) => { setMsg((e as Error)?.message || 'Save failed.'); setTimeout(() => setMsg(''), 4000) },
  })

  const syncMut = useMutation({
    mutationFn: () => api.post('/copytrade/sync', {}, { timeout: SLOW_MS }).then(r => r.data),
    onSuccess: (d: { queued?: number; fills?: unknown[]; note?: string }) => {
      const n = d.queued ?? d.fills?.length ?? 0
      setMsg(d.note === 'in sync' ? 'In sync — nothing to do.' : `Reconcile: ${n} action(s).`)
      refetchSettings(); refetchPending()
      setTimeout(() => setMsg(''), 4000)
    },
    onError: (e: unknown) => { setMsg((e as Error)?.message || 'Sync failed.'); setTimeout(() => setMsg(''), 4000) },
  })

  const approveMut = useMutation({
    mutationFn: async (id: string) => {
      if (!stepUpHeaders()['X-Step-Up-Token']) {
        const ok = await openStepUp({
          title: 'Confirm copy trade with 2FA',
          copy: 'Authorize this real-money order. Your approval is reused for ~5 min.',
        })
        if (!ok) throw new Error('Step-up cancelled')
      }
      return api.post(`/copytrade/pending/${id}/approve`, {},
        { headers: stepUpHeaders(), timeout: SLOW_MS }).then(r => r.data)
    },
    onMutate: (id) => { setBusyId(id); setMsg('') },
    onSuccess: () => { setBusyId(null); setMsg('Order placed ✓'); refetchPending(); refetchSettings(); setTimeout(() => setMsg(''), 3000) },
    onError: (e: unknown) => { setBusyId(null); setMsg((e as Error)?.message || 'Failed') },
  })

  const skipMut = useMutation({
    mutationFn: (id: string) => api.post(`/copytrade/pending/${id}/skip`).then(r => r.data),
    onMutate: (id) => setBusyId(id),
    onSuccess: () => { setBusyId(null); refetchPending() },
    onError: () => setBusyId(null),
  })

  const selected = portfolios.find(p => p.portfolio_id === s.follow_portfolio_id)
  const autoLockedWarning = s.mode === 'auto' && !s.autonomous_unlocked
  const dirty = override !== null

  return (
    <>
      {/* ── Status banner ── */}
      <div style={card}>
        <div style={sectionTitle}>🔁 Copy Trading → Fidelity</div>
        <p style={{ fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.6, margin: '0 0 12px' }}>
          Follow one paper competition portfolio and mirror its positions into your real Fidelity account —
          sized by <strong>weight</strong> (an 8%-of-book paper position → an 8%-of-your-account order, capped at 10%).
          Choose per-portfolio whether trades wait for your approval (HIL) or fire automatically and text you.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <span style={badge(s.enabled ? 'green' : 'grey')}>{s.enabled ? '● Following' : '○ Paused'}</span>
          <span style={badge(s.mode === 'auto' && s.autonomous_unlocked ? 'amber' : 'grey')}>
            {s.mode === 'auto' ? '🤖 Autonomous' : '✋ HIL approval'}
          </span>
          <span style={badge(s.live_trading_enabled ? 'green' : 'red')}>
            {s.live_trading_enabled ? 'Live trading ON' : 'Live trading OFF'}
          </span>
          {s.last_sync && <span style={badge('grey')}>Last sync {fmtTime(s.last_sync)}</span>}
          <span style={badge('grey')}>{Object.keys(s.owned ?? {}).length} copy-held</span>
        </div>
        {s.last_error && (
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--danger)' }}>⚠️ {s.last_error}</div>
        )}
      </div>

      {/* ── Config ── */}
      <div style={card}>
        <div style={sectionTitle}>⚙️ Follow settings</div>

        <div style={{ marginBottom: 14 }}>
          <label style={label}>Portfolio to follow</label>
          <select
            style={input}
            value={s.follow_portfolio_id ?? ''}
            onChange={e => patch({ follow_portfolio_id: e.target.value || null })}
          >
            <option value="">— none selected —</option>
            {portfolios.map(p => (
              <option key={p.portfolio_id} value={p.portfolio_id}>
                {p.name} · {p.all_time_ror >= 0 ? '+' : ''}{p.all_time_ror}% ROR · {p.open_positions} open
              </option>
            ))}
          </select>
          {selected && (
            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--ink-muted)' }}>
              {selected.source_strategy} · win rate {selected.win_rate}% · {selected.total_trades} trades ·
              equity ${selected.current_equity.toLocaleString()}
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 14 }}>
          <div>
            <label style={label}>Mode</label>
            <select style={input} value={s.mode} onChange={e => patch({ mode: e.target.value as 'hil' | 'auto' })}>
              <option value="hil">HIL — approve each</option>
              <option value="auto">Autonomous — auto-fire + text</option>
            </select>
          </div>
          <div>
            <label style={label}>Stop %</label>
            <input style={input} type="number" value={s.stop_pct}
              onChange={e => patch({ stop_pct: Number(e.target.value) })} />
          </div>
          <div>
            <label style={label}>Target %</label>
            <input style={input} type="number" value={s.target_pct}
              onChange={e => patch({ target_pct: Number(e.target.value) })} />
          </div>
          <div>
            <label style={label}>Max new buys / sync</label>
            <input style={input} type="number" value={s.max_new_buys_per_sync}
              onChange={e => patch({ max_new_buys_per_sync: Number(e.target.value) })} />
          </div>
          <div>
            <label style={label}>Fidelity account (optional)</label>
            <input style={input} type="text" placeholder="default" value={s.account ?? ''}
              onChange={e => patch({ account: e.target.value || null })} />
          </div>
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--ink)', marginBottom: 12, cursor: 'pointer' }}>
          <input type="checkbox" checked={s.enabled} onChange={e => patch({ enabled: e.target.checked })} />
          Enable following (background reconcile every ~10 min during market hours)
        </label>

        {autoLockedWarning && (
          <div style={{ padding: '10px 12px', borderRadius: 8, background: 'rgba(245,158,11,0.12)', color: '#b45309', fontSize: 12.5, marginBottom: 12, lineHeight: 1.5 }}>
            ⚠️ Autonomous mode is selected but the server kill-switch <code>COPYTRADE_AUTONOMOUS</code> is OFF.
            Until an operator enables it, trades will still queue as HIL approvals — nothing fires unattended.
          </div>
        )}
        {!disclosureAccepted && (
          <div style={{ padding: '10px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.10)', color: 'var(--danger)', fontSize: 12.5, marginBottom: 12 }}>
            Accept the HIL trading disclosure (Settings tab) before real orders can be placed.
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button variant="primary" disabled={!dirty} loading={saveMut.isPending} loadingText="Saving…"
            onClick={() => saveMut.mutate(pick(s))}>
            Save settings
          </Button>
          <Button variant="ghost" disabled={!s.follow_portfolio_id} loading={syncMut.isPending} loadingText="Reconciling…"
            onClick={() => syncMut.mutate()}>
            Sync now
          </Button>
          {msg && <span style={{ fontSize: 12.5, color: 'var(--ink-muted)' }}>{msg}</span>}
        </div>
      </div>

      {/* ── Pending copy trades ── */}
      <div style={card}>
        <div style={sectionTitle}>⏳ Pending copy trades {pending.length ? `(${pending.length})` : ''}</div>
        {pending.length === 0 ? (
          <EmptyState
            compact
            icon="⏳"
            title="No copy trades waiting"
            description={<>New actions appear here whenever the followed portfolio opens or closes a position (HIL mode), or after you press <em>Sync now</em>.</>}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {pending.map(p => (
              <div key={p.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '10px 12px', borderRadius: 8, border: '1px solid var(--surface-rule)', background: 'var(--surface-raised)', flexWrap: 'wrap' }}>
                <div>
                  <span style={badge(p.action === 'buy' ? 'green' : 'red')}>{p.action.toUpperCase()}</span>{' '}
                  <strong style={{ color: 'var(--ink)', fontSize: 14 }}>{p.ticker}</strong>
                  {p.action === 'buy' && (
                    <span style={{ fontSize: 12.5, color: 'var(--ink-muted)' }}> · ~{p.target_pct}% of account</span>
                  )}
                  <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>{p.reason}</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button variant="primary" size="sm"
                    loading={busyId === p.id && approveMut.isPending}
                    disabled={busyId === p.id}
                    onClick={() => approveMut.mutate(p.id!)}>
                    Approve
                  </Button>
                  <Button variant="ghost" size="sm" disabled={busyId === p.id}
                    onClick={() => skipMut.mutate(p.id!)}>Skip</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

// ── Helpers ─────────────────────────────────────────────────────────────────────

function defaultSettings(): CopySettings {
  return {
    enabled: false, follow_portfolio_id: null, mode: 'hil', account: null,
    stop_pct: 8, target_pct: 20, min_weight: 0.01, max_weight: 0.10,
    max_new_buys_per_sync: 3, owned: {}, last_sync: null, last_error: null,
    last_actions: [], pending_count: 0, autonomous_unlocked: false, live_trading_enabled: false,
  }
}

function pick(s: CopySettings): Partial<CopySettings> {
  return {
    enabled: s.enabled, follow_portfolio_id: s.follow_portfolio_id, mode: s.mode,
    account: s.account, stop_pct: s.stop_pct, target_pct: s.target_pct,
    max_new_buys_per_sync: s.max_new_buys_per_sync,
  }
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}
