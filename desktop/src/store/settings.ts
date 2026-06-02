import { create } from 'zustand'
import { load } from '@tauri-apps/plugin-store'
import { configureClient } from '../api/client'
import { DEFAULT_API_BASE_URL } from '@shared/constants'

const STORE_FILE = 'settings.json'

interface DesktopSettings {
  apiBaseUrl: string
  apiToken: string
  loaded: boolean
}

interface SettingsActions {
  load: () => Promise<void>
  save: (patch: Partial<Omit<DesktopSettings, 'loaded'>>) => Promise<void>
}

export const useSettingsStore = create<DesktopSettings & SettingsActions>((set, get) => ({
  apiBaseUrl: DEFAULT_API_BASE_URL,
  apiToken: '',
  loaded: false,

  load: async () => {
    try {
      const store = await load(STORE_FILE, { defaults: { apiBaseUrl: DEFAULT_API_BASE_URL, apiToken: '' } })
      const url = (await store.get<string>('apiBaseUrl')) ?? DEFAULT_API_BASE_URL
      const token = (await store.get<string>('apiToken')) ?? ''
      configureClient(url, token || null)
      set({ apiBaseUrl: url, apiToken: token, loaded: true })
    } catch {
      // Tauri store unavailable in browser dev — use defaults
      configureClient(DEFAULT_API_BASE_URL, null)
      set({ loaded: true })
    }
  },

  save: async (patch) => {
    const next = { ...get(), ...patch }
    set(patch)
    configureClient(next.apiBaseUrl, next.apiToken || null)
    try {
      const store = await load(STORE_FILE, { defaults: { apiBaseUrl: DEFAULT_API_BASE_URL, apiToken: '' } })
      if (patch.apiBaseUrl !== undefined) await store.set('apiBaseUrl', patch.apiBaseUrl)
      if (patch.apiToken !== undefined) await store.set('apiToken', patch.apiToken)
    } catch {
      // ignore in non-Tauri env
    }
  },
}))
