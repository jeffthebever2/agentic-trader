import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type ThemeMode = 'light' | 'dark'

interface ThemeState {
  mode: ThemeMode
  toggle: () => void
  set: (mode: ThemeMode) => void
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      mode: 'dark',
      toggle: () => {
        const next = get().mode === 'dark' ? 'light' : 'dark'
        applyTheme(next)
        set({ mode: next })
      },
      set: (mode: ThemeMode) => {
        applyTheme(mode)
        set({ mode })
      },
    }),
    { name: 'ta-theme' },
  ),
)

function applyTheme(mode: ThemeMode) {
  // Guard: this runs at module load (below) and document.body may not exist yet
  // (script in <head>, SSR, or a bundler eval context). No body → nothing to do.
  const body = typeof document !== 'undefined' ? document.body : null
  if (!body) return
  body.classList.toggle('theme-dark', mode === 'dark')
}

// Apply on store init
const saved = useThemeStore.getState().mode
applyTheme(saved)
