import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, Check } from 'lucide-react'
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
          {show ? <EyeOff size={16} strokeWidth={2} /> : <Eye size={16} strokeWidth={2} />}
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
    ['CLOUDFLARE_API_TOKEN', 'Cloudflare Workers AI', 'Main AI provider for analysis and backtests'],
    ['OPENROUTER_API_KEY',   'OpenRouter',             'Free & paid models, best for free tier'],
    ['OPENAI_API_KEY',       'OpenAI',                 'GPT-4, GPT-5 series'],
    ['ANTHROPIC_API_KEY',    'Anthropic',              'Claude Opus / Sonnet / Haiku'],
    ['GOOGLE_API_KEY',       'Google',                 'Gemini Pro / Flash'],
    ['NVIDIA_API_KEY',       'NVIDIA NIM',             'Nemotron models via NVIDIA API'],
    ['XAI_API_KEY',          'xAI',                    'Grok models'],
    ['DEEPSEEK_API_KEY',     'DeepSeek',               'DeepSeek Chat & Reasoner'],
    ['DASHSCOPE_API_KEY',    'Qwen',                   'Alibaba DashScope (Qwen models)'],
    ['ZHIPU_API_KEY',        'GLM (Zhipu)',             'GLM-4 / GLM-5 series'],
    ['OLLAMA_BASE_URL',      'Ollama URL',             'Local Ollama instance base URL'],
  ] as const

  const DATA_KEYS = [
    ['FMP_API_KEY',           'Financial Modeling Prep', 'Fundamentals, earnings, financials'],
    ['ALPHA_VANTAGE_API_KEY', 'Alpha Vantage',           'Price data & technical indicators'],
  ] as const

  const [apiKeyVals, setApiKeyVals] = useState<Record<string, string>>({})
  const [apiKeySaving, setApiKeySaving] = useState(false)
  const [apiKeyMsg, setApiKeyMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [dataKeySaving, setDataKeySaving] = useState(false)
  const [dataKeyMsg, setDataKeyMsg] = useState<{ text: string; ok: boolean } | null>(null)

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

  const handleSaveDataKeys = async () => {
    const updates: Record<string, string> = {}
    DATA_KEYS.forEach(([k]) => { if (apiKeyVals[k]) updates[k] = apiKeyVals[k] })
    if (!Object.keys(updates).length) { setDataKeyMsg({ text: 'No new values entered.', ok: false }); return }
    setDataKeySaving(true)
    try {
      await api.post('/settings', { updates })
      setDataKeyMsg({ text: '✓ Keys saved.', ok: true })
      qc.invalidateQueries({ queryKey: ['settings'] })
      setApiKeyVals({})
    } catch (e: unknown) {
      setDataKeyMsg({ text: (e as Error).message, ok: false })
    } finally {
      setDataKeySaving(false)
    }
  }

  // ════════════════════════════════════════════════════════════════
  // Section 8: Risk & Portfolio
  // ════════════════════════════════════════════════════════════════
  const RISK_KEYS: Array<[string, string, string, 'number' | 'text']> = [
    ['TRADINGAGENTS_STARTING_CASH',       'Starting Cash ($)',        'Initial portfolio cash',                    'number'],
    ['TRADINGAGENTS_MAX_POSITIONS',        'Max Open Positions',       'Maximum concurrent positions',              'number'],
    ['TRADINGAGENTS_MAX_POSITION_SIZE',    'Max Position Size',        'Fraction of portfolio (0–1)',                'number'],
    ['TRADINGAGENTS_MAX_SECTOR_EXPOSURE',  'Max Sector Exposure',      'Fraction per sector (0–1)',                  'number'],
    ['TRADINGAGENTS_MAX_DAILY_LOSS',       'Max Daily Loss',           'Negative value, e.g. -0.05',                'number'],
    ['TRADINGAGENTS_MAX_MONTHLY_LOSS',     'Max Monthly Loss',         'Negative value, e.g. -0.15',                'number'],
    ['TRADINGAGENTS_FMP_DAILY_LIMIT',      'FMP Daily API Limit',      'Requests per day',                          'number'],
    ['TRADINGAGENTS_SLIPPAGE',             'Slippage',                 'Fractional slippage (0–1)',                  'number'],
    ['TRADINGAGENTS_COMMISSION',           'Commission ($)',            'Per-trade commission in USD',               'number'],
    ['SEC_USER_AGENT',                     'SEC User Agent',           'Required for SEC EDGAR access',             'text'],
    ['LLM_PROVIDER',                       'Default LLM Provider',     'cloudflare, openrouter, openai…',           'text'],
    ['CLOUDFLARE_ACCOUNT_ID',              'Cloudflare Account ID',    'Required for Workers AI and D1',            'text'],
    ['CLOUDFLARE_AI_GATEWAY_URL',          'CF AI Gateway URL',        'Optional OpenAI-compatible gateway',        'text'],
    ['CLOUDFLARE_DEFAULT_QUICK_MODEL',     'CF Quick Model',           'Default quick model ID',                    'text'],
    ['CLOUDFLARE_DEFAULT_DEEP_MODEL',      'CF Deep Model',            'Default deep model ID',                     'text'],
    ['CLOUDFLARE_D1_DATABASE_ID',          'CF D1 Database ID',        'Optional D1 storage backend',               'text'],
  ]

  const [tradingVals, setTradingVals] = useState<Record<string, string>>({})
  const [tradingSaving, setTradingSaving] = useState(false)
  const [tradingMsg, setTradingMsg] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    if (settings) {
      const init: Record<string, string> = {}
      RISK_KEYS.forEach(([k]) => { if (settings[k] != null) init[k] = String(settings[k]) })
      setTradingVals(init)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

  const setTradingVal = (k: string) => (v: string) => setTradingVals(prev => ({ ...prev, [k]: v }))

  const handleSaveTradingControls = async () => {
    const updates: Record<string, string> = {}
    RISK_KEYS.forEach(([k]) => { if (tradingVals[k] !== undefined) updates[k] = tradingVals[k] })
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

  // ════════════════════════════════════════════════════════════════
  // Section 9: Fidelity Connection Test
  // ════════════════════════════════════════════════════════════════
  type FiTestBadge = 'idle' | 'testing' | 'pass' | 'partial' | 'fail'
  const [fiTestBadge, setFiTestBadge] = useState<FiTestBadge>('idle')
  const [fiTestRunning, setFiTestRunning] = useState(false)
  const [fiResSession, setFiResSession] = useState<{ text: string; ok: boolean | null } | null>(null)
  const [fiResValue, setFiResValue] = useState('')
  const [fiResGl, setFiResGl] = useState('')
  const [fiResGlUp, setFiResGlUp] = useState<boolean | null>(null)
  const [fiPositions, setFiPositions] = useState<Array<{ symbol?: string; qty?: string; market_value?: string; today_gain_loss?: string; today_gain_pct?: string }>>([])
  const [fiLog, setFiLog] = useState<Array<{ text: string; ok: boolean | null }>>([])
  const [fiCredUser, setFiCredUser] = useState('—')
  const [fiCredPass, setFiCredPass] = useState('—')
  const [fiShowResults, setFiShowResults] = useState(false)

  // Trade dry-run
  const [fiTradeSym, setFiTradeSym] = useState('')
  const [fiTradeAction, setFiTradeAction] = useState('Buy')
  const [fiTradeQty, setFiTradeQty] = useState('1')
  const [fiTradeType, setFiTradeType] = useState('Limit')
  const [fiTradePrice, setFiTradePrice] = useState('')
  const [fiTradeRunning, setFiTradeRunning] = useState(false)
  const [fiTradeResult, setFiTradeResult] = useState<{ status: string; ok: boolean; preview: string } | null>(null)

  const appendFiLog = (text: string, ok: boolean | null) =>
    setFiLog(prev => [...prev, { text, ok }])

  const handleFiRunTest = async () => {
    setFiTestRunning(true)
    setFiTestBadge('testing')
    setFiLog([])
    setFiShowResults(true)
    setFiResSession(null)
    setFiResValue('—')
    setFiResGl('—')
    setFiResGlUp(null)
    setFiPositions([])

    // Load creds from settings
    if (settings) {
      setFiCredUser((settings['FIDELITY_USERNAME'] as string) || '—')
      setFiCredPass((settings['FIDELITY_PASSWORD'] as { set?: boolean } | undefined)?.set ? '••••••••' : 'Not set')
    }

    let pass = true
    let statusOk = false

    // Step 1 — session status
    appendFiLog('Checking session status…', null)
    try {
      const s = await api.get<{ connected?: boolean; session_file?: boolean }>('/fidelity/status').then(r => r.data)
      const session = !!s.connected
      statusOk = session
      setFiResSession({ text: session ? 'Active ✓' : (s.session_file ? 'File exists, not logged in' : 'Not connected'), ok: session })
      appendFiLog(`Session: connected=${session}, session_file=${s.session_file}`, session)
      if (!session) pass = false
    } catch (e: unknown) {
      appendFiLog(`Status endpoint error: ${(e as Error).message || e}`, false)
      setFiResSession({ text: 'Error', ok: false })
      pass = false
    }

    // Step 2 — summary
    if (statusOk) {
      appendFiLog('Fetching account summary…', null)
      try {
        const r = await api.get<{ summary?: { total_value?: string; daily_change?: string; daily_change_pct?: string } }>('/fidelity/summary').then(r => r.data)
        const sm = r.summary || {}
        setFiResValue(sm.total_value || 'N/A')
        setFiResGl(sm.daily_change ? `${sm.daily_change}${sm.daily_change_pct ? ` (${sm.daily_change_pct})` : ''}` : 'N/A')
        setFiResGlUp(sm.daily_change ? !sm.daily_change.startsWith('-') : null)
        appendFiLog(`Summary: value=${sm.total_value || '?'}, gl=${sm.daily_change || '?'}`, true)
      } catch (e: unknown) {
        appendFiLog(`Summary error: ${(e as Error).message || e}`, false)
        setFiResValue('Error')
        pass = false
      }
    } else {
      appendFiLog('Skipping summary — not connected', false)
    }

    // Step 3 — positions
    if (statusOk) {
      appendFiLog('Fetching positions…', null)
      try {
        const r = await api.get<{ positions?: Array<{ symbol?: string; qty?: string; market_value?: string; today_gain_loss?: string; today_gain_pct?: string }> }>('/fidelity/positions').then(r => r.data)
        const positions = r.positions || []
        setFiPositions(positions)
        appendFiLog(`Positions: ${positions.length} rows fetched`, true)
      } catch (e: unknown) {
        appendFiLog(`Positions error: ${(e as Error).message || e}`, false)
        pass = false
      }
    }

    setFiTestBadge(pass ? 'pass' : (statusOk ? 'partial' : 'fail'))
    appendFiLog(pass ? 'All checks passed.' : statusOk ? 'Connected but some data unavailable.' : 'Not connected — go to Fidelity panel to log in.', pass || null)
    setFiTestRunning(false)
  }

  const handleFiRunTradeDry = async () => {
    const sym = fiTradeSym.trim().toUpperCase()
    const qty = parseFloat(fiTradeQty)
    const limitPrice = fiTradeType === 'Limit' ? parseFloat(fiTradePrice) : null

    if (!sym) return
    if (!qty || qty < 1) return
    if (fiTradeType === 'Limit' && (!limitPrice || limitPrice <= 0)) return

    setFiTradeRunning(true)
    setFiTradeResult({ status: 'Running…', ok: false, preview: '' })
    try {
      const body: Record<string, unknown> = { symbol: sym, action: fiTradeAction, quantity: qty, order_type: fiTradeType, execute: false }
      if (fiTradeType === 'Limit') body.limit_price = limitPrice
      const r = await api.post<{ success?: boolean; status?: string; preview_text_snippet?: string }>('/fidelity/trade', body).then(res => res.data)
      const ok = !!(r.success && r.status === 'previewed')
      setFiTradeResult({
        status: ok ? 'Preview OK ✓ — form filled and preview loaded' : `status: ${r.status}`,
        ok,
        preview: r.preview_text_snippet || '(no preview text returned)',
      })
    } catch (e: unknown) {
      const msg = (e as Error).message || String(e)
      setFiTradeResult({ status: `Error: ${msg}`, ok: false, preview: msg })
    } finally {
      setFiTradeRunning(false)
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
          <Check size={16} strokeWidth={2.5} style={{ flexShrink: 0 }} />
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
	              <div style={{ height: '100%', borderRadius: 4, background: orBarColor, width: '100%', transformOrigin: 'left center', transform: `scaleX(${orPct / 100})`, transition: 'transform .4s var(--ease-out)' }} />
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

      {/* ── Section 8: Data Sources (admin only) ──────────────────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Data Sources</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>Market data and financial data provider keys.</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {DATA_KEYS.map(([envKey, label, desc]) => (
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
            <button className="btn-primary" style={{ fontSize: 13 }} onClick={handleSaveDataKeys} disabled={dataKeySaving}>
              {dataKeySaving ? 'Saving…' : 'Save Data Keys'}
            </button>
            {dataKeyMsg && (
              <span style={{ fontSize: 12, color: dataKeyMsg.ok ? '#22c55e' : '#ef4444' }}>{dataKeyMsg.text}</span>
            )}
          </div>
        </Card>
      )}

      {/* ── Section 9: Risk & Portfolio (admin only) ───────────────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Risk &amp; Portfolio</div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>Controls position sizing, loss limits, portfolio constraints, and system config.</div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px 24px' }}>
            {RISK_KEYS.map(([k, label, desc, type]) => (
              <div key={k}>
                <label
                  htmlFor={`risk-${k}`}
                  style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--ink-muted)', marginBottom: 2 }}
                >
                  {label}
                </label>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>{desc}</div>
                <input
                  id={`risk-${k}`}
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

      {/* ── Section 10: Compliance Guardrails (admin only) ────────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Market Compliance Guardrails</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
                Live broker order placement is hard-blocked. The app is analysis, backtest, and paper trading only.
              </div>
            </div>
            <span style={{
              fontSize: 11, padding: '3px 10px', borderRadius: 4, whiteSpace: 'nowrap', flexShrink: 0,
              ...(settings?.compliance as { live_trading_hard_blocked?: boolean } | undefined)?.live_trading_hard_blocked !== false
                ? { border: '1px solid rgba(6,78,59,.8)', background: 'rgba(6,78,59,.2)', color: '#34d399' }
                : { border: '1px solid rgba(153,27,27,.7)', background: 'rgba(153,27,27,.2)', color: '#f87171' },
            }}>
              {(settings?.compliance as { live_trading_hard_blocked?: boolean } | undefined)?.live_trading_hard_blocked !== false ? 'Blocked' : 'WARNING: Unblocked'}
            </span>
          </div>
          {((settings?.compliance as { blocked_actions?: string[] } | undefined)?.blocked_actions || []).length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
              {((settings?.compliance as { blocked_actions?: string[] } | undefined)?.blocked_actions || []).map((a: string, i: number) => (
                <span key={i} style={{
                  fontSize: 11, padding: '2px 8px', borderRadius: 4,
                  border: '1px solid rgba(6,78,59,.6)', background: 'rgba(6,78,59,.15)', color: '#34d399',
                }}>
                  {a}
                </span>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* ── Section 11: Data Paths (admin only) ───────────────────────────── */}
      {admin && settings?.paths && Object.keys(settings.paths as object).length > 0 && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>Data Paths</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {Object.entries(settings.paths as Record<string, string>).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, fontSize: 12 }}>
                <span style={{ color: 'var(--ink-faint)', fontFamily: 'monospace', width: 200, flexShrink: 0, paddingTop: 1 }}>{k}</span>
                <span style={{ color: 'var(--ink-muted)', fontFamily: 'monospace', wordBreak: 'break-all' }}>{v}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ── Section 12: Fidelity Connection Test (admin only) ─────────────── */}
      {admin && (
        <Card style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Fidelity Connection Test</div>
              <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginTop: 2 }}>
                Verify credentials, session state, and live data pull from your Fidelity account.
              </div>
            </div>
            <span style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 4,
              border: '1px solid var(--surface-rule)', background: 'var(--surface-raised)', color: 'var(--ink-faint)',
              ...(fiTestBadge === 'pass' ? { border: '1px solid rgba(6,78,59,.7)', background: 'rgba(6,78,59,.2)', color: '#34d399' } :
                  fiTestBadge === 'partial' ? { border: '1px solid rgba(120,53,15,.7)', background: 'rgba(120,53,15,.2)', color: '#fbbf24' } :
                  fiTestBadge === 'fail' ? { border: '1px solid rgba(153,27,27,.7)', background: 'rgba(153,27,27,.2)', color: '#f87171' } :
                  fiTestBadge === 'testing' ? { color: '#67e8f9' } : {}),
            }}>
              {fiTestBadge === 'idle' ? 'Not tested' : fiTestBadge === 'testing' ? 'Testing…' :
               fiTestBadge === 'pass' ? 'Pass ✓' : fiTestBadge === 'partial' ? 'Partial' : 'Fail ✗'}
            </span>
          </div>

          {/* Credential display */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
            <div style={{ borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'rgba(2,6,23,.4)', padding: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>FIDELITY_USERNAME</div>
              <div style={{ fontSize: 13, fontFamily: 'monospace', color: 'var(--ink-muted)' }}>{fiCredUser}</div>
            </div>
            <div style={{ borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'rgba(2,6,23,.4)', padding: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>FIDELITY_PASSWORD</div>
              <div style={{ fontSize: 13, fontFamily: 'monospace', color: 'var(--ink-muted)' }}>{fiCredPass}</div>
            </div>
          </div>

          {/* Live results */}
          {fiShowResults && (
            <div style={{
              borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'rgba(2,6,23,.3)',
              padding: 16, marginBottom: 16,
            }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 2 }}>Session</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: fiResSession?.ok === true ? '#34d399' : fiResSession?.ok === false ? '#f87171' : 'var(--ink)' }}>
                    {fiResSession?.text ?? '—'}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 2 }}>Account Value</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{fiResValue || '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 2 }}>Today's G/L</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: fiResGlUp === true ? '#34d399' : fiResGlUp === false ? '#f87171' : 'var(--ink-muted)' }}>
                    {fiResGl || '—'}
                  </div>
                </div>
              </div>
              {fiPositions.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 6 }}>Positions returned</div>
                  <div style={{ maxHeight: 128, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {fiPositions.map((p, i) => {
                      const glUp = p.today_gain_loss && !p.today_gain_loss.startsWith('-')
                      return (
                        <div key={i} style={{ display: 'flex', gap: 12, fontSize: 12, color: 'var(--ink-muted)', fontFamily: 'monospace' }}>
                          <span style={{ fontWeight: 600, minWidth: 60 }}>{p.symbol || '?'}</span>
                          <span style={{ color: 'var(--ink-faint)' }}>{p.qty} sh</span>
                          <span>{p.market_value}</span>
                          <span style={{ color: p.today_gain_loss ? (glUp ? '#34d399' : '#f87171') : 'var(--ink-faint)' }}>
                            {p.today_gain_loss}{p.today_gain_pct ? ` (${p.today_gain_pct})` : ''}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
              <div>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>Log</div>
                <div style={{ maxHeight: 112, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {fiLog.map((l, i) => (
                    <div key={i} style={{ fontSize: 11, fontFamily: 'monospace', color: l.ok === true ? '#34d399' : l.ok === false ? '#f87171' : 'var(--ink-faint)' }}>
                      {l.text}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
            <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handleFiRunTest} disabled={fiTestRunning}>
              {fiTestRunning ? 'Testing…' : 'Run Test'}
            </button>
            <a href="/app/broker" className="btn-secondary" style={{ fontSize: 12 }}>Open Fidelity Panel</a>
          </div>

          {/* Trade Dry-Run */}
          <div style={{ paddingTop: 16, borderTop: '1px solid var(--surface-rule)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>Trade Execution Test</div>
              <span style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 4,
                border: '1px solid rgba(120,53,15,.7)', background: 'rgba(120,53,15,.2)', color: '#fbbf24',
              }}>
                Preview Only — No Real Order Placed
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>
              Fills the Fidelity order form and clicks "Preview Order" to verify the trade flow works end-to-end. Stops before "Place Order".
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 12 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }}>Symbol</label>
                <input
                  className="input"
                  placeholder="AAPL"
                  maxLength={10}
                  style={{ textTransform: 'uppercase' }}
                  value={fiTradeSym}
                  onChange={e => setFiTradeSym(e.target.value.toUpperCase())}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }}>Action</label>
                <select className="input" value={fiTradeAction} onChange={e => setFiTradeAction(e.target.value)}>
                  <option>Buy</option>
                  <option>Sell</option>
                </select>
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }}>Quantity</label>
                <input className="input" type="number" min={1} value={fiTradeQty} onChange={e => setFiTradeQty(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }}>Order Type</label>
                <select className="input" value={fiTradeType} onChange={e => setFiTradeType(e.target.value)}>
                  <option>Limit</option>
                  <option>Market</option>
                </select>
              </div>
            </div>

            {fiTradeType === 'Limit' && (
              <div style={{ maxWidth: 200, marginBottom: 12 }}>
                <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }}>Limit Price ($)</label>
                <input
                  className="input"
                  type="number"
                  min={0.01}
                  step={0.01}
                  placeholder="e.g. 150.00"
                  value={fiTradePrice}
                  onChange={e => setFiTradePrice(e.target.value)}
                />
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handleFiRunTradeDry} disabled={fiTradeRunning}>
                {fiTradeRunning ? 'Running…' : 'Dry-Run Preview'}
              </button>
            </div>

            {fiTradeResult && (
              <div style={{
                borderRadius: 6, border: '1px solid var(--surface-rule)', background: 'rgba(2,6,23,.3)',
                padding: 12, display: 'flex', flexDirection: 'column', gap: 8,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>Status</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: fiTradeResult.ok ? '#34d399' : '#f87171' }}>
                    {fiTradeResult.status}
                  </span>
                </div>
                {fiTradeResult.preview && (
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>Preview response snippet</div>
                    <pre style={{
                      fontSize: 11, color: 'var(--ink-muted)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                      maxHeight: 160, overflowY: 'auto', background: 'var(--surface-soft, #0f172a)',
                      borderRadius: 4, padding: 8, margin: 0, fontFamily: 'monospace',
                    }}>
                      {fiTradeResult.preview}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </Card>
      )}

    </div>
  )
}
