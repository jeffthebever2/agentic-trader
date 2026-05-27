import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { useThemeStore } from '@/store/theme'
import { useAuthStore } from '@/store/auth'

// ── Types ─────────────────────────────────────────────────────────────────────

interface MeUser {
  email: string
  name: string
  role: string
  phone?: string
  phone_number?: string
  sms_verified?: boolean
  sms_opted_out?: boolean
  sms_service?: string
  is_admin?: boolean
}

interface TwoFAStatus {
  method: string | null
  enabled: boolean
  verified: boolean
  email_enabled?: boolean
  totp_enabled?: boolean
  passkeys?: Array<{ id: string; name: string }>
}

interface VerificationCheck {
  label?: string
  id?: string
  status: 'pass' | 'fail' | 'warn'
  detail?: string
  note?: string
}

interface OpenRouterUsage {
  date?: string
  limit?: number
  requests?: number
  remaining?: number
  percent?: number
  by_source?: Record<string, number>
}

interface SettingsData {
  openrouter_usage?: OpenRouterUsage
  compliance?: {
    live_trading_hard_blocked?: boolean
    blocked_actions?: string[]
  }
  paths?: Record<string, string>
  [key: string]: unknown
}

// ── Toggle switch component ───────────────────────────────────────────────────

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', cursor: 'pointer', userSelect: 'none' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }}
      />
      <span style={{
        display: 'inline-block', width: 44, height: 24,
        background: checked ? 'var(--accent, #0891b2)' : 'var(--surface-raised, #334155)',
        borderRadius: 12, transition: 'background .2s', position: 'relative',
      }}>
        <span style={{
          position: 'absolute', top: 2, left: checked ? 22 : 2,
          width: 20, height: 20, borderRadius: 10,
          background: '#fff', transition: 'left .2s',
        }} />
      </span>
    </label>
  )
}

// ── Card wrapper ──────────────────────────────────────────────────────────────

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="card" style={{ padding: 20, ...style }}>
      {children}
    </div>
  )
}

// ── Inline block ──────────────────────────────────────────────────────────────

function InlineBlock({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      borderRadius: 6, border: '1px solid var(--surface-rule, #1e293b)',
      background: 'rgba(2,6,23,.3)', padding: 16, ...style,
    }}>
      {children}
    </div>
  )
}

// ── API key row ───────────────────────────────────────────────────────────────

