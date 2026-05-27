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
  if (mode === 'dark') {
    document.body.classList.add('theme-dark')
  } else {
    document.body.classList.remove('theme-dark')
  }
}

// Apply on store init
const saved = useThemeStore.getState().mode
applyTheme(saved)
