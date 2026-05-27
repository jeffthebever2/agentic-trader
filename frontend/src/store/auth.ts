import { create } from 'zustand'
import type { User, AuthFeatures } from '@/types'

interface AuthState {
  user: User | null
  features: AuthFeatures | null
  loading: boolean
  isAdmin: () => boolean
  setUser: (user: User | null) => void
  setFeatures: (features: AuthFeatures | null) => void
  setLoading: (loading: boolean) => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user:     null,
  features: null,
  loading:  true,
  isAdmin:  () => get().user?.role === 'admin',
  setUser:     user     => set({ user }),
  setFeatures: features => set({ features }),
  setLoading:  loading  => set({ loading }),
}))