function ApiKeyRow({ envKey, label, desc, isSet, value, onChange }: {
  envKey: string; label: string; desc: string; isSet: boolean
  value: string; onChange: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <span style={{
        width: 8, height: 8, borderRadius: 4, flexShrink: 0,
        background: isSet ? '#10b981' : 'var(--surface-raised)',
        display: 'inline-block',
      }} title={isSet ? 'Key is set' : 'Not configured'} />
      <div style={{ width: 160, flexShrink: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink-muted, #cbd5e1)' }}>{label}</div>
        {desc && <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>{desc}</div>}
      </div>
      <div style={{ flex: 1, display: 'flex', gap: 8, minWidth: 0 }}>
        <input
          className="input"
          id={`setting-${envKey}`}
          type={show ? 'text' : 'password'}
          placeholder={isSet ? 'Set — enter new value to replace' : 'Enter API key…'}
          value={value}
          onChange={e => onChange(e.target.value)}
          autoComplete="new-password"
          style={{ flex: 1, fontFamily: 'monospace', fontSize: 12, minWidth: 0 }}
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setShow(s => !s)}
          title="Show/hide"
          style={{ padding: '0 10px', flexShrink: 0 }}
        >
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </button>
      </div>
      {isSet
        ? <span style={{ fontSize: 12, color: '#10b981', flexShrink: 0 }}>✓</span>
        : <span style={{ fontSize: 12, color: 'var(--ink-faint)', flexShrink: 0 }}>—</span>}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const qc = useQueryClient()
  const { mode, toggle: toggleTheme } = useThemeStore()
  const { user: storeUser, isAdmin } = useAuthStore()

  // ── Save banner ──
  const [savedBanner, setSavedBanner] = useState(false)

  // ── /api/auth/me ──
  const { data: me, refetch: refetchMe } = useQuery<MeUser>({
    queryKey: ['auth-me'],
    queryFn: () => api.get<MeUser>('/auth/me').then(r => r.data),
    staleTime: 30_000,
  })

  // ── /api/settings ──
  const { data: settings } = useQuery<SettingsData>({
    queryKey: ['settings'],
    queryFn: () => api.get<SettingsData>('/settings').then(r => r.data),
    staleTime: 60_000,
    enabled: isAdmin(),
  })

  // ── /api/auth/2fa/status ──
  const { data: twofa, refetch: refetch2FA } = useQuery<TwoFAStatus>({
    queryKey: ['2fa-status'],
    queryFn: () => api.get<TwoFAStatus>('/auth/2fa/status').then(r => r.data),
    staleTime: 30_000,
  })

  // ── /api/live-verification ──
  const {
    data: liveVerif,
    refetch: refetchLive,
    isFetching: liveFetching,
  } = useQuery<{ checks: VerificationCheck[] }>({
    queryKey: ['live-verification'],
    queryFn: () => api.get('/live-verification').then(r => r.data),
    staleTime: 30_000,
  })

  // ════════════════════════════════════════════════════════════════
  // Section 1: Account
  // ════════════════════════════════════════════════════════════════
  const [displayName, setDisplayName] = useState('')
  const [nameMsg, setNameMsg] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    if (me?.name) setDisplayName(me.name)
  }, [me?.name])

  const saveName = useMutation({
    mutationFn: (name: string) => api.post('/auth/me/name', { name }).then(r => r.data),
    onSuccess: (data) => {
      setNameMsg({ text: '✓ Saved.', ok: true })
      if (data?.name) setDisplayName(data.name)
      qc.invalidateQueries({ queryKey: ['auth-me'] })
    },
    onError: (e: Error) => setNameMsg({ text: e.message, ok: false }),
  })

  // ════════════════════════════════════════════════════════════════
  // Section 2: SMS alerts
  // ════════════════════════════════════════════════════════════════
  const [phone, setPhone] = useState('')
  const [phoneMsg, setPhoneMsg] = useState<{ text: string; ok: boolean | null } | null>(null)
  const [verifyingPhone, setVerifyingPhone] = useState(false)
  const [savingPhone, setSavingPhone] = useState(false)
  const [smsOptOut, setSmsOptOut] = useState(false)

  useEffect(() => {
    if (me) {
      setPhone(me.phone_number || me.phone || '')
      setSmsOptOut(!!me.sms_opted_out)
    }
  }, [me])

  const handleVerifyPhone = async () => {
    if (!phone.trim()) { setPhoneMsg({ text: 'Enter a phone number first.', ok: false }); return }
    setVerifyingPhone(true)
    setPhoneMsg({ text: 'Verifying contact via Sendblue (no message sent)…', ok: null })
    try {
      const r = await api.post<{ reachable?: boolean; service?: string; phone_number?: string; error?: string }>(
        '/auth/me/phone/verify', { phone: phone.trim(), send_test: false }
      ).then(res => res.data)
      if (r.reachable) {
        setPhoneMsg({ text: `✓ Reachable via ${r.service || 'SMS'} — ${r.phone_number}.`, ok: true })
      } else {
        setPhoneMsg({ text: `Could not verify: ${r.error || 'number not reachable'}.`, ok: null })
      }
      refetchMe()
    } catch (e: unknown) {
      setPhoneMsg({ text: `Error: ${(e as Error).message || 'request failed'}`, ok: false })
    } finally {
      setVerifyingPhone(false)
    }
  }

  const handleSavePhone = async () => {
    if (!phone.trim()) { setPhoneMsg({ text: 'Enter a phone number first.', ok: false }); return }
    setSavingPhone(true)
    setPhoneMsg({ text: 'Sending test message via Sendblue...', ok: null })
    try {
      const r = await api.post<{
        sms_verified?: boolean; phone_number?: string; test_send?: { error?: string; status?: string }
      }>('/auth/me/phone', { phone: phone.trim(), send_test: true }).then(res => res.data)
      if (r?.sms_verified) {
        setPhoneMsg({ text: `✓ Test sent to ${r.phone_number}.`, ok: true })
      } else {
        const err = (r?.test_send?.error || r?.test_send?.status) || 'Unknown error'
        const shown = r?.phone_number || phone
        setPhoneMsg({ text: `Saved ${shown}, but test send failed: ${err}.`, ok: null })
      }
      refetchMe()
    } catch (e: unknown) {
      setPhoneMsg({ text: `Error: ${(e as Error).message || 'request failed'}`, ok: false })
    } finally {
      setSavingPhone(false)
    }
  }

  const handleOptOut = async (checked: boolean) => {
    setSmsOptOut(checked)
    try {
      await api.post('/auth/me/opt-out', { opt_out: checked })
      setPhoneMsg({ text: `SMS ${checked ? 'paused' : 'enabled'}.`, ok: true })
    } catch (e: unknown) {
      setPhoneMsg({ text: `Error: ${(e as Error).message || 'request failed'}`, ok: false })
      setSmsOptOut(!checked)
    }
  }

  // ════════════════════════════════════════════════════════════════
  // Section 3: Trade security
  // ════════════════════════════════════════════════════════════════
  const [totpEnrollOpen, setTotpEnrollOpen] = useState(false)
  const [totpSecret, setTotpSecret] = useState('')
  const [totpQrUrl, setTotpQrUrl] = useState('')
  const [totpQrError, setTotpQrError] = useState(false)
  const [totpCode, setTotpCode] = useState('')
  const [totpMsg, setTotpMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [methodMsg, setMethodMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [passkeyMsg, setPasskeyMsg] = useState<string | null>(null)

  const handleTotpEnroll = async () => {
    try {
      const r = await api.post<{ secret: string; qr_url?: string; qr_data_url?: string }>(
        '/auth/2fa/totp/enroll'
      ).then(res => res.data)
      setTotpSecret(r.secret)
      setTotpQrUrl(r.qr_data_url || r.qr_url || `/api/auth/2fa/totp/qr?ts=${Date.now()}`)
      setTotpQrError(false)
      setTotpCode('')
      setTotpMsg(null)
      setTotpEnrollOpen(true)
    } catch (e: unknown) {
      setTotpMsg({ text: `Enroll failed: ${(e as Error).message}`, ok: false })
    }
  }

  const handleTotpActivate = async () => {
    const code = totpCode.trim()
    try {
      await api.post('/auth/2fa/totp/activate', { code })
      setTotpMsg({ text: '✓ Authenticator enabled.', ok: true })
      setTotpEnrollOpen(false)
      refetch2FA()
    } catch {
      setTotpMsg({ text: 'Invalid code. Try again.', ok: false })
    }
  }

  const handleTotpDisable = async () => {
    if (!confirm('Remove authenticator app 2FA?')) return
    try {
      await api.post('/auth/2fa/totp/disable')
      refetch2FA()
    } catch (e: unknown) {
      setTotpMsg({ text: `Failed: ${(e as Error).message}`, ok: false })
    }
  }

  const handlePasskeyRegister = async () => {
    if (!navigator.credentials || !window.PublicKeyCredential) {
      setPasskeyMsg("Browser doesn't support passkeys.")
      return
    }
    try {
      const begin = await api.post<PublicKeyCredentialCreationOptions & {
        challenge: string; user: { id: string }; excludeCredentials?: Array<{ id: string }>
      }>('/auth/2fa/passkey/register/begin').then(r => r.data)

      const b64urlToBuf = (b64: string) => {
        const base64 = b64.replace(/-/g, '+').replace(/_/g, '/')
        const bin = atob(base64)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        return bytes.buffer
      }
      const bufToB64url = (buf: ArrayBuffer) => {
        const bytes = new Uint8Array(buf)
        let bin = ''
        bytes.forEach(b => { bin += String.fromCharCode(b) })
        return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '')
      }

      const pub = begin as unknown as PublicKeyCredentialCreationOptions & {
        challenge: string | ArrayBuffer
        user: { id: string | ArrayBuffer; displayName: string; name: string }
        excludeCredentials?: Array<{ id: string | ArrayBuffer; type: string; transports?: string[] }>
      }
      pub.challenge = b64urlToBuf(pub.challenge as string)
      pub.user.id = b64urlToBuf(pub.user.id as string);
      (pub.excludeCredentials || []).forEach(c => { c.id = b64urlToBuf(c.id as unknown as string) })

      const cred = await navigator.credentials.create({ publicKey: pub as PublicKeyCredentialCreationOptions }) as PublicKeyCredential & {
        response: AuthenticatorAttestationResponse
      }
      const name = (prompt('Name this passkey (e.g. "iPhone", "YubiKey"):', 'Passkey') || 'Passkey').slice(0, 40)
      const payload = {
        _name: name,
        id: cred.id,
        rawId: bufToB64url(cred.rawId),
        type: cred.type,
        response: {
          attestationObject: bufToB64url(cred.response.attestationObject),
          clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        },
      }
      await api.post('/auth/2fa/passkey/register/complete', payload)
      setPasskeyMsg('✓ Passkey registered.')
      refetch2FA()
    } catch (e: unknown) {
      setPasskeyMsg(`Passkey setup failed: ${(e as Error).message || e}`)
    }
  }

  const handleRemovePasskey = async (id: string) => {
    if (!confirm('Remove this passkey?')) return
    try {
      await api.delete(`/auth/2fa/passkey/${encodeURIComponent(id)}`)
      refetch2FA()
    } catch (e: unknown) {
      setPasskeyMsg(`Failed: ${(e as Error).message}`)
    }
  }

  const handleSetMethod = async (method: string) => {
    try {
      await api.post('/auth/2fa/method', { method })
      setMethodMsg({ text: `Trade 2FA set to ${method}.`, ok: true })
      refetch2FA()
    } catch (e: unknown) {
      setMethodMsg({ text: (e as Error).message, ok: false })
      refetch2FA()
    }
  }

  // ════════════════════════════════════════════════════════════════
  // Section 7: AI Provider API keys
  // ════════════════════════════════════════════════════════════════
  const AI_KEYS = [
    ['OPENROUTER_API_KEY',  'OpenRouter',  'Free & paid models, best for free tier'],
    ['OPENAI_API_KEY',      'OpenAI',      'GPT-4, GPT-5 series'],
    ['ANTHROPIC_API_KEY',   'Anthropic',   'Claude Opus / Sonnet / Haiku'],
    ['GOOGLE_API_KEY',      'Google',      'Gemini Pro / Flash'],
    ['NVIDIA_API_KEY',      'NVIDIA NIM',  'Nemotron models via NVIDIA API'],
    ['XAI_API_KEY',         'xAI',         'Grok models'],
    ['DEEPSEEK_API_KEY',    'DeepSeek',    'DeepSeek Chat & Reasoner'],
    ['DASHSCOPE_API_KEY',   'Qwen',        'Alibaba DashScope (Qwen models)'],
    ['OLLAMA_BASE_URL',     'Ollama URL',  'Local Ollama instance base URL'],
  ] as const

  const [apiKeyVals, setApiKeyVals] = useState<Record<string, string>>({})
  const [apiKeySaving, setApiKeySaving] = useState(false)
  const [apiKeyMsg, setApiKeyMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const setApiKey = (k: string) => (v: string) => setApiKeyVals(prev => ({ ...prev, [k]: v }))

  const handleSaveApiKeys = async () => {
    const updates: Record<string, string> = {}
    AI_KEYS.forEach(([k]) => { if (apiKeyVals[k]) updates[k] = apiKeyVals[k] })
    if (!Object.keys(updates).length) { setApiKeyMsg({ text: 'No new values entered.', ok: false }); return }
    setApiKeySaving(true)
    try {
      await api.post('/settings', { updates })
      setApiKeyMsg({ text: '✓ Keys saved.', ok: true })
      setSavedBanner(true)
      setTimeout(() => setSavedBanner(false), 3000)
      qc.invalidateQueries({ queryKey: ['settings'] })
      setApiKeyVals({})
    } catch (e: unknown) {
      setApiKeyMsg({ text: (e as Error).message, ok: false })
    } finally {
      setApiKeySaving(false)
    }
  }

  // ════════════════════════════════════════════════════════════════
  // Section 8: Trading Controls
  // ════════════════════════════════════════════════════════════════
  const TRADING_KEYS = [
    ['TRADINGAGENTS_MAX_POSITION_SIZE', 'Paper Max Position Size', 'Fraction of portfolio (0–1)', 'number'],
    ['TRADINGAGENTS_SLIPPAGE',          'Slippage',                'Fractional slippage (0–1)',    'number'],
    ['TRADINGAGENTS_COMMISSION',        'Commission ($)',          'Per-trade commission in USD',  'number'],
  ] as const

  const [tradingVals, setTradingVals] = useState<Record<string, string>>({})
  const [tradingSaving, setTradingSaving] = useState(false)
  const [tradingMsg, setTradingMsg] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    if (settings) {
      const init: Record<string, string> = {}
      TRADING_KEYS.forEach(([k]) => { if (settings[k] != null) init[k] = String(settings[k]) })
      setTradingVals(init)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

  const setTradingVal = (k: string) => (v: string) => setTradingVals(prev => ({ ...prev, [k]: v }))

  const handleSaveTradingControls = async () => {
    const updates: Record<string, string> = {}
    TRADING_KEYS.forEach(([k]) => { if (tradingVals[k] !== undefined) updates[k] = tradingVals[k] })
    setTradingSaving(true)
    try {
      await api.post('/settings', { updates })
      setTradingMsg({ text: '✓ Saved.', ok: true })
      qc.invalidateQueries({ queryKey: ['settings'] })
    } catch (e: unknown) {
      setTradingMsg({ text: (e as Error).message, ok: false })
    } finally {
      setTradingSaving(false)
    }
  }

  // ── Derived display values ──────────────────────────────────────
  const admin = isAdmin()

  const smsStateBadgeStyle: React.CSSProperties = me?.sms_verified
    ? { background: '#064e3b', color: '#34d399' }
    : me?.phone_number || me?.phone
      ? { background: '#78350f', color: '#fbbf24' }
      : {}

  const smsStateLabel = me?.sms_verified
    ? `Verified${me.sms_service ? ` · ${me.sms_service}` : ''}`
    : (me?.phone_number || me?.phone) ? 'Unverified' : 'Not set'

  const methodLabel: Record<string, string> = {
    none: 'Off', email: 'Email code', totp: 'Authenticator', passkey: 'Passkey',
  }
  const currentMethodLabel = methodLabel[twofa?.method || 'none'] || 'Off'
  const methodBadgeStyle: React.CSSProperties = twofa?.method && twofa.method !== 'none'
    ? { background: 'var(--accent)', color: '#000' }
    : {}

  const openRouterUsage = settings?.openrouter_usage
  const orUsed = Number(openRouterUsage?.requests || 0)
  const orLimit = Number(openRouterUsage?.limit || 1000)
  const orRemaining = Math.max(0, orLimit - orUsed)
  const orPct = orLimit > 0 ? Math.min(100, (orUsed / orLimit) * 100) : 0
  const orBarColor = orPct >= 90 ? '#ef4444' : orPct >= 75 ? '#f59e0b' : '#06b6d4'
  const orSources = openRouterUsage?.by_source || {}
  const orSourceText = Object.entries(orSources)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([k, v]) => `${k}: ${Number(v).toLocaleString()}`)
    .join(' · ')

  return (
    <div id="panel-settings" style={{ padding: 24, maxWidth: 768, margin: '0 auto' }}>

      {/* Save banner */}
      {savedBanner && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px',
          borderRadius: 8, border: '1px solid rgba(6,78,59,.8)',
          background: 'rgba(6,78,59,.2)', color: '#34d399', fontSize: 13, marginBottom: 16,
        }}>
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5} style={{ flexShrink: 0 }}>
            <path d="M20 6L9 17l-5-5" />
          </svg>
          Settings saved successfully
        </div>
      )}

      {/* ── Section 1: Account ─────────────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Account</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Your identity, verified by Cloudflare Access.</div>
          </div>
          <a
            href="https://jeffthebever.cloudflareaccess.com/cdn-cgi/access/logout"
            className="btn-secondary"
            style={{ fontSize: 12 }}
          >
            Sign out
          </a>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>Email</div>
            <div style={{ color: 'var(--ink-muted)', fontFamily: 'monospace', fontSize: 12 }}>
              {me?.email || storeUser?.email || '—'}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>Role</div>
            <span style={{
              padding: '2px 8px', borderRadius: 99, fontSize: 12, fontWeight: 700,
              background: (me?.is_admin || me?.role === 'admin') ? 'var(--accent)' : 'var(--surface-raised)',
              color: (me?.is_admin || me?.role === 'admin') ? '#000' : 'var(--ink-faint)',
            }}>
              {me?.role || storeUser?.role || '—'}
            </span>
          </div>
        </div>

        <div>
          <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
            Display name
          </label>
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <input
              type="text"
              maxLength={80}
              placeholder="Your name"
              className="input"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              autoComplete="name"
              style={{ flex: 1 }}
            />
            <button
              className="btn-primary"
              style={{ fontSize: 13 }}
              onClick={() => { if (displayName.trim()) saveName.mutate(displayName.trim()); else setNameMsg({ text: 'Enter a name.', ok: false }) }}
              disabled={saveName.isPending}
            >
              {saveName.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
          {nameMsg && (
            <div style={{ fontSize: 12, marginTop: 6, minHeight: 16, color: nameMsg.ok ? '#22c55e' : '#ef4444' }}>
              {nameMsg.text}
            </div>
          )}
        </div>
      </Card>

      {/* ── Section 2: SMS alerts ──────────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>SMS alerts</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Where Agentic Trader texts you. Uses Sendblue (iMessage where possible).</div>
          </div>
          <span style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 99,
            background: 'var(--surface-raised)', color: 'var(--ink-faint)',
            fontWeight: 600, whiteSpace: 'nowrap', flexShrink: 0, ...smsStateBadgeStyle,
          }}>
            {smsStateLabel}
          </span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
              Mobile number
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <input
                type="tel"
                inputMode="tel"
                placeholder="+1 (555) 123-4567"
                className="input"
                value={phone}
                onChange={e => setPhone(e.target.value)}
                autoComplete="tel"
                style={{ flex: 1 }}
              />
              <button
                className="btn-secondary"
                style={{ fontSize: 13, whiteSpace: 'nowrap' }}
                onClick={handleVerifyPhone}
                disabled={verifyingPhone}
                title="Check the number is reachable and iMessage/SMS capable (no message sent)"
              >
                {verifyingPhone ? 'Checking…' : 'Verify'}
              </button>
              <button
                className="btn-primary"
                style={{ fontSize: 13, whiteSpace: 'nowrap' }}
                onClick={handleSavePhone}
                disabled={savingPhone}
              >
                {savingPhone ? 'Saving…' : 'Save & test'}
              </button>
            </div>
            {phoneMsg && (
              <div style={{
                fontSize: 12, marginTop: 6, minHeight: 16,
                color: phoneMsg.ok === true ? '#22c55e' : phoneMsg.ok === false ? '#ef4444' : '#f59e0b',
              }}>
                {phoneMsg.text}
              </div>
            )}
          </div>

          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            paddingTop: 12, borderTop: '1px solid var(--surface-rule)',
          }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-muted)' }}>Pause SMS alerts</div>
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>Same effect as texting STOP. Reply START to re-enable.</div>
            </div>
            <Toggle checked={smsOptOut} onChange={handleOptOut} />
          </div>
        </div>
      </Card>

      {/* ── Section 3: Trade security ──────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Trade security</div>
          <span style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 99,
            background: 'var(--surface-raised)', color: 'var(--ink-faint)',
            fontWeight: 600, ...methodBadgeStyle,
          }}>
            {currentMethodLabel}
          </span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>
          Require a second factor before any real-money trade. Recommended: authenticator app (TOTP) or a passkey.
        </div>

        {/* Email OTC */}
        <InlineBlock style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Email one-time code</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Sends a 6-digit code to your Cloudflare login email.</div>
            </div>
            <span style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 99, fontWeight: 600,
              background: twofa?.email_enabled ? '#064e3b' : 'var(--surface-raised)',
              color: twofa?.email_enabled ? '#34d399' : 'var(--ink-faint)',
            }}>
              {twofa == null ? 'Checking' : twofa.email_enabled ? 'Available' : 'Not configured'}
            </span>
          </div>
        </InlineBlock>

        {/* TOTP */}
        <InlineBlock style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Authenticator app (TOTP)</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
                Microsoft / Google Authenticator.{' '}
                <span style={{ color: '#34d399', fontWeight: 700 }}>Recommended</span>
              </div>
            </div>
            <span style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 99, fontWeight: 600,
              background: twofa?.totp_enabled ? '#064e3b' : 'var(--surface-raised)',
              color: twofa?.totp_enabled ? '#34d399' : 'var(--ink-faint)',
            }}>
              {twofa?.totp_enabled ? 'Enabled' : 'Not set'}
            </span>
          </div>

          {totpEnrollOpen && (
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
                Scan the QR code with your authenticator app, or use the manual key below, then enter the 6-digit code to confirm.
              </div>
              <div style={{ display: 'flex', flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
                {totpQrUrl && !totpQrError && (
                  <div style={{
                    borderRadius: 6, border: '1px solid var(--surface-rule)',
                    background: '#f1f5f9', padding: 12, width: 176, flexShrink: 0,
                  }}>
                    <img
                      src={totpQrUrl}
                      alt="Authenticator setup QR code"
                      style={{ display: 'block', width: '100%', height: 'auto', borderRadius: 4 }}
                      onError={() => setTotpQrError(true)}
                    />
                  </div>
                )}
                {totpQrError && (
                  <div style={{
                    borderRadius: 6, border: '1px solid var(--surface-rule)',
                    background: '#f1f5f9', padding: 12, width: 176, flexShrink: 0,
                    textAlign: 'center', fontSize: 11, color: '#475569', lineHeight: 1.4,
                  }}>
                    QR unavailable. Use the manual key below.
                  </div>
                )}
                <div style={{
                  borderRadius: 6, border: '1px solid var(--surface-rule)',
                  background: 'rgba(15,23,42,.6)', padding: 12, fontSize: 12,
                  color: 'var(--ink-faint)', flex: 1, minWidth: 200,
                }}>
                  <div style={{ fontWeight: 600, color: 'var(--ink-muted)', marginBottom: 4 }}>Agentic Trader TOTP</div>
                  <div>Open Google Authenticator, Microsoft Authenticator, 1Password, or another TOTP app and scan this code. Keep the manual key private.</div>
                </div>
              </div>
              <div style={{
                borderRadius: 6, background: 'var(--surface-soft, #0f172a)',
                padding: 12, fontFamily: 'monospace', fontSize: 13,
                color: '#67e8f9', wordBreak: 'break-all',
              }}>
                {totpSecret}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  type="text"
                  inputMode="numeric"
                  maxLength={8}
                  placeholder="000000"
                  className="input"
                  value={totpCode}
                  onChange={e => setTotpCode(e.target.value)}
                  style={{ flex: 1, letterSpacing: '.2em', fontFamily: 'monospace' }}
                />
                <button className="btn-primary" style={{ fontSize: 13 }} onClick={handleTotpActivate}>Confirm</button>
              </div>
              {totpMsg && (
                <div style={{ fontSize: 12, minHeight: 14, color: totpMsg.ok ? '#22c55e' : '#ef4444' }}>
                  {totpMsg.text}
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            {!twofa?.totp_enabled && (
              <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handleTotpEnroll}>Set up</button>
            )}
            {twofa?.totp_enabled && (
              <button className="btn-secondary" style={{ fontSize: 12, color: '#ef4444' }} onClick={handleTotpDisable}>Remove</button>
            )}
          </div>
        </InlineBlock>

        {/* Passkey */}
        <InlineBlock>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Passkey</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Face ID, fingerprint, or hardware key. Strongest, phishing-proof.</div>
            </div>
            <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handlePasskeyRegister}>Add passkey</button>
          </div>
          {passkeyMsg && (
            <div style={{ fontSize: 12, marginTop: 8, color: passkeyMsg.startsWith('✓') ? '#22c55e' : '#ef4444' }}>
              {passkeyMsg}
            </div>
          )}
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {!twofa?.passkeys?.length && (
              <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No passkeys registered.</div>
            )}
            {(twofa?.passkeys || []).map(p => (
              <div key={p.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 12 }}>
                <span style={{ color: 'var(--ink-muted)' }}>{p.name || 'Passkey'}</span>
                <button
                  onClick={() => handleRemovePasskey(p.id)}
                  style={{ color: '#f87171', background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        </InlineBlock>

        {/* Method selector */}
        <div style={{
          marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--surface-rule)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-muted)' }}>Method used at trade time</div>
          <select
            className="input"
            value={twofa?.method || 'none'}
            onChange={e => handleSetMethod(e.target.value)}
            style={{ width: 'auto', padding: '4px 8px', fontSize: 12 }}
          >
            <option value="none">Off (not recommended)</option>
            <option value="email" disabled={!twofa?.email_enabled}>Email code</option>
            <option value="totp">Authenticator (TOTP)</option>
            <option value="passkey">Passkey</option>
          </select>
        </div>
        {methodMsg && (
          <div style={{ fontSize: 12, marginTop: 8, minHeight: 14, color: methodMsg.ok ? '#22c55e' : '#ef4444' }}>
            {methodMsg.text}
          </div>
        )}
      </Card>

      {/* ── Section 4: Live verification checklist ─────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Live verification checklist</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Broker isolation, paper runner supervision, and notification wiring.</div>
          </div>
          <button className="btn-secondary" style={{ fontSize: 12 }} onClick={() => refetchLive()} disabled={liveFetching}>
            {liveFetching ? 'Checking…' : 'Refresh'}
          </button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {!liveVerif && !liveFetching && (
            <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Loading…</div>
          )}
          {liveFetching && !liveVerif && (
            <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Checking...</div>
          )}
          {liveVerif?.checks?.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No checks returned.</div>
          )}
          {(liveVerif?.checks || []).map((c, i) => {
            const tone: Record<string, { label: string; borderColor: string; bg: string; color: string }> = {
              pass: { label: 'PASS', borderColor: 'rgba(6,78,59,.7)', bg: 'rgba(6,78,59,.2)', color: '#34d399' },
              warn: { label: 'WARN', borderColor: 'rgba(120,53,15,.7)', bg: 'rgba(120,53,15,.2)', color: '#fbbf24' },
              fail: { label: 'FAIL', borderColor: 'rgba(153,27,27,.7)', bg: 'rgba(153,27,27,.2)', color: '#f87171' },
            }
            const t = tone[c.status] || tone.warn
            return (
              <div key={i} style={{
                borderRadius: 6, border: `1px solid var(--surface-rule)`,
                background: 'rgba(2,6,23,.3)', padding: 12,
                display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12,
              }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{c.label || c.id}</div>
                  {(c.detail || c.note) && (
                    <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>{c.detail || c.note}</div>
                  )}
                </div>
                <span style={{
                  fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                  border: `1px solid ${t.borderColor}`, background: t.bg, color: t.color,
                  whiteSpace: 'nowrap', flexShrink: 0,
                }}>
                  {t.label}
                </span>
              </div>
            )
          })}
        </div>
      </Card>

      {/* ── Section 5: Help & contact ──────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>Help &amp; contact</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>
          For access issues or bugs, email support. For privacy, deletion, or data requests, email privacy.
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <a
            href="mailto:support@agentictrader.org?subject=Agentic%20Trader%20Support"
            className="btn-secondary"
            style={{ fontSize: 12 }}
          >
            support@agentictrader.org
          </a>
          <a
            href="mailto:privacy@agentictrader.org?subject=Agentic%20Trader%20Privacy%20Request"
            className="btn-secondary"
            style={{ fontSize: 12 }}
          >
            privacy@agentictrader.org
          </a>
        </div>
        <div style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
          Automated notifications come from <code>no-reply@agentictrader.org</code>; replies route to support where configured.
        </div>
      </Card>

      {/* ── Section 6: Appearance ─────────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Appearance</div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Dark mode reduces eye strain in low-light environments.</div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none' }}>
            <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Dark Mode</span>
            <Toggle checked={mode === 'dark'} onChange={() => toggleTheme()} />
          </label>
        </div>
      </Card>

      {/* ── Section 7: AI Providers (admin only) ──────────────────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>AI Providers</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>API keys for language model providers. Leave blank to keep existing value.</div>
            </div>
          </div>

          {/* OpenRouter usage card */}
          <div style={{
            marginBottom: 16, borderRadius: 6, border: '1px solid var(--surface-rule)',
            background: 'rgba(2,6,23,.4)', padding: 16,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>OpenRouter Daily Usage</div>
                <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>Tracked local requests against the 1,000 request/day free limit.</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>
                  {orUsed.toLocaleString()} / {orLimit.toLocaleString()}
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>{openRouterUsage?.date || 'Today'}</div>
              </div>
            </div>
            <div style={{ marginTop: 12, height: 8, borderRadius: 4, background: 'var(--surface-soft, #0f172a)', overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 4, background: orBarColor, width: `${orPct.toFixed(1)}%`, transition: 'width .4s' }} />
            </div>
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--ink-faint)' }}>
              Remaining: {orRemaining.toLocaleString()}{orSourceText ? ' · ' + orSourceText : ''}
            </div>
          </div>

          {/* API key rows */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {AI_KEYS.map(([envKey, label, desc]) => (
              <ApiKeyRow
                key={envKey}
                envKey={envKey}
                label={label}
                desc={desc}
                isSet={!!(settings?.[envKey] as { set?: boolean } | undefined)?.set}
                value={apiKeyVals[envKey] || ''}
                onChange={setApiKey(envKey)}
              />
            ))}
          </div>

          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="btn-primary" style={{ fontSize: 13 }} onClick={handleSaveApiKeys} disabled={apiKeySaving}>
              {apiKeySaving ? 'Saving…' : 'Save API Keys'}
            </button>
            {apiKeyMsg && (
              <span style={{ fontSize: 12, color: apiKeyMsg.ok ? '#22c55e' : '#ef4444' }}>{apiKeyMsg.text}</span>
            )}
          </div>
        </Card>
      )}

      {/* ── Section 8: Trading Controls (admin only) ───────────────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Trading Controls</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>Paper trading max position size, slippage, and commission.</div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px 24px' }}>
            {TRADING_KEYS.map(([k, label, desc, type]) => (
              <div key={k}>
                <label
                  htmlFor={`trading-${k}`}
                  style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--ink-muted)', marginBottom: 2 }}
                >
                  {label}
                </label>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>{desc}</div>
                <input
                  id={`trading-${k}`}
                  className="input"
                  type={type}
                  value={tradingVals[k] ?? ''}
                  onChange={e => setTradingVal(k)(e.target.value)}
                  step={type === 'number' ? 'any' : undefined}
                />
              </div>
            ))}
          </div>

          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="btn-primary" style={{ fontSize: 13 }} onClick={handleSaveTradingControls} disabled={tradingSaving}>
              {tradingSaving ? 'Saving…' : 'Save'}
            </button>
            {tradingMsg && (
              <span style={{ fontSize: 12, color: tradingMsg.ok ? '#22c55e' : '#ef4444' }}>{tradingMsg.text}</span>
            )}
          </div>
        </Card>
      )}

    </div>
  )
}
