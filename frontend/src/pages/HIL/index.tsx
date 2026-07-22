import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '@/api/client'
import { LoadingState } from '@/components/shared/LoadingState'
import { openStepUp, stepUpHeaders } from '@/components/modals/StepUpModal'
import CopyTrade from './CopyTrade'

// ── Thematic HIL types ────────────────────────────────────────────────────────

interface ThematicHilPrefs {
  enabled: boolean
  fidelity_trade: boolean
  dollar_amount: number
  sms_notify: boolean
}

interface ThematicSignal {
  id: string
  ticker: string
  name?: string
  theme?: string
  conviction?: number
  raw_score?: number
  score?: number
  thesis?: string
  catalyst?: string
  target_pct?: number
  stop_pct?: number
  hold_days?: number
  status: string
  diversify?: {
    cluster: string | null
    cluster_names: number
    cluster_pct: number | null
    max_names: number
    max_cluster_pct: number
    blocked: boolean
    cap_reason: string | null
    decay_mult: number
    n_eff: number
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface HilPrefs {
  enabled?: boolean
  risk_profile?: string
  min_risk_reward?: number
  position_max_pct?: number
  position_min_pct?: number
  max_positions?: number
  daily_loss_limit_pct?: number
  approval_timeout_min?: number
  auto_reject_on_timeout?: boolean
  notify_channel?: string
  [key: string]: unknown
}

interface User {
  email?: string
  name?: string
  role?: string
  hil_disclosure_accepted?: boolean
  hil_disclosure_accepted_at?: number
  hil_disclosure_version?: string
  hil_disclosure_effective_date?: string
  current_hil_disclosure_version?: string
  current_hil_disclosure_effective_date?: string
  hil_prefs?: HilPrefs
  phone_number?: string
  phone?: string
  [key: string]: unknown
}

interface SmsStatus {
  provider: string
  sendblue_configured: boolean
  textbelt_key_set: boolean
  textnow_username_set: boolean
  textnow_sid_set: boolean
  default_phone_set: boolean
  default_phone_masked: string
  playwright_available: boolean
}

interface Features {
  real_broker_trading?: boolean
  sms_trade_approvals?: boolean
}

interface HilPendingRaw {
  pending: boolean
  trade?: {
    id?: string
    ticker?: string
    action?: string
    side?: string
    qty?: number
    shares?: number
    price?: number
    entry_price?: number
    [key: string]: unknown
  }
}

interface BrainAction {
  kind: string
  reason?: string
  fraction?: number
  conviction?: number
  stop?: number | null
  target?: number | null
  risk_flags?: string[]
}
interface BrainHolding {
  ticker: string
  shares?: number
  last?: number
  unrealized_pct?: number
  pct_of_account?: number
  account_name?: string
  name?: string
}
interface BrainProposal {
  id: string
  ticker: string
  broker?: string
  action: BrainAction
  holding?: BrainHolding
  status?: string
  created_at?: string
  priority?: boolean
}
interface BrainDeferred {
  ticker: string
  kind: string
  conviction?: number
  reason: string
  holding?: BrainHolding
}
interface BrainProposalsResp {
  ok: boolean
  pending: BrainProposal[]
  count: number
  deferred?: BrainDeferred[]
  history?: BrainProposal[]
}
interface BrainExcluded {
  symbol: string
  account_name?: string
  account_number?: string
  reason: string
}
interface BrainHoldingsResp {
  ok: boolean
  count: number
  excluded?: BrainExcluded[]
  holdings?: Array<{ holding: BrainHolding; action: BrainAction }>
}

// ── Styles ────────────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 10,
  padding: '18px 20px',
  marginBottom: 16,
}

const sectionTitle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 700,
  color: 'var(--ink)',
  marginBottom: 14,
  paddingBottom: 10,
  borderBottom: '1px solid var(--surface-rule)',
  display: 'flex',
  alignItems: 'center',
  gap: 7,
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--ink-muted)',
  marginBottom: 4,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
}

const tabBarWrap: React.CSSProperties = {
  display: 'flex', gap: 4, marginBottom: 16,
  background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)',
  borderRadius: 10, padding: 4,
}
function tabBtn(active: boolean): React.CSSProperties {
  return {
    flex: 1, padding: '9px 14px', fontSize: 13, fontWeight: 600,
    border: 'none', borderRadius: 7, cursor: 'pointer',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#fff' : 'var(--ink-muted)',
    transition: 'background .15s, color .15s',
  }
}
const pillFor = (kind: string): { bg: string; fg: string; label: string } => {
  const k = (kind || '').toUpperCase()
  if (k === 'EXIT') return { bg: 'rgba(239,68,68,.12)', fg: '#f87171', label: 'DROP / EXIT' }
  if (k === 'TRIM') return { bg: 'rgba(251,191,36,.12)', fg: '#fbbf24', label: 'TRIM' }
  if (k === 'ADD') return { bg: 'rgba(52,211,153,.12)', fg: '#34d399', label: 'ADD' }
  if (k === 'ADOPT') return { bg: 'rgba(96,165,250,.12)', fg: '#60a5fa', label: 'KEEP / ADOPT' }
  if (k === 'SET_STOP') return { bg: 'rgba(167,139,250,.12)', fg: '#a78bfa', label: 'SET STOP' }
  return { bg: 'var(--surface-raised)', fg: 'var(--ink-muted)', label: k || '—' }
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '7px 10px',
  border: '1px solid var(--surface-rule)',
  borderRadius: 6,
  background: 'var(--surface-soft)',
  color: 'var(--ink)',
  fontSize: 13,
  outline: 'none',
  boxSizing: 'border-box',
}

const btnPrimary: React.CSSProperties = {
  padding: '8px 18px',
  borderRadius: 7,
  border: 'none',
  background: 'var(--accent)',
  color: '#fff',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

const btnSecondary: React.CSSProperties = {
  padding: '8px 18px',
  borderRadius: 7,
  border: '1px solid var(--surface-rule)',
  background: 'var(--surface-soft)',
  color: 'var(--ink)',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

const badge = (color: string): React.CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '2px 9px',
  borderRadius: 99,
  fontSize: 11,
  fontWeight: 700,
  background: color === 'green' ? 'rgba(52,211,153,.12)' :
              color === 'red'   ? 'rgba(239,68,68,.12)'   :
              color === 'amber' ? 'rgba(251,191,36,.12)'  :
                                  'rgba(148,163,184,.12)',
  color: color === 'green' ? '#34d399' :
         color === 'red'   ? '#ef4444'  :
         color === 'amber' ? '#fbbf24'  :
                             '#94a3b8',
  border: `1px solid ${
    color === 'green' ? 'rgba(52,211,153,.3)' :
    color === 'red'   ? 'rgba(239,68,68,.3)'  :
    color === 'amber' ? 'rgba(251,191,36,.3)' :
                        'rgba(148,163,184,.3)'
  }`,
})

// ── Preset definitions (using API field names) ────────────────────────────────

