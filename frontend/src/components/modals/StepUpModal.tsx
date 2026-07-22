/* eslint-disable react-refresh/only-export-components */
/**
 * 2FA Step-Up Modal — shown when a protected action requires re-verification.
 * Trigger by calling openStepUp(resolve, reject) from the stepUpStore.
 *
 * Method-aware: on open it reads the user's active step-up method and adapts the
 * endpoint + copy (trading passcode / authenticator TOTP / emailed code). The
 * caller's `copy` is shown as context above the method-specific instruction.
 */
import { useEffect, useState } from 'react'
import { create } from 'zustand'
import { Lock } from 'lucide-react'
import { useAuth, useClerk } from '@clerk/react'
import api from '@/api/client'

const CLERK_ENABLED = !!import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

// ── Global store so any component can trigger step-up ─────────────────────────
// Cache the minted step-up token slightly under the server's 300s TTL so a
// just-verified token is still fresh when the follow-up trade request lands.
const STEP_UP_TTL_MS = 290_000

interface StepUpState {
  open: boolean
  method: string   // 'totp' | 'email' | 'passkey'
  title: string
  copy: string
  resolve: ((ok: boolean) => void) | null
  token: string
  tokenExp: number   // epoch ms when the cached token expires
  setToken: (t: string) => void
  openStepUp: (opts?: { title?: string; copy?: string }) => Promise<boolean>
  close: (ok: boolean) => void
}

export const useStepUpStore = create<StepUpState>((set, get) => ({
  open: false,
  method: 'totp',
  title: 'Confirm with 2FA',
  copy: '',
  resolve: null,
  token: '',
  tokenExp: 0,
  setToken(t) {
    set({ token: t, tokenExp: t ? Date.now() + STEP_UP_TTL_MS : 0 })
  },
  openStepUp(opts) {
    return new Promise<boolean>(resolve => {
      set({
        open: true,
        title: opts?.title ?? 'Confirm with 2FA',
        copy: opts?.copy ?? '',
        resolve,
      })
    })
  },
  close(ok) {
    get().resolve?.(ok)
    set({ open: false, resolve: null })
  },
}))

/** Imperatively trigger the 2FA modal from outside React (e.g. a mutationFn). */
export function openStepUp(opts?: { title?: string; copy?: string }): Promise<boolean> {
  return useStepUpStore.getState().openStepUp(opts)
}

/** Header bag carrying a fresh step-up token, or empty if none/expired. */
export function stepUpHeaders(): Record<string, string> {
  const { token, tokenExp } = useStepUpStore.getState()
  return token && Date.now() < tokenExp ? { 'X-Step-Up-Token': token } : {}
}

// ── Per-method config: endpoint + wording + input shape ───────────────────────
interface MethodCfg {
  endpoint: string
  instruction: string
  type: 'text' | 'password'
  placeholder: string
  maxLen: number
  numeric: boolean
}
const METHOD_CFG: Record<string, MethodCfg> = {
  totp: {
    endpoint: '/auth/2fa/step-up/totp',
    instruction: 'Enter the 6-digit code from your authenticator app.',
    type: 'text', placeholder: '000000', maxLen: 8, numeric: true,
  },
  passcode: {
    endpoint: '/auth/2fa/step-up/passcode',
    instruction: 'Enter your trading passcode.',
    type: 'password', placeholder: 'Trading passcode', maxLen: 64, numeric: false,
  },
  email: {
    endpoint: '/auth/2fa/step-up/email',
    instruction: 'Enter the code we emailed to your login address.',
    type: 'text', placeholder: '000000', maxLen: 8, numeric: true,
  },
}
const cfgFor = (m: string): MethodCfg => METHOD_CFG[m] ?? METHOD_CFG.totp

// ── Modal component ───────────────────────────────────────────────────────────
const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 95,
  background: 'rgba(0,0,0,.6)', backdropFilter: 'blur(4px)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
}
const dialog: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--surface-rule)',
  borderRadius: 14, boxShadow: '0 30px 80px rgba(0,0,0,.45)',
  width: 'min(380px, 100%)', overflow: 'hidden',
}
const btnPrimary: React.CSSProperties = {
  padding: '8px 18px', background: 'var(--accent)', color: '#fff',
  border: 'none', borderRadius: 6, fontWeight: 600, fontSize: 13, cursor: 'pointer',
}
const btnSecondary: React.CSSProperties = {
  padding: '8px 18px', background: 'var(--surface-raised)', color: 'var(--ink)',
  border: '1px solid var(--surface-rule)', borderRadius: 6, fontWeight: 600, fontSize: 13, cursor: 'pointer',
}

