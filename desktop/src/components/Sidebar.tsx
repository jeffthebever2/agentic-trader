import { NavLink } from 'react-router-dom'
import {
  Home, HeartPulse, Server, ScrollText, Rocket, Brain, Database, Settings,
  Shield, ShieldOff,
} from 'lucide-react'
import { useAuthStore } from '../store/auth'

const NAV = [
  { to: '/',           label: 'Home',          icon: Home },
  { to: '/health',     label: 'App Health',    icon: HeartPulse },
  { to: '/services',   label: 'Services',      icon: Server },
  { to: '/logs',       label: 'Logs',          icon: ScrollText },
  { to: '/deploy',     label: 'Deployments',   icon: Rocket },
  { to: '/models',     label: 'AI / Models',   icon: Brain },
  { to: '/data',       label: 'Data Sources',  icon: Database },
  { to: '/settings',   label: 'Settings',      icon: Settings },
]

export function Sidebar() {
  const { user, safeMode, setSafeMode } = useAuthStore()

  return (
    <aside className="w-52 shrink-0 bg-gray-950 border-r border-gray-800 flex flex-col">
      {/* Header */}
      <div className="px-4 py-5 border-b border-gray-800">
        <p className="text-sm font-bold text-white tracking-tight">Private Manager</p>
        <p className="text-xs text-gray-600 mt-0.5">Owner Console · {user?.role ?? 'unknown'}</p>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                isActive
                  ? 'bg-indigo-600/20 text-indigo-300 ring-1 ring-inset ring-indigo-600/20'
                  : 'text-gray-400 hover:bg-gray-800/80 hover:text-white'
              }`
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer: safe mode + version */}
      <div className="px-3 py-3 border-t border-gray-800 space-y-2">
        <button
          onClick={() => setSafeMode(!safeMode)}
          className={`w-full flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs transition ${
            safeMode
              ? 'bg-yellow-500/10 text-yellow-400 ring-1 ring-yellow-600/30'
              : 'text-gray-600 hover:text-gray-400 hover:bg-gray-800'
          }`}
        >
          {safeMode ? <Shield className="h-3 w-3" /> : <ShieldOff className="h-3 w-3" />}
          {safeMode ? 'Safe Mode ON' : 'Safe Mode off'}
        </button>
        <p className="text-xs text-gray-700 px-1">v0.1.0</p>
      </div>
    </aside>
  )
}