const PRESETS: Record<string, Partial<HilPrefs>> = {
  conservative: {
    min_risk_reward: 2.0, position_max_pct: 10, position_min_pct: 2,
    max_positions: 3, daily_loss_limit_pct: 1.0, risk_profile: 'conservative',
  },
  balanced: {
    min_risk_reward: 1.5, position_max_pct: 25, position_min_pct: 3,
    max_positions: 5, daily_loss_limit_pct: 2.0, risk_profile: 'balanced',
  },
  aggressive: {
    min_risk_reward: 1.2, position_max_pct: 40, position_min_pct: 4,
    max_positions: 8, daily_loss_limit_pct: 3.5, risk_profile: 'aggressive',
  },
}

const DEFAULT_PREFS: HilPrefs = {
  enabled: false,
  risk_profile: 'balanced',
  min_risk_reward: 1.5,
  position_max_pct: 25,
  position_min_pct: 10,
  max_positions: 5,
  daily_loss_limit_pct: 2.0,
  approval_timeout_min: 15,
  auto_reject_on_timeout: true,
  notify_channel: 'sms',
}

// ── On-demand trade chart (server-rendered TradingView-style PNG) ─────────────
// Lazy: only fetched when the user expands it, so the Approvals queue doesn't
// trigger N heavy chart renders at once. Real entry/stop/target (or pcts) drive
// the lines; the endpoint caches each chart ~15 min.
function tradeChartUrl(p: {
  ticker: string; entry?: number | null; stop?: number | null; target?: number | null
  stopPct?: number | null; targetPct?: number | null
}): string {
  const q = new URLSearchParams({ ticker: p.ticker })
  if (p.entry != null) q.set('entry', String(p.entry))
  if (p.stop != null) q.set('stop', String(p.stop))
  if (p.target != null) q.set('target', String(p.target))
  if (p.stopPct != null) q.set('stop_pct', String(p.stopPct))
  if (p.targetPct != null) q.set('target_pct', String(p.targetPct))
  return `/api/market/trade-chart.png?${q.toString()}`
}

