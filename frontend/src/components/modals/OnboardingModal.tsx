import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { updateName, updatePhone } from '@/api/auth'
import { useAuthStore } from '@/store/auth'

const TOTAL_STEPS = 6

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 90,
  background: 'rgba(0,0,0,.55)', backdropFilter: 'blur(4px)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
}
const dialog: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--surface-rule)',
  borderRadius: 14, boxShadow: '0 30px 80px rgba(0,0,0,.4)',
  width: 'min(540px, 100%)', maxHeight: 'calc(100vh - 48px)',
  overflow: 'hidden', display: 'flex', flexDirection: 'column',
}
const card: React.CSSProperties = {
  padding: '12px 14px', background: 'var(--surface-soft)',
  border: '1px solid var(--surface-rule)', borderRadius: 8,
}
const inputStyle: React.CSSProperties = {
  flex: 1, padding: '10px 12px', background: 'var(--surface-soft)',
  border: '1px solid var(--surface-rule)', borderRadius: 6,
  color: 'var(--ink)', fontSize: 14, fontFamily: 'inherit', width: '100%',
}
const btnPrimary: React.CSSProperties = {
  padding: '8px 18px', background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 6, fontWeight: 600, fontSize: 13,
  cursor: 'pointer',
}
const btnSecondary: React.CSSProperties = {
  padding: '8px 18px', background: 'var(--surface-raised)',
  color: 'var(--ink)', border: '1px solid var(--surface-rule)',
  borderRadius: 6, fontWeight: 600, fontSize: 13, cursor: 'pointer',
}

interface Props { onClose: () => void }

