import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { LoadingState } from '@/components/shared/LoadingState'

interface HilPending {
  id: string
  ticker: string
  action: string
  qty: number
  price: number
  created_at: string
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

interface HilSettings {
  hil_enabled?: boolean
  hil_timeout?: number
  hil_on_timeout?: string
  hil_notify?: string
  hil_min_rr?: number
  hil_pos_max?: number
  hil_pos_min?: number
  hil_max_pos?: number
  hil_daily_loss?: number
  hil_profile?: string
  sms_number?: string
  [key: string]: unknown
}

interface User {
  hil_disclosure_accepted?: boolean
  phone?: string
  [key: string]: unknown
}

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

const label: React.CSSProperties = {
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
                                  'rgba(148,163,184,.12)',
  color: color === 'green' ? '#34d399' :
         color === 'red'   ? '#ef4444'  :
                             '#94a3b8',
  border: `1px solid ${
    color === 'green' ? 'rgba(52,211,153,.3)' :
    color === 'red'   ? 'rgba(239,68,68,.3)'  :
                        'rgba(148,163,184,.3)'
  }`,
})

const PRESETS: Record<string, Partial<HilSettings>> = {
  conservative: { hil_min_rr: 2.0, hil_pos_max: 10, hil_pos_min: 2, hil_max_pos: 3, hil_daily_loss: 1.0, hil_profile: 'conservative' },
  balanced:     { hil_min_rr: 1.5, hil_pos_max: 25, hil_pos_min: 3, hil_max_pos: 5, hil_daily_loss: 2.0, hil_profile: 'balanced' },
  aggressive:   { hil_min_rr: 1.2, hil_pos_max: 40, hil_pos_min: 4, hil_max_pos: 8, hil_daily_loss: 3.5, hil_profile: 'aggressive' },
}

