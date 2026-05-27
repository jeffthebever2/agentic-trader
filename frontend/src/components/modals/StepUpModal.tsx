/**
 * 2FA Step-Up Modal — shown when a protected action requires re-verification.
 * Trigger by calling openStepUp(resolve, reject) from the stepUpStore.
 */
import { useState } from 'react'
import { create } from 'zustand'
import { Lock } from 'lucide-react'
import api from '@/api/client'

// ── Global store so any component can trigger step-up ─────────────────────────
interface StepUpState {
  open: boolean
  method: string   // 'totp' | 'email' | 'passkey'
  title: string
  copy: string
  resolve: ((ok: boolean) => void) | null
  openStepUp: (opts?: { title?: string; copy?: string }) => Promise<boolean>
  close: (ok: boolean) => void
}

export const useStepUpStore = create<StepUpState>((set, get) => ({
  open: false,
  method: 'totp',
  title: 'Confirm with 2FA',
  copy: 'Enter the 6-digit code from your authenticator app.',
  resolve: null,
  openStepUp(opts) {
    return new Promise<boolean>(resolve => {
      set({
        open: true,
        title: opts?.title ?? 'Confirm with 2FA',
        copy: opts?.copy ?? 'Enter the 6-digit code from your authenticator app.',
        resolve,
      })
    })
  },
  close(ok) {
    get().resolve?.(ok)
    set({ open: false, resolve: null })
  },
}))

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

export function StepUpModal() {
  const { open, title, copy, close } = useStepUpStore()
  const [code, setCode] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  if (!open) return null

  const submit = async () => {
    if (!code.trim()) { setMsg('Enter the code.'); return }
    setLoading(true); setMsg('')
    try {
      await api.post('/auth/2fa/step-up/totp', { code: code.trim() })
      setCode('')
      close(true)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      setMsg(err?.response?.data?.detail ?? err?.message ?? 'Invalid code.')
    } finally {
      setLoading(false)
    }
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
          <div style={{ fontSize: 13, color: 'var(--ink-muted)', lineHeight: 1.5, marginBottom: 14 }}>{copy}</div>
          <input
            type="text"
            inputMode="numeric"
            maxLength={8}
            placeholder="000000"
            value={code}
            onChange={e => setCode(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit() }}
            autoFocus
            style={{
              width: '100%', textAlign: 'center', letterSpacing: '.35em',
              fontSize: 24, fontFamily: 'monospace', padding: 12,
              background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)',
              borderRadius: 8, color: 'var(--ink)', boxSizing: 'border-box',
            }}
          />
          {msg && <div style={{ fontSize: 12, color: '#ef4444', minHeight: 16, marginTop: 8 }}>{msg}</div>}
        </div>
        <div style={{ padding: '14px 24px', borderTop: '1px solid var(--surface-rule)', display: 'flex', gap: 10, justifyContent: 'flex-end', background: 'var(--surface-soft)' }}>
          <button style={btnSecondary} onClick={cancel}>Cancel</button>
          <button style={btnPrimary} onClick={submit} disabled={loading}>
            {loading ? 'Checking…' : 'Authorize'}
          </button>
        </div>
      </div>
    </div>
  )
}
