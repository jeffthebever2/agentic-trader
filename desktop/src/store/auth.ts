import { create } from 'zustand'
import { getClient } from '../api/client'

export interface AuthUser {
  email: string
  name: string
  role: 'admin' | 'user' | 'viewer'
  is_admin?: boolean
}

interface AuthState {
  user: AuthUser | null
  initializing: boolean  // true only during the very first load
  checking: boolean      // true during re-checks (don't unmount UI)
  error: string | null
  safeMode: boolean
}

interface AuthActions {
  checkAuth: () => Promise<void>
  logout: () => void
  setSafeMode: (v: boolean) => void
}

export const useAuthStore = create<AuthState & AuthActions>((set, get) => ({
  user: null,
  initializing: true,
  checking: false,
  error: null,
  safeMode: false,

  checkAuth: async () => {
    const isFirst = get().initializing
    // First call: block UI (initializing). Subsequent calls: silent re-check.
    if (isFirst) {
      set({ error: null })
    } else {
      set({ checking: true, error: null })
    }
    try {
      const r = await getClient().get<AuthUser>('/api/auth/me')
      set({ user: r.data, initializing: false, checking: false })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Not authenticated'
      set({ user: null, initializing: false, checking: false, error: msg })
    }
  },

  logout: () => set({ user: null, error: null }),

  setSafeMode: (v) => set({ safeMode: v }),
}))