export default function HILPage() {
  const qc = useQueryClient()

  const [disclosureChecked, setDisclosureChecked] = useState(false)
  const [form, setForm] = useState<HilSettings>({})
  const [formDirty, setFormDirty] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [phone, setPhone] = useState('')
  const [phoneMsg, setPhoneMsg] = useState('')

  const { data: user, isLoading: userLoading } = useQuery<User>({
    queryKey: ['me'],
    queryFn: () => api.get<User>('/auth/me').then(r => r.data),
  })

  const { data: settings, isLoading: settingsLoading } = useQuery<HilSettings>({
    queryKey: ['settings'],
    queryFn: (): Promise<HilSettings> => api.get<HilSettings>('/settings').then(r => r.data),
  })

  useEffect(() => {
    if (settings) {
      setForm(settings)
      if (settings.sms_number) setPhone(settings.sms_number as string)
    }
  }, [settings])

  const { data: smsStatus } = useQuery<SmsStatus>({
    queryKey: ['sms-status'],
    queryFn: () => api.get<SmsStatus>('/paper/sms/status').then(r => r.data),
    refetchInterval: 30_000,
  })

  const { data: hilRaw, refetch: refetchPending } = useQuery<{ pending: boolean; trade?: HilPending & Record<string, unknown> }>({
    queryKey: ['hil-pending'],
    queryFn: () => api.get('/paper/hil/pending').then(r => r.data),
    refetchInterval: 10_000,
  })

  const disclosureMut = useMutation({
    mutationFn: () => api.post('/auth/me/hil-disclosure'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
  })

  const saveMut = useMutation({
    mutationFn: (data: Partial<HilSettings>) => api.patch('/settings', data).then(r => r.data),
    onSuccess: () => {
      setSavedMsg('Preferences saved.')
      setFormDirty(false)
      qc.invalidateQueries({ queryKey: ['settings'] })
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
    onSuccess: () => {
      setPhoneMsg('Test sent.')
      setTimeout(() => setPhoneMsg(''), 4000)
    },
    onError: () => {
      setPhoneMsg('Failed to send.')
      setTimeout(() => setPhoneMsg(''), 4000)
    },
  })

  function setField(key: keyof HilSettings, value: unknown) {
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
    const hilFields: Partial<HilSettings> = {}
    const keys: (keyof HilSettings)[] = [
      'hil_enabled','hil_timeout','hil_on_timeout','hil_notify',
      'hil_min_rr','hil_pos_max','hil_pos_min','hil_max_pos',
      'hil_daily_loss','hil_profile',
    ]
    for (const k of keys) {
      if (form[k] !== undefined) hilFields[k] = form[k]
    }
    if (phone) hilFields.sms_number = phone
    saveMut.mutate(hilFields)
  }

  function handleDiscard() {
    if (settings) {
      setForm(settings)
      if (settings.sms_number) setPhone(settings.sms_number as string)
    }
    setFormDirty(false)
  }

  const accepted = user?.hil_disclosure_accepted === true
  const pending = hilRaw?.pending ? hilRaw.trade : null

  if (userLoading || settingsLoading) return <LoadingState />

  const profile = (form.hil_profile as string | undefined) ?? settings?.hil_profile ?? 'balanced'

  return (
    <div id="panel-hil" style={{ padding: 24, maxWidth: 860 }}>

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
            <span style={badge(accepted ? 'green' : 'red')}>
              {accepted ? '✓ Disclosure accepted' : '⚠ Disclosure required'}
            </span>
            <span style={badge(form.hil_enabled ? 'green' : 'grey')}>
              {form.hil_enabled ? '● HIL active' : '○ HIL disabled'}
            </span>
            {smsStatus?.default_phone_set && (
              <span style={badge('green')}>📱 SMS configured</span>
            )}
          </div>
        </div>
      </div>

      {/* ── 2. Disclosure card ── */}
      <div style={card}>
        <div style={sectionTitle}>📋 Required HIL trading disclosure</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 10 }}>
          Version 1.0 · Effective date: 2025-01-01
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
            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 8 }}>
              Plain-English Summary
            </div>
            <p style={{ marginBottom: 10 }}>
              Human-in-the-Loop (HIL) mode lets you approve or reject individual trade signals before they are submitted to your broker. Enabling HIL does <strong>not</strong> guarantee profits, prevent losses, or constitute investment advice. You remain solely responsible for every trade executed under your account, whether you reviewed and approved it manually or allowed it to time out to an automatic action.
            </p>

            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', margin: '12px 0 8px' }}>
              Required Terms
            </div>
            <ol style={{ paddingLeft: 20, margin: 0 }}>
              <li style={{ marginBottom: 6 }}>
                <strong>No investment advice.</strong> Agentic Trader and its HIL feature do not provide investment, legal, or tax advice. All trade signals are generated algorithmically and may be wrong.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>You accept full responsibility.</strong> By enabling HIL, you acknowledge that you are the final decision-maker for each trade. Approving a trade means you have independently evaluated the signal and accept the associated risk.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Timeout behavior.</strong> If you do not respond to an approval request within the configured timeout window, the system will automatically apply your configured timeout action (auto-reject or auto-approve). You agree that this automated fallback is acceptable.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>SMS delivery is not guaranteed.</strong> Approval notifications are delivered via SMS, which may be delayed, filtered, or not received due to carrier or network issues. You must maintain a reliable mobile connection and monitor for messages during trading hours.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Real-money trades.</strong> HIL mode is designed for use with real-money brokerage accounts. Any approved trade may result in financial loss up to the full position size.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Risk parameters are not guarantees.</strong> Setting a minimum risk:reward ratio, position size limits, or daily loss limits reduces exposure but does not eliminate the possibility of losses exceeding those thresholds in gap or illiquid market conditions.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>System availability.</strong> Agentic Trader may be unavailable due to maintenance, outages, or third-party service failures. You should have independent means to monitor and close positions if the system is unreachable.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>No liability for automated actions.</strong> Agentic Trader and its operators bear no liability for trades executed as a result of your manual approval, your failure to respond within the timeout window, or any automated fallback action.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Regulatory compliance.</strong> You are responsible for complying with all applicable laws and regulations in your jurisdiction, including but not limited to pattern day trader (PDT) rules, wash-sale rules, and broker-specific margin requirements.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Data accuracy.</strong> Trade signals are based on market data that may be delayed, inaccurate, or incomplete. Prices shown in approval notifications are estimates and may differ from actual fill prices.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Revocation.</strong> You may disable HIL at any time. Disabling HIL does not affect trades already submitted to your broker or currently open positions.
              </li>
              <li style={{ marginBottom: 6 }}>
                <strong>Disclosure updates.</strong> This disclosure may be updated periodically. Continued use of the HIL feature after an update constitutes acceptance of the revised terms.
              </li>
            </ol>

            <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', margin: '12px 0 8px' }}>
              Required Acknowledgment
            </div>
            <p style={{ marginBottom: 0 }}>
              By accepting this disclosure, you confirm that you have read and understood all terms above, that you are of legal age to trade securities in your jurisdiction, that you understand trading involves substantial risk of loss, and that you are enabling HIL mode voluntarily with full knowledge that you — not Agentic Trader — bear ultimate responsibility for all trading decisions and their outcomes.
            </p>
          </div>
        </details>

        {!accepted ? (
          <div>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer', marginBottom: 14 }}>
              <input
                type="checkbox"
                checked={disclosureChecked}
                onChange={e => setDisclosureChecked(e.target.checked)}
                style={{ marginTop: 2, width: 15, height: 15, flexShrink: 0 }}
              />
              <span style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.5 }}>
                I have read and accept the HIL trading disclosure above. I understand that trading involves substantial risk of loss and that I am solely responsible for all trade decisions made under HIL mode.
              </span>
            </label>
            <button
              style={{
                ...btnPrimary,
                opacity: disclosureChecked && !disclosureMut.isPending ? 1 : 0.5,
                cursor: disclosureChecked ? 'pointer' : 'not-allowed',
              }}
              disabled={!disclosureChecked || disclosureMut.isPending}
              onClick={() => disclosureMut.mutate()}
            >
              {disclosureMut.isPending ? 'Saving…' : 'Accept HIL disclosure'}
            </button>
            {disclosureMut.isError && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--danger)' }}>
                Failed to save acceptance. Please try again.
              </div>
            )}
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
      {!accepted && (
        <div style={{
          ...card,
          textAlign: 'center', color: 'var(--ink-faint)', fontSize: 13, padding: '24px 20px',
        }}>
          Accept the HIL trading disclosure above before configuring approvals and risk settings.
        </div>
      )}

      {accepted && (
        <>
          {/* ── 3. Risk profile card ── */}
          <div style={card}>
            <div style={sectionTitle}>⚖️ Risk profile</div>

            {/* Preset cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
              {/* Conservative */}
              <div
                onClick={() => applyPreset('conservative')}
                style={{
                  border: `1px solid ${profile === 'conservative' ? 'var(--accent)' : 'var(--surface-rule)'}`,
                  background: profile === 'conservative' ? 'var(--surface-raised)' : 'var(--surface-soft)',
                  borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
                  boxShadow: profile === 'conservative' ? 'inset 0 0 0 1px var(--accent)' : 'none',
                  transition: 'border-color .15s, background-color .15s',
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
                  🛡️ Conservative
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 1 }}>Lower risk, fewer trades</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
                  R:R ≥ 2.0 · 10% max · 3 positions · 1% daily stop
                </div>
              </div>

              {/* Balanced */}
              <div
                onClick={() => applyPreset('balanced')}
                style={{
                  border: `1px solid ${profile === 'balanced' ? 'var(--accent)' : 'var(--surface-rule)'}`,
                  background: profile === 'balanced' ? 'var(--surface-raised)' : 'var(--surface-soft)',
                  borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
                  boxShadow: profile === 'balanced' ? 'inset 0 0 0 1px var(--accent)' : 'none',
                  transition: 'border-color .15s, background-color .15s',
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
                  ⚡ Balanced
                  <span style={{
                    fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em',
                    color: '#34d399', background: 'rgba(52,211,153,.12)', padding: '1px 6px',
                    borderRadius: 99,
                  }}>Recommended</span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 1 }}>Balanced risk/reward</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
                  R:R ≥ 1.5 · 25% max · 5 positions · 2% daily stop
                </div>
              </div>

              {/* Aggressive */}
              <div
                onClick={() => applyPreset('aggressive')}
                style={{
                  border: `1px solid ${profile === 'aggressive' ? 'var(--accent)' : 'var(--surface-rule)'}`,
                  background: profile === 'aggressive' ? 'var(--surface-raised)' : 'var(--surface-soft)',
                  borderRadius: 8, padding: '12px 14px', cursor: 'pointer',
                  boxShadow: profile === 'aggressive' ? 'inset 0 0 0 1px var(--accent)' : 'none',
                  transition: 'border-color .15s, background-color .15s',
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
                  🔥 Aggressive
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 1 }}>Higher risk, more trades</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
                  R:R ≥ 1.2 · 40% max · 8 positions · 3.5% daily stop
                </div>
              </div>
            </div>

            {/* Grid inputs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '12px 20px' }}>
              <div>
                <label style={label} htmlFor="hil-min-rr">Min Risk:Reward</label>
                <input id="hil-min-rr" type="number" step="0.1" min="0" style={inputStyle}
                  value={form.hil_min_rr ?? ''}
                  onChange={e => setField('hil_min_rr', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-pos-max">Position Max %</label>
                <input id="hil-pos-max" type="number" step="1" min="0" max="100" style={inputStyle}
                  value={form.hil_pos_max ?? ''}
                  onChange={e => setField('hil_pos_max', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-pos-min">Position Min %</label>
                <input id="hil-pos-min" type="number" step="0.5" min="0" max="100" style={inputStyle}
                  value={form.hil_pos_min ?? ''}
                  onChange={e => setField('hil_pos_min', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-max-pos">Max Positions</label>
                <input id="hil-max-pos" type="number" step="1" min="1" style={inputStyle}
                  value={form.hil_max_pos ?? ''}
                  onChange={e => setField('hil_max_pos', parseInt(e.target.value, 10))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-daily-loss">Daily Loss Limit %</label>
                <input id="hil-daily-loss" type="number" step="0.1" min="0" style={inputStyle}
                  value={form.hil_daily_loss ?? ''}
                  onChange={e => setField('hil_daily_loss', parseFloat(e.target.value))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-profile-input">Active profile</label>
                <input id="hil-profile-input" type="text" style={{ ...inputStyle, color: 'var(--ink-faint)' }}
                  value={form.hil_profile ?? ''}
                  readOnly />
              </div>
            </div>
          </div>

          {/* ── 4. Approvals & behavior card ── */}
          <div style={card}>
            <div style={sectionTitle}>🔔 Approvals &amp; behavior</div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 18 }}>
              <input
                type="checkbox"
                checked={!!form.hil_enabled}
                onChange={e => setField('hil_enabled', e.target.checked)}
                style={{ width: 16, height: 16 }}
              />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
                  Require SMS approval before real-money trades
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>
                  Each trade signal will wait for your SMS approval before being submitted to the broker.
                </div>
              </div>
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '12px 20px' }}>
              <div>
                <label style={label} htmlFor="hil-timeout">Approval Timeout (min)</label>
                <input id="hil-timeout" type="number" step="1" min="1" max="120" style={inputStyle}
                  value={form.hil_timeout ?? 15}
                  onChange={e => setField('hil_timeout', parseInt(e.target.value, 10))} />
              </div>
              <div>
                <label style={label} htmlFor="hil-on-timeout">On timeout</label>
                <select id="hil-on-timeout" style={{ ...inputStyle, cursor: 'pointer' }}
                  value={(form.hil_on_timeout as string | undefined) ?? 'reject'}
                  onChange={e => setField('hil_on_timeout', e.target.value)}>
                  <option value="reject">Reject (safe default)</option>
                  <option value="approve">Auto-approve</option>
                </select>
              </div>
              <div>
                <label style={label} htmlFor="hil-notify">Notify via</label>
                <select id="hil-notify" style={{ ...inputStyle, cursor: 'pointer' }}
                  value={(form.hil_notify as string | undefined) ?? 'sms'}
                  onChange={e => setField('hil_notify', e.target.value)}>
                  <option value="sms">SMS</option>
                  <option value="email">Email</option>
                  <option value="none">None</option>
                </select>
              </div>
            </div>
          </div>

          {/* ── 5. Phone / SMS card ── */}
          <div style={card}>
            <div style={sectionTitle}>📱 Phone / SMS</div>

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
                <label style={label} htmlFor="hil-phone">Mobile number</label>
                <input id="hil-phone" type="tel" style={inputStyle} placeholder="+1XXXXXXXXXX"
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

          {/* ── 6. Bridge status card ── */}
          <div style={card}>
            <div style={sectionTitle}>🌉 Bridge status</div>
            {smsStatus ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px 24px' }}>
                {[
                  ['Provider', smsStatus.provider],
                  ['Sendblue configured', smsStatus.sendblue_configured ? 'Yes' : 'No'],
                  ['Textbelt key set', smsStatus.textbelt_key_set ? 'Yes' : 'No'],
                  ['TextNow username set', smsStatus.textnow_username_set ? 'Yes' : 'No'],
                  ['TextNow SID set', smsStatus.textnow_sid_set ? 'Yes' : 'No'],
                  ['Phone configured', smsStatus.default_phone_set ? `Yes (${smsStatus.default_phone_masked})` : 'No'],
                  ['Playwright available', smsStatus.playwright_available ? 'Yes' : 'No'],
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
          </div>

          {/* ── 7. Pending approvals card ── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={sectionTitle}>⏳ Pending approvals</div>
              <button style={btnSecondary} onClick={() => refetchPending()}>
                ↻ Refresh
              </button>
            </div>

            {!pending ? (
              <div style={{
                padding: '20px 0', textAlign: 'center',
                fontSize: 13, color: 'var(--ink-faint)',
              }}>
                No pending approvals.
              </div>
            ) : (
              <div style={{
                border: '1px solid var(--surface-rule)', borderRadius: 8,
                padding: '14px 16px',
                background: 'var(--surface-soft)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>Ticker</div>
                      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>{pending.ticker}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>Action</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{String(pending.action ?? (pending as Record<string, unknown>).side ?? '—').toUpperCase()}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>Qty</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{pending.qty ?? (pending as Record<string, unknown>).shares ?? '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>Price</div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>
                        ${Number(pending.price ?? (pending as Record<string, unknown>).entry_price ?? 0).toFixed(2)}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <button
                      style={{ ...btnSecondary, color: 'var(--danger)', borderColor: 'rgba(239,68,68,.35)' }}
                      disabled={resolveMut.isPending}
                      onClick={() => resolveMut.mutate({ id: pending.id, action: 'reject' })}
                    >
                      Reject
                    </button>
                    <button
                      style={btnPrimary}
                      disabled={resolveMut.isPending}
                      onClick={() => resolveMut.mutate({ id: pending.id, action: 'approve' })}
                    >
                      Approve
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── 8. Save / Discard ── */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
            <button
              style={{ ...btnPrimary, opacity: (!formDirty || saveMut.isPending) ? 0.6 : 1 }}
              disabled={!formDirty || saveMut.isPending}
              onClick={handleSave}
            >
              {saveMut.isPending ? 'Saving…' : 'Save preferences'}
            </button>
            {formDirty && (
              <button style={btnSecondary} onClick={handleDiscard}>
                Discard
              </button>
            )}
            {savedMsg && (
              <span style={{ fontSize: 13, color: '#34d399' }}>✓ {savedMsg}</span>
            )}
            {saveMut.isError && (
              <span style={{ fontSize: 13, color: 'var(--danger)' }}>
                Save failed. Try again.
              </span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
