import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '@/api/client'
import { LoadingState } from '@/components/shared/LoadingState'

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

// ── Main component ────────────────────────────────────────────────────────────

export default function HILPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()

  const [disclosureChecked, setDisclosureChecked] = useState(false)
  const [form, setForm] = useState<HilPrefs>({ ...DEFAULT_PREFS })
  const [formDirty, setFormDirty] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [phone, setPhone] = useState('')
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

  // ── Sync from API ─────────────────────────────────────────────────
  useEffect(() => {
    if (user?.hil_prefs) {
      setForm({ ...DEFAULT_PREFS, ...user.hil_prefs })
    }
    if (user?.phone_number || user?.phone) {
      setPhone((user.phone_number || user.phone || '') as string)
    }
  }, [user])

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

  // ── Helpers ───────────────────────────────────────────────────────
  function setField(key: keyof HilPrefs, value: unknown) {
    setForm(f => ({ ...f, [key]: value }))
    setFormDirty(true)
  }

  function applyPreset(name: string) {
    const p = PRESETS[name]
    if (!p) return
    setForm(f => ({ ...f, ...p }))
    setFormDirty(true)
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
    if (user?.hil_prefs) {
      setForm({ ...DEFAULT_PREFS, ...user.hil_prefs })
      if (user.phone_number || user.phone) setPhone((user.phone_number || user.phone || '') as string)
    }
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
              🛡️ Human-In-the-Loop Approvals
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-muted)', maxWidth: 560 }}>
              Set your risk profile and require SMS approval before any real-money trade. These preferences are saved to your account.
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

        <details open style={{ marginBottom: 16 }}>
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
          {/* ── 3. Risk profile card ── */}
          <div style={card}>
            <div style={sectionTitle}>⚖️ Risk profile</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: -8, marginBottom: 14 }}>
              Pick a recommended preset, or fine-tune below (switches to Custom).
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

          {/* ── 7. Pending approvals card ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={sectionTitle}>⏳ Pending approval</div>
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

          {/* ── 8. Save / Discard ── */}
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
    </div>
  )
}
