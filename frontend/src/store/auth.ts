import { create } from 'zustand'
import type { User, AuthFeatures } from '@/types'

interface AuthState {
  user: User | null
  realUser: User | null
  features: AuthFeatures | null
  loading: boolean
  isAdmin: () => boolean
  previewStandardUser: () => void
  restoreAdminView: () => void
  setUser: (user: User | null) => void
  setFeatures: (features: AuthFeatures | null) => void
  setLoading: (loading: boolean) => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user:     null,
  realUser: null,
  features: null,
  loading:  true,
  isAdmin:  () => get().user?.role === 'admin' || get().user?.is_admin === true,
  previewStandardUser: () => {
    const { user, realUser } = get()
    if (!user || realUser || !(user.role === 'admin' || user.is_admin)) return
    set({
      realUser: user,
      user: {
        ...user,
        role: 'user',
        is_admin: false,
        viewed_by_admin: true,
        actual_admin_email: user.email,
        email: 'standard user',
        name: 'Standard User Preview',
      },
    })
  },
  restoreAdminView: () => {
    const { realUser } = get()
    if (realUser) set({ user: realUser, realUser: null })
  },
  setUser:     user     => set({ user }),
  setFeatures: features => set({ features }),
  setLoading:  loading  => set({ loading }),
}))