function TradeChart(props: {
  ticker: string; entry?: number | null; stop?: number | null; target?: number | null
  stopPct?: number | null; targetPct?: number | null
}) {
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState(false)
  return (
    <div style={{ marginBottom: 10 }}>
      <button
        onClick={() => { setErr(false); setOpen(o => !o) }}
        style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 12, padding: 0, fontWeight: 600 }}
      >
        {open ? '▾ Hide chart' : '📈 Show chart'}
      </button>
      {open && !err && (
        <img
          src={tradeChartUrl(props)}
          alt={`${props.ticker} trade chart`}
          loading="lazy"
          onError={() => setErr(true)}
          style={{ marginTop: 8, width: '100%', borderRadius: 8, border: '1px solid var(--surface-rule)', display: 'block' }}
        />
      )}
      {open && err && (
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 8 }}>Chart unavailable right now.</div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function HILPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()

  const [disclosureChecked, setDisclosureChecked] = useState(false)
  const [formOverride, setFormOverride] = useState<HilPrefs | null>(null)
  const [formDirty, setFormDirty] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [phoneOverride, setPhoneOverride] = useState<string | null>(null)
  const [phoneMsg, setPhoneMsg] = useState('')

  // ── Queries ──────────────────────────────────────────────────────
  const { data: user, isLoading: userLoading } = useQuery<User>({
    queryKey: ['auth-me'],
    queryFn: () => api.get<User>('/auth/me').then(r => r.data),
  })

  const { data: features } = useQuery<Features>({
    queryKey: ['auth-features'],
    queryFn: () => api.get<Features>('/auth/features').then(r => r.data),
    staleTime: 60_000,
  })

  const { data: smsStatus } = useQuery<SmsStatus>({
    queryKey: ['sms-status'],
    queryFn: () => api.get<SmsStatus>('/paper/sms/status').then(r => r.data),
    refetchInterval: 30_000,
  })

  const { data: hilRaw, refetch: refetchPending } = useQuery<HilPendingRaw>({
    queryKey: ['hil-pending'],
    queryFn: () => api.get<HilPendingRaw>('/paper/hil/pending').then(r => r.data),
    refetchInterval: 10_000,
  })

  const form = formOverride ?? { ...DEFAULT_PREFS, ...(user?.hil_prefs ?? {}) }
  function setForm(f: HilPrefs | ((prev: HilPrefs) => HilPrefs)) {
    setFormOverride(typeof f === 'function' ? f(form) : f)
    setFormDirty(true)
  }
  const phone = phoneOverride ?? (user?.phone_number as string | undefined) ?? (user?.phone as string | undefined) ?? ''
  const setPhone = setPhoneOverride

  // ── Mutations ─────────────────────────────────────────────────────
  const disclosureMut = useMutation({
    mutationFn: (version: string) =>
      api.post('/auth/me/hil-disclosure', { accepted: true, version }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-me'] })
      setDisclosureChecked(false)
    },
  })

  const saveMut = useMutation({
    mutationFn: (prefs: Partial<HilPrefs>) =>
      api.post('/auth/me/hil-prefs', prefs).then(r => r.data),
    onSuccess: () => {
      setSavedMsg('Preferences saved.')
      setFormOverride(null)
      setFormDirty(false)
      qc.invalidateQueries({ queryKey: ['auth-me'] })
      setTimeout(() => setSavedMsg(''), 3000)
    },
  })

  const resolveMut = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      api.post('/paper/hil/resolve', { id, action }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hil-pending'] }),
  })

  const smsMut = useMutation({
    mutationFn: (p: string) =>
      api.post('/paper/sms/test', { phone: p, message: 'Agentic Trader HIL test — SMS confirmed.' }).then(r => r.data),
    onSuccess: () => { setPhoneMsg('Test sent.'); setTimeout(() => setPhoneMsg(''), 4000) },
    onError: () => { setPhoneMsg('Failed to send.'); setTimeout(() => setPhoneMsg(''), 4000) },
  })

  // ── Thematic HIL ─────────────────────────────────────────────────
  const [thematicHilForm, setThematicHilForm] = useState<ThematicHilPrefs | null>(null)
  const [thematicSavedMsg, setThematicSavedMsg] = useState('')
  const [approvingId, setApprovingId] = useState<string | null>(null)

  const { data: thematicHilRaw, refetch: refetchThematicHil } = useQuery<{ hil: ThematicHilPrefs }>({
    queryKey: ['thematic-hil-settings'],
    queryFn: () => api.get('/thematic/auto/hil-settings').then(r => r.data),
    staleTime: 30_000,
  })

  const { data: thematicSignalsRaw, refetch: refetchThematicSignals } = useQuery<{ signals: ThematicSignal[] }>({
    queryKey: ['thematic-signals'],
    queryFn: () => api.get('/thematic/auto/signals').then(r => r.data),
    refetchInterval: 30_000,
    enabled: !!(thematicHilForm ?? thematicHilRaw?.hil)?.enabled,
  })

  const thematicHil: ThematicHilPrefs = thematicHilForm ?? thematicHilRaw?.hil ?? {
    enabled: false, fidelity_trade: false, dollar_amount: 500, sms_notify: true,
  }
  const pendingSignals = (thematicSignalsRaw?.signals ?? []).filter(s => s.status === 'pending')

  const saveThematicHilMut = useMutation({
    mutationFn: (prefs: ThematicHilPrefs) =>
      api.post('/thematic/auto/hil-settings', prefs).then(r => r.data),
    onSuccess: () => {
      setThematicSavedMsg('Saved.')
      setThematicHilForm(null)
      qc.invalidateQueries({ queryKey: ['thematic-hil-settings'] })
      setTimeout(() => setThematicSavedMsg(''), 3000)
    },
  })

  const approveSignalMut = useMutation({
    mutationFn: ({ id, dollar, fidelity }: { id: string; dollar: number; fidelity: boolean }) =>
      api.post(`/thematic/auto/signals/${id}/approve`, {
        dollar_amount: dollar,
        fidelity_trade: fidelity,
        execute_fidelity: fidelity,
      }).then(r => r.data),
    onSuccess: () => {
      setApprovingId(null)
      refetchThematicSignals()
    },
    onError: () => setApprovingId(null),
  })

  const skipSignalMut = useMutation({
    mutationFn: (id: string) => api.post(`/thematic/auto/signals/${id}/skip`).then(r => r.data),
    onSuccess: () => refetchThematicSignals(),
  })

  const [tab, setTab] = useState<'approvals' | 'settings' | 'copytrade'>(() => {
    // Deep link from the trade-request SMS lands on the Approvals tab; the
    // copy-trade SMS deep-links to ?tab=copytrade.
    try {
      const t = new URLSearchParams(window.location.search).get('tab')
      if (t === 'settings') return 'settings'
      if (t === 'copytrade') return 'copytrade'
      return 'approvals'
    } catch {
      return 'approvals'
    }
  })

  // ── Holdings Brain (full-account takeover: keep/drop/trim/exit proposals) ──
  const { data: brainData, refetch: refetchBrain } = useQuery<BrainProposalsResp>({
    queryKey: ['brain-proposals'],
    queryFn: () => api.get<BrainProposalsResp>('/thematic/brain/proposals').then(r => r.data),
    refetchInterval: 20_000,
  })
  // Live Fidelity scrape (Playwright) — only when viewing Settings, to avoid a
  // heavy broker read on every Approvals poll.
  const { data: brainHoldings, refetch: refetchBrainHoldings } = useQuery<BrainHoldingsResp>({
    queryKey: ['brain-holdings'],
    queryFn: () => api.get<BrainHoldingsResp>('/thematic/brain/holdings', { timeout: 180_000 }).then(r => r.data),
    enabled: tab === 'settings',
  })
  // Broker actions drive Playwright (scrape / order placement) → 30-90s. Override
  // the default 30s axios timeout so the UI doesn't bail while the order completes.
  const SLOW_MS = 180_000
  const brainAssessMut = useMutation({
    mutationFn: () => api.post('/thematic/brain/assess', {}, { timeout: SLOW_MS }).then(r => r.data),
    onSuccess: () => { refetchBrain(); refetchBrainHoldings() },
  })
  const [brainBusyId, setBrainBusyId] = useState<string | null>(null)
  const [brainMsg, setBrainMsg] = useState<{ id: string; text: string; ok: boolean } | null>(null)
  const brainApproveMut = useMutation({
    mutationFn: async ({ id, execute }: { id: string; execute: boolean }) => {
      // Real orders (execute) need step-up 2FA; store-only keeps/stops do not.
      // Reuse a cached step-up token (~5 min) so you enter a code once, not every tap.
      if (execute && !stepUpHeaders()['X-Step-Up-Token']) {
        const ok = await openStepUp({
          title: 'Confirm trade with 2FA',
          copy: 'Authorize this real-money order. Your approval is reused for ~5 min.',
        })
        if (!ok) throw new Error('Step-up cancelled')
      }
      return api.post(`/thematic/brain/proposals/${id}/approve`, { execute },
        { headers: stepUpHeaders(), timeout: SLOW_MS }).then(r => r.data)
    },
    onMutate: ({ id }) => { setBrainBusyId(id); setBrainMsg(null) },
    onSuccess: (_d, { id, execute }) => {
      setBrainBusyId(null)
      setBrainMsg({ id, ok: true, text: execute ? 'Order placed ✓' : 'Kept & adopted ✓' })
      refetchBrain(); refetchBrainHoldings()
    },
    onError: (e: unknown, { id }) => {
      setBrainBusyId(null)
      setBrainMsg({ id, ok: false, text: (e as Error)?.message || 'Failed' })
    },
  })
  const brainSkipMut = useMutation({
    mutationFn: (id: string) => api.post(`/thematic/brain/proposals/${id}/skip`).then(r => r.data),
    onMutate: (id) => setBrainBusyId(id),
    onSuccess: () => { setBrainBusyId(null); refetchBrain() },
    onError: () => setBrainBusyId(null),
  })
  // Bulk "keep & adopt everything" — store-only, no orders, no step-up. Sequential
  // so the proposals file isn't raced.
  const brainKeepAllMut = useMutation({
    mutationFn: async (ids: string[]) => {
      for (const id of ids) {
        await api.post(`/thematic/brain/proposals/${id}/approve`, { execute: false }, { timeout: SLOW_MS })
      }
    },
    onSuccess: () => { setBrainMsg(null); refetchBrain(); refetchBrainHoldings() },
  })

  // ── Helpers ───────────────────────────────────────────────────────
  function setField(key: keyof HilPrefs, value: unknown) {
    setForm(f => ({ ...f, [key]: value }))
  }

  function applyPreset(name: string) {
    const p = PRESETS[name]
    if (!p) return
    setForm(f => ({ ...f, ...p }))
  }

  function handleSave() {
    const prefs: Partial<HilPrefs> = {}
    const keys: (keyof HilPrefs)[] = [
      'enabled', 'risk_profile', 'min_risk_reward', 'position_max_pct', 'position_min_pct',
      'max_positions', 'daily_loss_limit_pct', 'approval_timeout_min',
      'auto_reject_on_timeout', 'notify_channel',
    ]
    for (const k of keys) {
      if (form[k] !== undefined) prefs[k] = form[k]
    }
    saveMut.mutate(prefs)
  }

  function handleDiscard() {
    setFormOverride(null)
    setPhoneOverride(null)
    setFormDirty(false)
  }

  // ── Derived state ─────────────────────────────────────────────────
  const accepted = user?.hil_disclosure_accepted === true
  const currentVersion = user?.current_hil_disclosure_version || '1.0'
  const currentEffectiveDate = user?.current_hil_disclosure_effective_date || '2026-05-20'
  const acceptedVersion = user?.hil_disclosure_version
  const disclosureVersionOk = !acceptedVersion || acceptedVersion === currentVersion
  const fullyAccepted = accepted && disclosureVersionOk
  const needsReAccept = accepted && !disclosureVersionOk

  const acceptedAt = user?.hil_disclosure_accepted_at
    ? new Date(user.hil_disclosure_accepted_at * 1000).toLocaleDateString()
    : null

  // real_broker_trading === false means the feature flag is off → HIL unavailable
  const hilUnavailable = features?.real_broker_trading === false

  const profile = (form.risk_profile as string | undefined) ?? 'balanced'
  const pending = hilRaw?.pending ? hilRaw.trade : null
  const brainPending = brainData?.pending ?? []
  const brainDeferred = brainData?.deferred ?? []
  const brainExcluded = brainHoldings?.excluded ?? []
  const approvalsCount = (pending ? 1 : 0) + pendingSignals.length + brainPending.length

  if (userLoading) return <LoadingState />

  return (
    <div id="panel-hil" style={{ padding: 24, maxWidth: 860 }}>

      {/* ── HIL Unavailable banner ── */}
      {hilUnavailable && (
        <div style={{
          ...card,
          borderColor: 'rgba(251,191,36,.32)', background: 'rgba(251,191,36,.08)',
          marginBottom: 16,
        }}>
          <div style={{ fontWeight: 700, color: '#fbbf24', fontSize: 13, marginBottom: 4 }}>
            HIL is currently unavailable
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-muted)', lineHeight: 1.6 }}>
            Please come back later. Real broker trading is shut off in Settings, so Human-in-the-Loop
            approvals, configuration, and live trade controls are paused.
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <button style={btnPrimary} onClick={() => navigate('/dashboard')}>Go to dashboard</button>
            <button style={btnSecondary} onClick={() => navigate('/admin')}>Open admin flags</button>
          </div>
        </div>
      )}

      {/* ── 1. Status header card ── */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)', marginBottom: 4 }}>
              🛡️ Human-In-the-Loop
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-muted)', maxWidth: 560 }}>
              Review and approve every real-money trade. <strong>Approvals</strong> is your action queue;
              <strong> HIL Settings</strong> holds your risk profile, disclosure, SMS, and brain controls.
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={badge(fullyAccepted ? 'green' : needsReAccept ? 'amber' : 'red')}>
              {fullyAccepted ? '✓ Disclosure accepted' : needsReAccept ? '⚠ Updated disclosure required' : '⚠ Disclosure required'}
            </span>
            <span style={badge(form.enabled ? 'green' : 'grey')}>
              {form.enabled ? '● HIL active' : '○ HIL disabled'}
            </span>
            {smsStatus?.default_phone_set && (
              <span style={badge('green')}>📱 SMS configured</span>
            )}
          </div>
        </div>
      </div>

      {/* ── Tab bar ── */}
      <div style={tabBarWrap}>
        <button style={tabBtn(tab === 'approvals')} onClick={() => setTab('approvals')}>
          ⏳ Approvals{approvalsCount ? ` (${approvalsCount})` : ''}
        </button>
        <button style={tabBtn(tab === 'copytrade')} onClick={() => setTab('copytrade')}>
          🔁 Copy Trade
        </button>
        <button style={tabBtn(tab === 'settings')} onClick={() => setTab('settings')}>
          ⚙️ HIL Settings
        </button>
      </div>

      {/* ===== COPY TRADE ===== */}
      {tab === 'copytrade' && <CopyTrade disclosureAccepted={fullyAccepted} />}

      {/* ===== SETTINGS: disclosure (also shown until accepted) ===== */}
      {(tab === 'settings' || !fullyAccepted) && (
      <>
      {/* ── 2. Disclosure card ── */}
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
              Required HIL trading disclosure
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
              Version {currentVersion}, effective {currentEffectiveDate}. Required before real broker trading can be enabled.
            </div>
          </div>
          {fullyAccepted && acceptedAt && (
            <div style={{ fontSize: 11, color: '#34d399', whiteSpace: 'nowrap' }}>
              v{acceptedVersion} accepted {acceptedAt}
            </div>
          )}
          {needsReAccept && (
            <span style={badge('amber')}>v{currentVersion} requires re-acceptance</span>
          )}
        </div>

        <details {...(fullyAccepted ? {} : { open: true })} style={{ marginBottom: 16 }}>
          <summary style={{
            fontSize: 13, fontWeight: 600, color: 'var(--ink)', cursor: 'pointer',
            padding: '8px 0', userSelect: 'none',
          }}>
            Human-in-the-Loop Trading Disclosure
          </summary>

          <div style={{
            marginTop: 12, padding: '14px 16px',
            background: 'var(--surface-soft)', borderRadius: 8,
            border: '1px solid var(--surface-rule)',
            fontSize: 12.5, color: 'var(--ink-muted)', lineHeight: 1.65,
          }}>
            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 12, fontStyle: 'italic' }}>
              This disclosure is a required condition for enabling real-money broker trading features in Agentic Trader.
              It is not legal advice. Consult a licensed securities attorney for advice specific to your situation.
            </div>

            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 8 }}>
              Plain-English Summary
            </div>
            <p style={{ marginBottom: 10 }}>
              Agentic Trader is a software dashboard that uses artificial intelligence, machine learning models, and automated
              pipelines to analyze market data, score trade candidates, generate summaries, and surface proposed trades for
              your review. It does not trade for you. It does not make trading decisions on your behalf. It is not a broker,
              a registered investment adviser, or a fiduciary.
            </p>
            <p style={{ marginBottom: 10 }}>
              Every real-money trade that reaches your broker must be individually reviewed and explicitly approved by you,
              a live authenticated human user, inside the Agentic Trader dashboard. No AI signal, automated alert,
              paper-trading result, SMS notification, or email can substitute for your independent review and deliberate approval.
            </p>

            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', margin: '12px 0 8px' }}>
              Required Terms
            </div>
            <ol style={{ paddingLeft: 20, margin: 0 }}>
              {[
                'Human-in-the-Loop means AI, ML, paper runners, alerts, and automation may prepare, suggest, rank, or preview trades, but they cannot submit or complete a real-money trade without your explicit dashboard approval.',
                'All real-money trade approvals must occur inside the authenticated Agentic Trader dashboard. Replying to an SMS, email, or notification does not approve a trade under the current system design.',
                'Before approving any trade, you are responsible for verifying ticker, action, quantity, order type, account, estimated price, risk exposure, and current market conditions.',
                'AI signals, model outputs, trade rankings, market summaries, alerts, paper-trading results, and backtests are informational tools only. They are not financial advice, investment recommendations, solicitations, or guarantees.',
                'No trade is guaranteed to execute at the price displayed in the dashboard. Market orders may slip, limit orders may not fill, and broker rules, outages, rate limits, or account restrictions may affect execution.',
                'Technical failures can occur, including broker API errors, Cloudflare tunnel interruption, internet failures, stale quotes, data feed errors, model inference errors, parsing errors, duplicate order attempts, session expiration, and third-party rate limits.',
                'Paper trading, simulated performance, and backtests are hypothetical. They can overstate performance and may not account for spreads, commissions, fees, taxes, market impact, slippage, overfitting, look-ahead bias, survivorship bias, or emotional decision-making.',
                'Real-money trading involves substantial risk, including loss of principal and, when margin is used, losses that may exceed your initial investment.',
                'Agentic Trader is a software technology tool. It is not a broker-dealer, investment adviser, registered investment adviser, fiduciary, tax adviser, attorney, or financial planner.',
                'You are solely responsible for all trades you approve, all profits and losses, all federal, state, and local tax obligations, and compliance with all applicable laws, broker agreements, margin rules, and account restrictions.',
                'Pattern day trader rules, wash-sale rules, securities law, broker restrictions, and tax reporting obligations may apply to your trading activity. Agentic Trader does not monitor or ensure your compliance.',
                'If you need personalized financial, tax, or legal advice, seek it from a qualified licensed professional. Do not rely on Agentic Trader or its outputs for personalized advice.',
              ].map((term, i) => (
                <li key={i} style={{ marginBottom: 6 }}>{term}</li>
              ))}
            </ol>

            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', margin: '12px 0 8px' }}>
              Required Acknowledgment
            </div>
            <p style={{ marginBottom: 0 }}>
              By accepting, you acknowledge that you have read this HIL Trading Disclosure in its entirety, understand it,
              and agree that you are the human responsible for reviewing and approving every real-money trade submitted
              through Agentic Trader. You understand that approving a trade may cause an order to be submitted to your
              connected broker account, and that your broker's execution, rejection, fill price, fees, margin rules,
              and account restrictions are outside Agentic Trader's control.
            </p>
          </div>
        </details>

        {!fullyAccepted ? (
          <div>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer', marginBottom: 14 }}>
              <input
                type="checkbox"
                checked={disclosureChecked}
                onChange={e => setDisclosureChecked(e.target.checked)}
                style={{ marginTop: 2, width: 15, height: 15, flexShrink: 0 }}
              />
              <span style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.5 }}>
                I have read and accept the Agentic Trader Human-in-the-Loop Trading Disclosure version {currentVersion},
                and I understand I am solely responsible for reviewing, approving, rejecting, and managing every
                real-money trade and all resulting risks.
              </span>
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <button
                style={{
                  ...btnPrimary,
                  opacity: disclosureChecked && !disclosureMut.isPending ? 1 : 0.5,
                  cursor: disclosureChecked ? 'pointer' : 'not-allowed',
                }}
                disabled={!disclosureChecked || disclosureMut.isPending}
                onClick={() => disclosureMut.mutate(currentVersion)}
              >
                {disclosureMut.isPending ? 'Saving…' : 'Accept HIL disclosure'}
              </button>
              {disclosureMut.isError && (
                <span style={{ fontSize: 12, color: 'var(--danger)' }}>
                  {(disclosureMut.error as Error)?.message || 'Failed to save. Try again.'}
                </span>
              )}
            </div>
          </div>
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 12px', borderRadius: 7,
            background: 'rgba(52,211,153,.08)', border: '1px solid rgba(52,211,153,.25)',
            fontSize: 13, color: '#34d399',
          }}>
            ✓ Disclosure accepted — HIL configuration unlocked
          </div>
        )}
      </div>

      </>
      )}

      {/* ── Locked message if not accepted ── */}
      {!fullyAccepted && (
        <div style={{
          ...card,
          textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13, padding: '24px 20px',
        }}>
          Accept the HIL trading disclosure above before configuring approvals, SMS, bridge status, or pending real-money trade controls.
        </div>
      )}

      {fullyAccepted && (
        <>
          {/* ===== SETTINGS PANEL (risk · behavior · sms · bridge) ===== */}
          {tab === 'settings' && (
          <>
          {/* ── 3. Risk profile card ── */}
          <div style={card}>
            <div style={sectionTitle}>⚖️ Risk profile</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: -8, marginBottom: 14 }}>
              Sizing/limits for the <strong>paper competition &amp; candidate scanner</strong>. Live broker sizing
              is governed separately by compliance (10% position cap) and the brain&apos;s thematic conviction —
              see <em>Holdings-Brain controls</em> below.
            </div>

            {/* Preset cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
              {[
                { key: 'conservative', icon: '🛡️', label: 'Conservative', sub: 'Capital preservation', detail: 'R:R ≥ 2.0 · 10% max · 3 positions · 1% daily stop' },
                { key: 'balanced',     icon: '⚡', label: 'Balanced',      sub: 'Steady growth',       detail: 'R:R ≥ 1.5 · 25% max · 5 positions · 2% daily stop', rec: true },
                { key: 'aggressive',   icon: '🔥', label: 'Aggressive',    sub: 'Higher risk/reward',  detail: 'R:R ≥ 1.2 · 40% max · 8 positions · 3.5% daily stop' },
              ].map(p => (
                <div
                  key={p.key}
                  onClick={() => applyPreset(p.key)}
                  style={{
                    border: `1px solid ${profile === p.key ? 'var(--accent)' : 'var(--surface-rule)'}`,
                    background: profile === p.key ? 'var(--surface-raised)' : 'var(--surface-soft)',
                    borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
                    boxShadow: profile === p.key ? 'inset 0 0 0 1px var(--accent)' : 'none',
                    transition: 'border-color .15s, background-color .15s',
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 5 }}>
                    {p.icon} {p.label}
                    {p.rec && (
                      <span style={{
                        fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em',
                        color: '#34d399', background: 'rgba(52,211,153,.12)', padding: '1px 5px', borderRadius: 99,
                      }}>Recommended</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 2 }}>{p.sub}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>{p.detail}</div>
                </div>
              ))}
            </div>

            {/* Grid inputs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '12px 20px' }}>
              <div>
                <label style={labelStyle} htmlFor="hil-min-rr" title="Minimum reward-to-risk ratio for a setup">Min Risk:Reward</label>
                <input id="hil-min-rr" type="number" step="0.1" min="0.5" max="10" style={inputStyle}
                  value={form.min_risk_reward ?? ''}
                  onChange={e => setField('min_risk_reward', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-pos-max" title="Max % of account value per position">Position Max %</label>
                <input id="hil-pos-max" type="number" step="1" min="1" max="100" style={inputStyle}
                  value={form.position_max_pct ?? ''}
                  onChange={e => setField('position_max_pct', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-pos-min" title="Min % of account value per position">Position Min %</label>
                <input id="hil-pos-min" type="number" step="0.5" min="0.5" max="100" style={inputStyle}
                  value={form.position_min_pct ?? ''}
                  onChange={e => setField('position_min_pct', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-max-pos" title="Max concurrent open positions">Max Positions</label>
                <input id="hil-max-pos" type="number" step="1" min="1" max="50" style={inputStyle}
                  value={form.max_positions ?? ''}
                  onChange={e => setField('max_positions', parseInt(e.target.value, 10))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-daily-loss" title="Halt new entries past this daily loss">Daily Loss Limit %</label>
                <input id="hil-daily-loss" type="number" step="0.1" min="0.1" max="50" style={inputStyle}
                  value={form.daily_loss_limit_pct ?? ''}
                  onChange={e => setField('daily_loss_limit_pct', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-profile-input">Active profile</label>
                <input id="hil-profile-input" type="text" style={{ ...inputStyle, color: 'var(--ink-faint)' }}
                  value={form.risk_profile ?? ''}
                  readOnly />
              </div>
            </div>
          </div>

          {/* ── 4. Approvals & behavior card ── */}
          <div style={card}>
            <div style={sectionTitle}>🔔 Approvals &amp; behavior</div>

            <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, cursor: 'pointer', marginBottom: 18 }}>
              <span style={{ fontSize: 13, color: 'var(--ink)' }}>Require SMS approval before real-money trades</span>
              {/* Toggle switch */}
              <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', flexShrink: 0 }}>
                <input
                  type="checkbox"
                  checked={!!form.enabled}
                  onChange={e => setField('enabled', e.target.checked)}
                  style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }}
                />
                <span onClick={() => setField('enabled', !form.enabled)} style={{
                  display: 'inline-block', width: 36, height: 20, borderRadius: 10,
                  background: form.enabled ? 'var(--accent)' : 'var(--surface-raised)',
                  position: 'relative', cursor: 'pointer', transition: 'background .2s',
                }}>
                  <span style={{
                    position: 'absolute', top: 2, left: form.enabled ? 18 : 2,
                    width: 16, height: 16, borderRadius: 8, background: '#fff', transition: 'left .2s',
                  }} />
                </span>
              </span>
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '12px 20px' }}>
              <div>
                <label style={labelStyle} htmlFor="hil-timeout" title="How long to wait for your SMS reply before timing out">
                  Approval Timeout (min)
                </label>
                <input id="hil-timeout" type="number" step="1" min="1" max="120" style={inputStyle}
                  value={form.approval_timeout_min ?? 15}
                  onChange={e => setField('approval_timeout_min', parseInt(e.target.value, 10))} />
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-on-timeout">On timeout</label>
                <select id="hil-on-timeout" style={{ ...inputStyle, cursor: 'pointer' }}
                  value={form.auto_reject_on_timeout === false ? 'approve' : 'reject'}
                  onChange={e => setField('auto_reject_on_timeout', e.target.value === 'reject')}>
                  <option value="reject">Reject trade (safe)</option>
                  <option value="approve">Auto-approve</option>
                </select>
              </div>
              <div>
                <label style={labelStyle} htmlFor="hil-notify">Notify via</label>
                <select id="hil-notify" style={{ ...inputStyle, cursor: 'pointer' }}
                  value={(form.notify_channel as string | undefined) ?? 'sms'}
                  onChange={e => setField('notify_channel', e.target.value)}>
                  <option value="sms">SMS</option>
                  <option value="email">Email</option>
                  <option value="none">None</option>
                </select>
              </div>
            </div>

            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 12 }}>
              Real-money execution also requires step-up 2FA.{' '}
              <button
                onClick={() => navigate('/settings')}
                style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 11, padding: 0 }}
              >
                Manage 2FA in Settings →
              </button>
            </div>
          </div>

          {/* ── 5. Phone / SMS card ── */}
          <div style={card}>
            <div style={sectionTitle}>📱 Approval phone (SMS)</div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              {smsStatus?.default_phone_set ? (
                <span style={badge('green')}>● SMS configured · {smsStatus.default_phone_masked}</span>
              ) : (
                <span style={badge('red')}>○ No SMS number set</span>
              )}
              <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                Provider: {smsStatus?.provider ?? '—'}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle} htmlFor="hil-phone">Mobile number</label>
                <input id="hil-phone" type="tel" style={inputStyle} placeholder="+1 555 123 4567"
                  value={phone}
                  onChange={e => setPhone(e.target.value)} />
              </div>
              <button
                style={{ ...btnPrimary, whiteSpace: 'nowrap', flexShrink: 0 }}
                disabled={smsMut.isPending || !phone}
                onClick={() => smsMut.mutate(phone)}
              >
                {smsMut.isPending ? 'Sending…' : 'Save & send test'}
              </button>
            </div>
            {phoneMsg && (
              <div style={{ marginTop: 8, fontSize: 12, color: phoneMsg.includes('sent') ? '#34d399' : 'var(--danger)' }}>
                {phoneMsg}
              </div>
            )}
          </div>

          {/* ── 6. Bridge / approval bridge card ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--ink)' }}>🌉 Approval bridge</div>
              <button style={{ ...btnSecondary, padding: '4px 12px', fontSize: 12 }}
                onClick={() => qc.invalidateQueries({ queryKey: ['sms-status'] })}>
                ↺ Refresh
              </button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 14 }}>
              The bridge delivers the approval link to your phone and relays your reply back.
              Provider credentials are configured by an admin.
            </div>
            {smsStatus ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px 24px' }}>
                {[
                  ['Provider', smsStatus.provider],
                  ['Outbound', (smsStatus.sendblue_configured || smsStatus.textnow_username_set || smsStatus.textbelt_key_set) ? 'Configured' : 'Not set'],
                  ['Default phone', smsStatus.default_phone_set ? smsStatus.default_phone_masked || 'Set' : '—'],
                  ['Sendblue', smsStatus.sendblue_configured ? 'Yes' : 'No'],
                  ['Textbelt key', smsStatus.textbelt_key_set ? 'Yes' : 'No'],
                  ['TextNow', smsStatus.textnow_username_set ? 'Yes' : 'No'],
                  ['Playwright', smsStatus.playwright_available ? 'Available' : 'Not available'],
                ].map(([k, v]) => (
                  <div key={k as string} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--surface-rule)' }}>
                    <span style={{ color: 'var(--ink-muted)' }}>{k}</span>
                    <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{v as string}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 13, color: 'var(--ink-faint)' }}>Loading bridge status…</div>
            )}
            {smsStatus && (
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 10 }}>
                Inbound reply bridge{' '}
                <span style={{ color: smsStatus.sendblue_configured ? '#34d399' : '#fbbf24', fontWeight: 600 }}>
                  {smsStatus.sendblue_configured ? 'active' : 'limited'}
                </span>
                . Approve from the link in the SMS, or reply to the message.
              </div>
            )}
          </div>

          </>
          )}

          {/* ===== APPROVALS PANEL ===== */}
          {tab === 'approvals' && (
          <>
          {/* ── Holdings-Brain proposals (keep / drop / trim / exit) ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <div style={sectionTitle}>🧠 Holdings-Brain proposals</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {(() => {
                  const keepIds = brainPending
                    .filter(p => !['EXIT', 'TRIM', 'ADD'].includes((p.action?.kind || '').toUpperCase()))
                    .map(p => p.id)
                  return keepIds.length > 1 ? (
                    <button style={{ ...btnPrimary, padding: '4px 12px', fontSize: 12 }}
                      disabled={brainKeepAllMut.isPending}
                      onClick={() => brainKeepAllMut.mutate(keepIds)}>
                      {brainKeepAllMut.isPending ? 'Keeping…' : `✓ Keep all (${keepIds.length})`}
                    </button>
                  ) : null
                })()}
                <button style={{ ...btnSecondary, padding: '4px 12px', fontSize: 12 }}
                  disabled={brainAssessMut.isPending}
                  onClick={() => brainAssessMut.mutate()}>
                  {brainAssessMut.isPending ? 'Scanning…' : '↻ Re-assess account'}
                </button>
              </div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 14 }}>
              On takeover the brain proposes which existing holdings to <strong>keep</strong> vs
              <strong> drop</strong>, plus trims/exits — capped per cycle to avoid churn. Each is propose-only
              and needs your approval (step-up 2FA on live orders).
            </div>

            {brainPending.length === 0 ? (
              <div style={{ padding: '16px 0', textAlign: 'center', fontSize: 13, color: 'var(--ink-faint)' }}>
                No brain proposals pending. Re-assess to refresh.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {brainPending.map(p => {
                  const pill = pillFor(p.action?.kind || '')
                  const h = p.holding || { ticker: p.ticker }
                  const isOrder = ['EXIT', 'TRIM', 'ADD'].includes((p.action?.kind || '').toUpperCase())
                  return (
                    <div key={p.id} style={{
                      border: '1px solid var(--surface-rule)', borderRadius: 8,
                      padding: '12px 14px', background: 'var(--surface-soft)',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
                        <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--ink)' }}>{p.ticker}</span>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 99, background: pill.bg, color: pill.fg }}>
                          {pill.label}
                        </span>
                        {p.priority && <span style={{ ...badge('red'), fontSize: 10 }}>PRIORITY</span>}
                        <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                          {h.pct_of_account != null ? `${Number(h.pct_of_account).toFixed(1)}% of acct · ` : ''}
                          {h.unrealized_pct != null ? `${Number(h.unrealized_pct) >= 0 ? '+' : ''}${Number(h.unrealized_pct).toFixed(1)}%` : ''}
                          {p.action?.conviction != null ? ` · conv ${p.action.conviction}/10` : ''}
                        </span>
                      </div>
                      {p.action?.reason && (
                        <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 10, lineHeight: 1.45 }}>
                          {p.action.reason}
                        </div>
                      )}
                      <TradeChart ticker={p.ticker} entry={h.last} stop={p.action?.stop} target={p.action?.target} />
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <button
                          style={{ ...btnPrimary, padding: '5px 14px', fontSize: 12, opacity: brainBusyId === p.id ? 0.6 : 1 }}
                          disabled={brainBusyId === p.id}
                          onClick={() => brainApproveMut.mutate({ id: p.id, execute: isOrder })}
                        >
                          {brainBusyId === p.id ? 'Working…' : isOrder ? 'Approve & place order' : 'Approve — keep'}
                        </button>
                        <button
                          style={{ ...btnSecondary, padding: '5px 14px', fontSize: 12 }}
                          disabled={brainBusyId === p.id}
                          onClick={() => brainSkipMut.mutate(p.id)}
                        >
                          Skip
                        </button>
                        {brainMsg?.id === p.id && (
                          <span style={{ fontSize: 12, fontWeight: 600, color: brainMsg.ok ? '#34d399' : 'var(--danger)' }}>
                            {brainMsg.text}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* ── Deferred trades (held back by per-cycle budget / min-hold) ── */}
          {brainDeferred.length > 0 && (
            <div style={card}>
              <div style={sectionTitle}>🕒 Deferred to a later cycle ({brainDeferred.length})</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>
                Held back to avoid churn — surfaced automatically on the next brain cycle, or force them now
                with “Re-assess account”.
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {brainDeferred.map((d, i) => {
                  const pill = pillFor(d.kind)
                  return (
                    <div key={`${d.ticker}-${i}`} style={{
                      display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                      padding: '8px 12px', borderRadius: 7,
                      background: 'var(--surface-soft)', border: '1px dashed var(--surface-rule)',
                    }}>
                      <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>{d.ticker}</span>
                      <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 99, background: pill.bg, color: pill.fg }}>
                        {pill.label}
                      </span>
                      {d.conviction != null && (
                        <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>conv {d.conviction}/10</span>
                      )}
                      <span style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 'auto' }}>{d.reason}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* ── 7. Pending approvals card ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={sectionTitle}>⏳ Pending paper approval</div>
              <button style={{ ...btnSecondary, padding: '4px 12px', fontSize: 12 }} onClick={() => refetchPending()}>
                ↻ Refresh
              </button>
            </div>

            {!pending ? (
              <div style={{
                padding: '20px 0', textAlign: 'center',
                fontSize: 13, color: 'var(--ink-faint)',
              }}>
                No trade awaiting approval.
              </div>
            ) : (
              <div style={{
                border: '1px solid rgba(251,191,36,.4)', borderRadius: 8,
                padding: '14px 16px',
                background: 'rgba(251,191,36,.06)',
              }}>
                <div style={{ fontWeight: 700, color: '#fbbf24', marginBottom: 8, fontSize: 14 }}>
                  {pending.ticker || 'Trade'} — awaiting approval
                </div>
                <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 14 }}>
                  {[
                    ['Ticker', pending.ticker || '—'],
                    ['Action', String(pending.action ?? pending.side ?? '—').toUpperCase()],
                    ['Qty', String(pending.qty ?? pending.shares ?? '—')],
                    ['Price', pending.price != null ? `$${Number(pending.price).toFixed(2)}` : (pending.entry_price != null ? `$${Number(pending.entry_price).toFixed(2)}` : '—')],
                  ].map(([label, value]) => (
                    <div key={label as string}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>{label}</div>
                      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>{value as string}</div>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button
                    style={{ ...btnSecondary, color: 'var(--danger)', borderColor: 'rgba(239,68,68,.35)' }}
                    disabled={resolveMut.isPending}
                    onClick={() => resolveMut.mutate({ id: String(pending.id || ''), action: 'reject' })}
                  >
                    Reject
                  </button>
                  <button
                    style={btnPrimary}
                    disabled={resolveMut.isPending}
                    onClick={() => resolveMut.mutate({ id: String(pending.id || ''), action: 'approve' })}
                  >
                    Approve
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── 8. Thematic auto-picker HIL ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <div style={sectionTitle}>📊 Thematic auto-picker approvals</div>
              <button style={{ ...btnSecondary, padding: '4px 12px', fontSize: 12 }}
                onClick={() => { refetchThematicHil(); refetchThematicSignals() }}>
                ↺ Refresh
              </button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>
              Require your approval before thematic signals enter the portfolio. Optionally route approved signals to Fidelity.
            </div>

            {/* Enable toggle */}
            <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, cursor: 'pointer', marginBottom: 16 }}>
              <span style={{ fontSize: 13, color: 'var(--ink)' }}>Require approval before signals execute</span>
              <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', flexShrink: 0 }}>
                <input type="checkbox" checked={thematicHil.enabled}
                  onChange={e => setThematicHilForm({ ...thematicHil, enabled: e.target.checked })}
                  style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }} />
                <span onClick={() => setThematicHilForm({ ...thematicHil, enabled: !thematicHil.enabled })} style={{
                  display: 'inline-block', width: 36, height: 20, borderRadius: 10,
                  background: thematicHil.enabled ? 'var(--accent)' : 'var(--surface-raised)',
                  position: 'relative', cursor: 'pointer', transition: 'background .2s',
                }}>
                  <span style={{
                    position: 'absolute', top: 2, left: thematicHil.enabled ? 18 : 2,
                    width: 16, height: 16, borderRadius: 8, background: '#fff', transition: 'left .2s',
                  }} />
                </span>
              </span>
            </label>

            {thematicHil.enabled && (
              <>
                <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 14 }}>
                  <div>
                    <label style={labelStyle}>$ per trade</label>
                    <input type="number" min={10} max={50000} step={50}
                      style={{ ...inputStyle, width: 110 }}
                      value={thematicHil.dollar_amount}
                      onChange={e => setThematicHilForm({ ...thematicHil, dollar_amount: parseFloat(e.target.value) || 500 }) } />
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, justifyContent: 'flex-end', paddingBottom: 2 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: 'var(--ink)' }}>
                      <input type="checkbox" checked={thematicHil.sms_notify}
                        onChange={e => setThematicHilForm({ ...thematicHil, sms_notify: e.target.checked })} />
                      SMS notify when signals pending
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: '#fbbf24' }}>
                      <input type="checkbox" checked={thematicHil.fidelity_trade}
                        onChange={e => setThematicHilForm({ ...thematicHil, fidelity_trade: e.target.checked })} />
                      Route approved signals to Fidelity <span style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 4 }}>(real money)</span>
                    </label>
                  </div>
                </div>

                {thematicHil.fidelity_trade && (
                  <div style={{
                    padding: '8px 12px', borderRadius: 7, marginBottom: 14,
                    background: 'rgba(251,191,36,.08)', border: '1px solid rgba(251,191,36,.3)',
                    fontSize: 12, color: '#fbbf24',
                  }}>
                    ⚠ Approved signals will place real limit orders in your Fidelity account. Each approval requires explicit confirmation.
                  </div>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <button style={{ ...btnPrimary, padding: '7px 18px', fontSize: 12 }}
                    disabled={saveThematicHilMut.isPending}
                    onClick={() => saveThematicHilMut.mutate(thematicHil)}>
                    {saveThematicHilMut.isPending ? 'Saving…' : 'Save thematic HIL settings'}
                  </button>
                  {thematicSavedMsg && <span style={{ fontSize: 12, color: '#34d399' }}>✓ {thematicSavedMsg}</span>}
                </div>

                {/* Pending signals queue */}
                <div style={{ marginTop: 20 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 10 }}>
                    Pending signals ({pendingSignals.length})
                  </div>
                  {pendingSignals.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--ink-faint)', padding: '12px 0' }}>No signals pending approval.</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {pendingSignals.map(sig => (
                        <div key={sig.id} style={{
                          border: '1px solid var(--surface-rule)', borderRadius: 8,
                          padding: '12px 14px', background: 'var(--surface-soft)',
                          opacity: approvingId === sig.id ? 0.5 : 1,
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
                            <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--ink)' }}>{sig.ticker}</span>
                            {sig.theme && (
                              <span style={{ ...badge('grey'), fontSize: 10 }}>{sig.theme}</span>
                            )}
                            <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                              Score {sig.score ?? Math.round((sig.conviction ?? 7) * 9)}/100
                            </span>
                            {thematicHil.fidelity_trade && (
                              <span style={{ fontSize: 10, fontWeight: 700, color: '#fbbf24' }}>FIDELITY</span>
                            )}
                          </div>
                          {(sig.thesis || sig.catalyst) && (
                            <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 6, lineHeight: 1.45 }}>
                              {(sig.thesis || sig.catalyst || '').slice(0, 180)}
                            </div>
                          )}
                          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 10 }}>
                            Target +{sig.target_pct ?? 12}% · Stop -{sig.stop_pct ?? 6}% · Hold {sig.hold_days ?? 5}d
                          </div>
                          {sig.diversify?.cluster && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 11, marginBottom: 10 }}>
                              <span style={{ ...badge(sig.diversify.blocked ? 'red' : 'grey'), fontSize: 10 }}>
                                {sig.diversify.cluster}
                              </span>
                              <span style={{ color: 'var(--ink-faint)' }}>
                                {sig.diversify.cluster_names}/{sig.diversify.max_names} names
                                {sig.diversify.cluster_pct != null && ` · ${sig.diversify.cluster_pct}% of book`}
                                {` · N_eff ${sig.diversify.n_eff}`}
                              </span>
                              {sig.diversify.blocked ? (
                                <span style={{ color: '#ef4444', fontWeight: 600 }}>
                                  ⛔ {sig.diversify.cap_reason || 'cluster full'}
                                </span>
                              ) : sig.diversify.decay_mult < 1 ? (
                                <span style={{ color: '#fbbf24', fontWeight: 600 }}>
                                  sized ×{sig.diversify.decay_mult}
                                </span>
                              ) : null}
                            </div>
                          )}
                          <TradeChart ticker={sig.ticker}
                            stopPct={sig.stop_pct ?? 6} targetPct={sig.target_pct ?? 12} />
                          <div style={{ display: 'flex', gap: 8 }}>
                            <button
                              style={{ ...btnPrimary, padding: '5px 14px', fontSize: 12 }}
                              disabled={!!approvingId || approveSignalMut.isPending}
                              onClick={() => {
                                setApprovingId(sig.id)
                                approveSignalMut.mutate({
                                  id: sig.id,
                                  dollar: thematicHil.dollar_amount,
                                  fidelity: thematicHil.fidelity_trade,
                                })
                              }}
                            >
                              Approve ${thematicHil.dollar_amount.toFixed(0)}
                            </button>
                            <button
                              style={{ ...btnSecondary, padding: '5px 14px', fontSize: 12 }}
                              disabled={skipSignalMut.isPending}
                              onClick={() => skipSignalMut.mutate(sig.id)}
                            >
                              Skip
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          </>
          )}

          {/* ===== SETTINGS PANEL 2 (brain controls · protected accounts · save) ===== */}
          {tab === 'settings' && (
          <>
          {/* ── Holdings-Brain controls (status) ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
              <div style={sectionTitle}>🧠 Holdings-Brain controls</div>
              <button style={{ ...btnSecondary, padding: '4px 12px', fontSize: 12 }}
                onClick={() => refetchBrainHoldings()}>↺ Refresh</button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 14 }}>
              The brain takes control of your taxable account, decides keep/drop by thematic conviction, then
              trades. Protected accounts are never touched. These limits are set server-side (.env).
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '8px 24px', marginBottom: 14 }}>
              {[
                ['Managed equity positions', String(brainHoldings?.count ?? '—')],
                ['Protected / untouched', String(brainExcluded.length)],
                ['Live order requires', 'Step-up 2FA + compliance'],
                ['Trades capped', 'per cycle (anti-churn)'],
              ].map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--surface-rule)' }}>
                  <span style={{ color: 'var(--ink-muted)' }}>{k}</span>
                  <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{v}</span>
                </div>
              ))}
            </div>
            {brainExcluded.length > 0 && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>
                  🔒 Protected — never traded
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {brainExcluded.map((e, i) => (
                    <div key={`${e.symbol}-${i}`} style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      fontSize: 12, padding: '6px 10px', borderRadius: 6,
                      background: 'rgba(96,165,250,.06)', border: '1px solid rgba(96,165,250,.2)',
                    }}>
                      <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{e.symbol}</span>
                      <span style={{ color: 'var(--ink-faint)' }}>{e.account_name || e.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ── 9. Save / Discard ── */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
            <button
              style={{ ...btnPrimary, padding: '8px 32px', opacity: (!formDirty || saveMut.isPending) ? 0.6 : 1 }}
              disabled={!formDirty || saveMut.isPending}
              onClick={handleSave}
            >
              {saveMut.isPending ? 'Saving…' : 'Save preferences'}
            </button>
            {formDirty && (
              <button style={btnSecondary} onClick={handleDiscard}>
                Discard changes
              </button>
            )}
            {savedMsg && (
              <span style={{ fontSize: 13, color: '#34d399' }}>✓ {savedMsg}</span>
            )}
            {saveMut.isError && (
              <span style={{ fontSize: 13, color: 'var(--danger)' }}>
                Save failed: {(saveMut.error as Error)?.message || 'Try again.'}
              </span>
            )}
          </div>
          </>
          )}
        </>
      )}
    </div>
  )
}