export function OnboardingModal({ onClose }: Props) {
  const { user, setUser } = useAuthStore()
  const qc = useQueryClient()
  const [step, setStep] = useState(1)
  const [nameVal, setNameVal] = useState(user?.name ?? '')
  const [nameStatus, setNameStatus] = useState('')
  const [termsOk, setTermsOk] = useState(false)
  const [privacyOk, setPrivacyOk] = useState(false)
  const [riskOk, setRiskOk] = useState(false)
  const [legalStatus, setLegalStatus] = useState('All three acknowledgments are required before continuing.')
  const [phone, setPhone] = useState(user?.phone ?? '')
  const [phoneStatus, setPhoneStatus] = useState('')
  const [phoneSending, setPhoneSending] = useState(false)
  const [err, setErr] = useState('')

  const legalAccepted = termsOk && privacyOk && riskOk

  const saveName = async () => {
    const name = nameVal.trim()
    if (!name) { setNameStatus('Enter a name first.'); return }
    try {
      const r = await updateName(name)
      const updated = (r as { data?: { name?: string } }).data
      if (user) setUser({ ...user, name: (updated as { name?: string })?.name ?? name })
      setNameStatus('✓ Saved.')
    } catch {
      setNameStatus('Save failed.')
    }
  }

  const sendPhone = async () => {
    if (!phone.trim()) { setPhoneStatus('Enter a phone number first.'); return }
    setPhoneSending(true)
    setPhoneStatus('Sending test message…')
    try {
      const r = await updatePhone(phone.trim())
      const d = (r as { data?: { sms_verified?: boolean; phone_number?: string } }).data
      if (d?.sms_verified) {
        setPhoneStatus(`✓ Test sent to ${d.phone_number}. Check your messages.`)
        if (user) setUser({ ...user, phone: d.phone_number, sms_verified: true })
      } else {
        setPhoneStatus('Saved, but test send failed. You can still continue.')
      }
    } catch {
      setPhoneStatus('Error sending test. Check the number and try again.')
    } finally {
      setPhoneSending(false)
    }
  }

  const completeMutation = useMutation({
    mutationFn: () => api.post('/auth/me/complete-onboarding', {
      terms_accepted: true, privacy_accepted: true, risk_acknowledged: true,
    }),
    onSuccess: () => {
      if (user) setUser({ ...user, onboarding_completed: true, legal_accepted: true })
      qc.invalidateQueries({ queryKey: ['auth', 'me'] })
      onClose()
    },
    onError: () => setErr('Could not save acknowledgments. Try again.'),
  })

  const next = () => {
    if (step === 2 && !legalAccepted) {
      setLegalStatus('All three acknowledgments are required before continuing.')
      return
    }
    if (step === TOTAL_STEPS) {
      if (!legalAccepted) { setStep(2); return }
      completeMutation.mutate()
      return
    }
    setStep(s => s + 1)
  }

  const back = () => setStep(s => Math.max(1, s - 1))

  return (
    <div style={overlay}>
      <div style={dialog}>
        {/* Header */}
        <div style={{ padding: '22px 26px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <img src="/app/agentic-trader-icon.png" alt="Logo"
            style={{ width: 32, height: 32, borderRadius: 8 }}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>Welcome to Agentic Trader</div>
            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>Step {step} of {TOTAL_STEPS}</div>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px 26px' }}>
          {step === 1 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>
                Welcome, <strong style={{ color: 'var(--ink)' }}>{user?.name || user?.email}</strong>. You're logged in via Cloudflare Access — your identity is verified.
              </div>
              <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
                <span style={{ color: 'var(--ink-faint)' }}>Signed in as</span>
                <span style={{ color: 'var(--ink-muted)', fontWeight: 600, fontFamily: 'monospace' }}>{user?.email}</span>
                <span style={{ marginLeft: 'auto', padding: '2px 8px', borderRadius: 99, fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em', background: 'var(--surface-raised)', color: 'var(--ink-faint)' }}>{user?.role}</span>
              </div>
              <div style={{ marginTop: 16 }}>
                <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Display name</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input value={nameVal} onChange={e => setNameVal(e.target.value)} placeholder="Your name" style={inputStyle} />
                  <button style={btnSecondary} onClick={saveName}>Save</button>
                </div>
                {nameStatus && <div style={{ marginTop: 8, fontSize: 12, color: nameStatus.startsWith('✓') ? '#22c55e' : '#ef4444' }}>{nameStatus}</div>}
              </div>
            </div>
          )}

          {step === 2 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>
                Before using Agentic Trader, confirm the legal and risk terms.
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label style={{ ...card, display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
                  <input type="checkbox" checked={termsOk} onChange={e => { setTermsOk(e.target.checked); setLegalStatus('') }} style={{ marginTop: 3 }} />
                  <span style={{ fontSize: 12.5, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                    I have read and agree to the <a href="/app/terms" style={{ color: 'var(--accent)', fontWeight: 700, textDecoration: 'none' }}>Terms of Service</a>.
                  </span>
                </label>
                <label style={{ ...card, display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
                  <input type="checkbox" checked={privacyOk} onChange={e => { setPrivacyOk(e.target.checked); setLegalStatus('') }} style={{ marginTop: 3 }} />
                  <span style={{ fontSize: 12.5, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                    I have read and agree to the <a href="/app/privacy" style={{ color: 'var(--accent)', fontWeight: 700, textDecoration: 'none' }}>Privacy Policy</a>.
                  </span>
                </label>
                <label style={{ ...card, display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer', background: 'rgba(245,158,11,.07)', border: '1px solid rgba(245,158,11,.26)' }}>
                  <input type="checkbox" checked={riskOk} onChange={e => { setRiskOk(e.target.checked); setLegalStatus('') }} style={{ marginTop: 3 }} />
                  <span style={{ fontSize: 12.5, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                    I understand Agentic Trader is not financial advice, trading involves risk, and I am responsible for every trading decision and approval.
                  </span>
                </label>
              </div>
              <div style={{ marginTop: 12, fontSize: 12, lineHeight: 1.5, color: legalAccepted ? '#22c55e' : 'var(--ink-faint)' }}>
                {legalAccepted ? 'Accepted. These acknowledgments will be saved to your account.' : legalStatus}
              </div>
            </div>
          )}

          {step === 3 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>
                The dashboard has three views you'll use most:
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {[
                  { label: 'Dashboard', desc: 'Live market feed, top movers, AI signals.' },
                  { label: 'Paper Trading', desc: 'Shared simulated runner. Everyone sees the same positions and equity curve.' },
                  { label: 'Real Broker', desc: 'Your own Fidelity / Webull positions. Private to you.' },
                ].map(item => (
                  <div key={item.label} style={card}>
                    <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 2 }}>{item.label}</div>
                    <div style={{ fontSize: 12, color: 'var(--ink-faint)', lineHeight: 1.5 }}>{item.desc}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {step === 4 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>
                What you can do with your role:
              </div>
              {user?.role === 'admin' ? (
                <div style={{ padding: 14, background: 'rgba(34,197,94,.06)', border: '1px solid rgba(34,197,94,.25)', borderRadius: 8, marginBottom: 10 }}>
                  <div style={{ fontWeight: 700, fontSize: 13, color: '#22c55e', marginBottom: 6 }}>Admin — full control</div>
                  <div style={{ fontSize: 12, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                    Start/stop the shared paper runner, approve or reject HIL trades, edit autostart, send SMS tests, manage user roles.
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ padding: 14, background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 8, marginBottom: 10 }}>
                    <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 6 }}>User — read access</div>
                    <div style={{ fontSize: 12, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                      View all dashboards, paper trading results, run analysis. Cannot start/stop the paper runner or approve trades.
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--ink-faint)', lineHeight: 1.5 }}>
                    Need broader access? Email <a href="mailto:support@agentictrader.org" style={{ color: 'var(--accent)', fontWeight: 600, textDecoration: 'none' }}>support@agentictrader.org</a>.
                  </div>
                </>
              )}
            </div>
          )}

          {step === 5 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>
                <strong style={{ color: 'var(--ink)' }}>SMS alerts</strong> — get a text when the system fires a trade or asks for approval.
              </div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Mobile number</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input type="tel" value={phone} onChange={e => setPhone(e.target.value)} placeholder="+1 (555) 123-4567" style={inputStyle} />
                <button style={{ ...btnPrimary, whiteSpace: 'nowrap', padding: '0 16px' }} onClick={sendPhone} disabled={phoneSending}>
                  {phoneSending ? 'Sending…' : 'Send test'}
                </button>
              </div>
              {phoneStatus && (
                <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.5, color: phoneStatus.startsWith('✓') ? '#22c55e' : phoneStatus.startsWith('Error') ? '#ef4444' : '#f59e0b' }}>
                  {phoneStatus}
                </div>
              )}
              <div style={{ marginTop: 12, fontSize: 11, color: 'var(--ink-faint)', lineHeight: 1.5 }}>
                We send one test message immediately. If it arrives, your number is verified. This step is optional — you can skip it.
              </div>
              <div style={{ marginTop: 18, ...card }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>Text back any of these</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: 12, fontFamily: 'monospace', color: 'var(--ink-muted)' }}>
                  {[['STATUS', 'runner state + equity'], ['POSITIONS', 'open positions'], ['HIL', 'pending trade approval status'], ['WHOAMI', 'your account info'], ['STOP', 'unsubscribe'], ['HELP', 'list commands again']].map(([cmd, desc]) => (
                    <>
                      <span key={cmd} style={{ color: 'var(--accent)', fontWeight: 700 }}>{cmd}</span>
                      <span key={desc}>{desc}</span>
                    </>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 6 && (
            <div>
              <div style={{ fontSize: 14, color: 'var(--ink-muted)', lineHeight: 1.55, marginBottom: 14 }}>Quick tips:</div>
              <ul style={{ fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.7, paddingLeft: 18, margin: 0 }}>
                <li>Press <kbd style={{ padding: '1px 6px', background: 'var(--surface-raised)', border: '1px solid var(--surface-rule)', borderRadius: 3, fontSize: 11, fontFamily: 'monospace' }}>g</kbd> then a letter to jump pages (<code>g d</code> = dashboard, <code>g k</code> = broker).</li>
                <li>The sidebar pill shows your role at all times.</li>
                <li>Trade approval texts always link to the dashboard — only admins can approve or reject inside the app.</li>
                <li>Settings → API tokens are masked. Only admins can change them.</li>
              </ul>
              <div style={{ marginTop: 18, padding: '12px 14px', background: 'rgba(59,130,246,.08)', border: '1px solid rgba(59,130,246,.25)', borderRadius: 8, fontSize: 12, color: 'var(--ink-muted)', lineHeight: 1.55 }}>
                You can re-open this tour anytime by clicking your email in the sidebar.
              </div>
              {err && <div style={{ marginTop: 10, fontSize: 12, color: '#ef4444' }}>{err}</div>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '16px 22px', borderTop: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-soft)' }}>
          {step > 1 && <button style={btnSecondary} onClick={back}>Back</button>}
          <div style={{ flex: 1, display: 'flex', gap: 4, justifyContent: 'center' }}>
            {Array.from({ length: TOTAL_STEPS }, (_, i) => (
              <span key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: step === i + 1 ? 'var(--accent)' : 'var(--surface-rule)', display: 'inline-block', transition: 'background .15s' }} />
            ))}
          </div>
          <button
            style={{ ...btnPrimary, opacity: (step === 2 && !legalAccepted) ? .55 : 1, cursor: (step === 2 && !legalAccepted) ? 'not-allowed' : 'pointer' }}
            onClick={next}
            disabled={(step === 2 && !legalAccepted) || completeMutation.isPending}
          >
            {step === TOTAL_STEPS ? (completeMutation.isPending ? 'Saving…' : 'Accept & start') : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}
