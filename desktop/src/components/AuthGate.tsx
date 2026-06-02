import { useState } from 'react'
import { useSettingsStore } from '../store/settings'
import { DEFAULT_API_BASE_URL } from '@shared/constants'
import { useAuthStore } from '../store/auth'
import { Loading } from './StateViews'
import { Lock, Eye, EyeOff } from 'lucide-react'

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, initializing, checking, error, checkAuth } = useAuthStore()
  const { apiBaseUrl, apiToken, save, loaded: settingsLoaded } = useSettingsStore()
  const [url, setUrl] = useState(apiBaseUrl)
  const [token, setToken] = useState(apiToken)
  const [showToken, setShowToken] = useState(false)
  const [connecting, setConnecting] = useState(false)

  async function handleConnect() {
    setConnecting(true)
    await save({ apiBaseUrl: url.trim() || DEFAULT_API_BASE_URL, apiToken: token.trim() })
    await checkAuth()
    setConnecting(false)
  }

  if (!settingsLoaded || initializing) {
    return (
      <div className="h-screen bg-gray-950 flex items-center justify-center">
        <Loading label="Connecting to server…" />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="h-screen bg-gray-950 flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="flex flex-col items-center mb-8">
            <div className="rounded-2xl bg-indigo-600/20 ring-1 ring-indigo-500/30 p-4 mb-4">
              <Lock className="h-8 w-8 text-indigo-400" />
            </div>
            <h1 className="text-xl font-bold text-white">Private Manager</h1>
            <p className="text-sm text-gray-500 mt-1">Owner access only</p>
          </div>

          <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5 space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">Server URL</label>
              <input
                type="url"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder={DEFAULT_API_BASE_URL}
                className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder:text-gray-600"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">Manager API Key</label>
              <div className="relative">
                <input
                  type={showToken ? 'text' : 'password'}
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder="Value of MANAGER_API_KEY from server .env"
                  onKeyDown={e => e.key === 'Enter' && handleConnect()}
                  className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 pr-10 focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder:text-gray-600"
                />
                <button
                  type="button"
                  onClick={() => setShowToken(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-600 hover:text-gray-400"
                >
                  {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {error && (
              <p className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">{error}</p>
            )}

            <button
              onClick={handleConnect}
              disabled={connecting}
              className="w-full py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-semibold transition"
            >
              {connecting || checking ? 'Connecting…' : 'Connect'}
            </button>
          </div>

          <p className="text-center text-xs text-gray-600 mt-4">
            Token stored in local encrypted Tauri store. Never sent to third parties.
          </p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
