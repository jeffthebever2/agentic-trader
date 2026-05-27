import { create } from 'zustand'

interface LiveState {
  hilPending: number
  paperRunning: boolean
  lastSignalTicker: string | null
  setHilPending: (n: number) => void
  setPaperRunning: (v: boolean) => void
  setLastSignalTicker: (t: string | null) => void
}

export const useLiveStore = create<LiveState>((set) => ({
  hilPending: 0,
  paperRunning: false,
  lastSignalTicker: null,
  setHilPending: (n) => set({ hilPending: n }),
  setPaperRunning: (v) => set({ paperRunning: v }),
  setLastSignalTicker: (t) => set({ lastSignalTicker: t }),
}))