// ── Clerk (Google re-auth) step-up button ────────────────────────────────────
// Uses Clerk hooks, so it must only mount when ClerkProvider is present (i.e. a
// publishable key is configured). Renders a Google verify button instead of a
// code field: sign in with Clerk if needed, then exchange the session token for a
// step-up token at /auth/2fa/step-up/clerk.
function ClerkStepUpButton({ onDone, onError }: { onDone: () => void; onError: (m: string) => void }) {
  const { isLoaded, isSignedIn, getToken } = useAuth()
  const clerk = useClerk()
  const [busy, setBusy] = useState(false)

  const verify = async () => {
    onError('')
    if (!isLoaded) return
    if (!isSignedIn) {
      // Open Clerk's Google sign-in; the user re-verifies, then clicks again.
      clerk.openSignIn({})
      onError('Signed in with Google? Click “Verify with Google” again to finish.')
      return
    }
    setBusy(true)
    try {
      const token = await getToken()
      if (!token) { onError('Could not get a Clerk token — try signing in again.'); return }
      const res = await api.post('/auth/2fa/step-up/clerk', { clerk_token: token })
      const tok = (res.data as { step_up_token?: string })?.step_up_token
      useStepUpStore.getState().setToken(tok ?? '')
      onDone()
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      onError(err?.response?.data?.detail ?? err?.message ?? 'Clerk verification failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <button onClick={verify} disabled={busy || !isLoaded}
      style={{
        width: '100%', padding: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
        background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)', borderRadius: 8,
        color: 'var(--ink)', fontWeight: 600, fontSize: 14, cursor: 'pointer',
      }}>
      <span aria-hidden style={{ fontSize: 16 }}>🔵</span>
      {busy ? 'Verifying…' : isSignedIn ? 'Verify with Google' : 'Sign in with Google'}
    </button>
  )
}

export function StepUpModal() {
  const { open, title, copy, close } = useStepUpStore()
  const [code, setCode] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)
  const [method, setMethod] = useState('totp')

  // On open, read the active step-up method so we hit the right endpoint and
  // show the right instruction. For email, send the one-time code immediately.
  useEffect(() => {
    if (!open) return
    setCode(''); setMsg(''); setMethod('totp')
    let cancelled = false
    api.get<{ method?: string }>('/auth/2fa/status')
      .then(r => {
        if (cancelled) return
        const m = r.data?.method || 'totp'
        // 'clerk' has no code input (Google re-auth button); other methods must
        // be in METHOD_CFG or we fall back to totp.
        const use = m === 'clerk' ? 'clerk' : (METHOD_CFG[m] ? m : 'totp')
        setMethod(use)
        if (use === 'email') api.post('/auth/2fa/step-up/email/send').catch(() => {})
      })
      .catch(() => { if (!cancelled) setMethod('totp') })
    return () => { cancelled = true }
  }, [open])

  if (!open) return null

  const cfg = cfgFor(method)

  const submit = async () => {
    if (!code.trim()) { setMsg('Enter the code.'); return }
    setLoading(true); setMsg('')
    try {
      const res = await api.post(cfg.endpoint, { code: code.trim() })
      const tok = (res.data as { step_up_token?: string })?.step_up_token
      useStepUpStore.getState().setToken(tok ?? '')
      setCode('')
      close(true)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setMsg(err?.response?.data?.detail ?? err?.message ?? 'Invalid code.')
    } finally {
      setLoading(false)
    }
  }

  const resendEmail = async () => {
    setMsg('')
    try { await api.post('/auth/2fa/step-up/email/send'); setMsg('Code re-sent.') }
    catch { setMsg('Could not resend code.') }
  }

  const cancel = () => { setCode(''); setMsg(''); close(false) }

  return (
    <div style={overlay} onClick={e => { if (e.target === e.currentTarget) cancel() }}>
      <div style={dialog}>
        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Lock size={18} strokeWidth={2} color="var(--accent)" />
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)' }}>{title}</div>
        </div>
        <div style={{ padding: '22px 24px' }}>
          {copy && (
            <div style={{ fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.5, marginBottom: 8 }}>{copy}</div>
          )}
          <div style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.5, marginBottom: 14, fontWeight: 500 }}>
            {method === 'clerk' ? 'Re-verify your identity with Google to authorize this trade.' : cfg.instruction}
          </div>
          {method === 'clerk' ? (
            CLERK_ENABLED
              ? <ClerkStepUpButton onDone={() => { setCode(''); close(true) }} onError={setMsg} />
              : <div style={{ fontSize: 13, color: '#ef4444' }}>Clerk isn’t configured in this build.</div>
          ) : (
          <input
            type={cfg.type}
            {...(cfg.numeric ? { inputMode: 'numeric' as const } : {})}
            autoComplete={cfg.type === 'password' ? 'current-password' : 'one-time-code'}
            maxLength={cfg.maxLen}
            placeholder={cfg.placeholder}
            value={code}
            onChange={e => setCode(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit() }}
            autoFocus
            style={{
              width: '100%', textAlign: 'center',
              letterSpacing: cfg.numeric ? '.35em' : '.12em',
              fontSize: 24, fontFamily: 'monospace', padding: 12,
              background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)',
              borderRadius: 8, color: 'var(--ink)', boxSizing: 'border-box',
            }}
          />
          )}
          {method === 'email' && (
            <button onClick={resendEmail}
              style={{ marginTop: 8, background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 12, padding: 0 }}>
              Resend code
            </button>
          )}
          {msg && <div style={{ fontSize: 12, color: '#ef4444', minHeight: 16, marginTop: 8 }}>{msg}</div>}
        </div>
        <div style={{ padding: '14px 24px', borderTop: '1px solid var(--surface-rule)', display: 'flex', gap: 10, justifyContent: 'flex-end', background: 'var(--surface-soft)' }}>
          <button style={btnSecondary} onClick={cancel}>Cancel</button>
          {method !== 'clerk' && (
            <button style={btnPrimary} onClick={submit} disabled={loading}>
              {loading ? 'Checking…' : 'Authorize'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
