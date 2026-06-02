import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useSettingsStore } from '../store/settings'
import { useAuthStore } from '../store/auth'
import { fetchAdminFlags, saveAdminFlags, fetchCloudflareConfig } from '../api/queries'
import { DEFAULT_API_BASE_URL } from '@shared/constants'
import { Eye, EyeOff, Save, Shield, ShieldOff, LogOut } from 'lucide-react'
import { Loading, ErrorState } from '../components/StateViews'

export function SettingsPage() {
  const { apiBaseUrl, apiToken, save: saveLocal, loaded } = useSettingsStore()
  const { user, safeMode, setSafeMode, logout, checkAuth } = useAuthStore()

  const [url, setUrl] = useState(apiBaseUrl)
  const [token, setToken] = useState(apiToken)
  const [showToken, setShowToken] = useState(false)
  const [refreshInterval, setRefreshInterval] = useState(15)
  const [connSaved, setConnSaved] = useState(false)

  useEffect(() => { setUrl(apiBaseUrl); setToken(apiToken) }, [apiBaseUrl, apiToken, loaded])

  async function handleSaveConnection() {
    await saveLocal({ apiBaseUrl: url.trim() || DEFAULT_API_BASE_URL, apiToken: token.trim() })
    await checkAuth()
    setConnSaved(true)
    setTimeout(() => setConnSaved(false), 2500)
  }

  const flags = useQuery({ queryKey: ['admin-flags'], queryFn: fetchAdminFlags, staleTime: 60_000 })
  const cfConfig = useQuery({ queryKey: ['cf-config'], queryFn: fetchCloudflareConfig, staleTime: 120_000 })

  const saveFlagsMut = useMutation({
    mutationFn: (f: Record<string, boolean>) => saveAdminFlags(f),
    onSuccess: () => flags.refetch(),
  })

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-bold text-white">Settings + Security</h1>
        <p className="text-sm text-gray-500 mt-1">Private manager configuration — no raw secrets displayed</p>
      </div>

      {/* Signed-in user */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Session</h2>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-white">{user?.name || user?.email}</p>
            <p className="text-xs text-gray-500">{user?.email} · role: {user?.role}</p>
          </div>
          <button
            onClick={() => { logout() }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 text-sm transition"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      </div>

      {/* Connection settings */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5 space-y-4">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Connection</h2>
        <div>
          <label className="block text-xs font-medium text-gray-300 mb-1.5">Server URL</label>
          <input
            type="url"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder={DEFAULT_API_BASE_URL}
            className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder:text-gray-600"
          />
          <p className="mt-1 text-xs text-gray-600">Stored in Tauri encrypted local store — never in plaintext.</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-300 mb-1.5">Manager API Key <span className="text-gray-500">(MANAGER_API_KEY from .env)</span></label>
          <div className="relative">
            <input
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="Value of MANAGER_API_KEY in your server .env"
              className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 pr-10 focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder:text-gray-600"
            />
            <button type="button" onClick={() => setShowToken(v => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <p className="mt-1 text-xs text-gray-600">Sent as <code>X-Manager-Key</code> header. Find value in server <code>.env</code> → <code>MANAGER_API_KEY</code>.</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-300 mb-1.5">Auto-refresh interval (seconds)</label>
          <select
            value={refreshInterval}
            onChange={e => setRefreshInterval(Number(e.target.value))}
            className="rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            {[10, 15, 30, 60].map(n => <option key={n} value={n}>{n}s</option>)}
          </select>
        </div>
        <button
          onClick={handleSaveConnection}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold transition"
        >
          <Save className="h-4 w-4" />
          {connSaved ? 'Saved!' : 'Save Connection'}
        </button>
      </div>

      {/* Safe mode */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Safe Mode</h2>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-white">Safe Mode</p>
            <p className="text-xs text-gray-500 mt-0.5">Disables all write/restart/deploy actions globally in this session</p>
          </div>
          <button
            onClick={() => setSafeMode(!safeMode)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition ${safeMode ? 'bg-yellow-600 hover:bg-yellow-500 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-300'}`}
          >
            {safeMode ? <Shield className="h-4 w-4" /> : <ShieldOff className="h-4 w-4" />}
            {safeMode ? 'Disable Safe Mode' : 'Enable Safe Mode'}
          </button>
        </div>
      </div>

      {/* Feature flags */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Feature Flags</h2>
        {flags.isLoading ? <Loading label="Loading flags…" /> : flags.error ? (
          <ErrorState error={flags.error} />
        ) : flags.data ? (
          <div className="space-y-3">
            {Object.entries(flags.data.flags).map(([key, val]) => (
              <div key={key} className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-gray-200">{key.replace(/_/g, ' ')}</p>
                </div>
                <button
                  onClick={() => {
                    if (safeMode) return
                    saveFlagsMut.mutate({ ...flags.data!.flags, [key]: !val })
                  }}
                  disabled={safeMode || saveFlagsMut.isPending}
                  className={`relative inline-flex h-5 w-9 rounded-full transition ${val ? 'bg-indigo-600' : 'bg-gray-600'} disabled:opacity-50`}
                >
                  <span className={`inline-block h-4 w-4 m-0.5 rounded-full bg-white shadow transition-transform ${val ? 'translate-x-4' : 'translate-x-0'}`} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Cloudflare config (no secrets) */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Cloudflare Configuration</h2>
        {cfConfig.isLoading ? <Loading label="Loading…" /> : cfConfig.error ? (
          <p className="text-xs text-gray-500">Cloudflare config unavailable (admin access required)</p>
        ) : cfConfig.data ? (
          <div className="space-y-2 text-sm">
            {[
              ['Domain', String(cfConfig.data.domain ?? '—')],
              ['Access team', String(cfConfig.data.access_team_domain ?? '—')],
              ['Tunnel running', String(cfConfig.data.tunnel_running)],
              ['Workers AI', String((cfConfig.data.workers_ai as Record<string, unknown>)?.configured)],
              ['D1', String((cfConfig.data.d1 as Record<string, unknown>)?.configured)],
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between">
                <span className="text-gray-500">{label}</span>
                <span className="text-gray-200">{val}</span>
              </div>
            ))}
          </div>
        ) : null}
        <p className="mt-3 text-xs text-gray-600 italic">Token values are never displayed. Masked keys shown server-side only.</p>
      </div>

      {/* Security notes */}
      <div className="rounded-xl bg-gray-900/60 ring-1 ring-gray-800 p-4 text-xs text-gray-500 space-y-1">
        <p className="font-medium text-gray-400">Security constraints active in this app:</p>
        <ul className="list-disc list-inside space-y-0.5">
          <li>Auth token stored in Tauri encrypted local store — not in env or localStorage</li>
          <li>No raw secrets, full env files, or passwords displayed</li>
          <li>All write actions require authentication + confirmation modal</li>
          <li>Safe mode disables all writes for the session</li>
          <li>No arbitrary shell command execution</li>
          <li>All management actions route through backend API — no direct server access</li>
        </ul>
      </div>
    </div>
  )
}
