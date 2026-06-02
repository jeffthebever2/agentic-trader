import { Shield, ShieldOff } from 'lucide-react'
import { useAuthStore } from '../store/auth'

export function SafeModeBar() {
  const { safeMode, setSafeMode } = useAuthStore()

  if (!safeMode) return null

  return (
    <div className="flex items-center gap-2 rounded-lg bg-yellow-900/30 border border-yellow-700/40 px-3 py-2 text-xs text-yellow-400">
      <Shield className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">Safe Mode active — write actions disabled</span>
      <button
        onClick={() => setSafeMode(false)}
        className="ml-auto flex items-center gap-1 text-yellow-600 hover:text-yellow-400"
      >
        <ShieldOff className="h-3 w-3" />
        Disable
      </button>
    </div>
  )
}
