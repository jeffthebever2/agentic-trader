import { NavLink } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useThemeStore } from '@/store/theme'

interface NavItem {
  to:        string
  label:     string
  id:        string
  adminOnly?: boolean
  icon:      React.ReactNode
}

const NAV: NavItem[] = [
  {
    to: '/', id: 'nav-dashboard', label: 'Dashboard',
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>,
  },
  {
    to: '/analyze', id: 'nav-analyze', label: 'Analyze', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>,
  },
  {
    to: '/paper', id: 'nav-paper', label: 'Paper Trading',
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path d="M4 19V5"/><path d="M4 19h16"/><rect x="7" y="10" width="3" height="6" rx="1"/><rect x="12" y="7" width="3" height="9" rx="1"/><rect x="17" y="4" width="3" height="12" rx="1"/></svg>,
  },
  {
    to: '/backtest', id: 'nav-backtest', label: 'Backtest & Screener', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>,
  },
  {
    to: '/history', id: 'nav-history', label: 'History', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>,
  },
  {
    to: '/broker', id: 'nav-broker', label: 'Real Broker',
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><line x1="3" x2="21" y1="22" y2="22"/><line x1="6" x2="6" y1="18" y2="11"/><line x1="10" x2="10" y1="18" y2="11"/><line x1="14" x2="14" y1="18" y2="11"/><line x1="18" x2="18" y1="18" y2="11"/><polygon points="12 2 20 7 4 7"/></svg>,
  },
  {
    to: '/ml', id: 'nav-ml', label: 'Statistics', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>,
  },
  {
    to: '/rl', id: 'nav-rl', label: 'RL Agent', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path d="M9.663 17h4.673M12 3v1m6.364 1.636-.707.707M21 12h-1M4 12H3m3.343-5.657-.707-.707m2.828 9.9a5 5 0 1 1 7.072 0l-.548.547A3.374 3.374 0 0 0 14 18.469V19a2 2 0 1 1-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>,
  },
  {
    to: '/hil', id: 'nav-hil', label: 'HIL Approvals',
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>,
  },
  {
    to: '/admin', id: 'nav-admin', label: 'Admin', adminOnly: true,
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3z"/><path d="M9 12l2 2 4-4"/></svg>,
  },
  {
    to: '/settings', id: 'nav-settings', label: 'Settings',
    icon: <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>,
  },
]

interface SidebarProps {
  onOpenOnboarding?: () => void
}

export function Sidebar({ onOpenOnboarding }: SidebarProps) {
  const { user, isAdmin } = useAuth()
  const { mode, toggle } = useThemeStore()

  return (
    <aside
      style={{
        width: 200,
        flexShrink: 0,
        borderRight: '1px solid var(--surface-rule)',
        background: 'var(--surface)',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
      }}
    >
      {/* Logo */}
      <div style={{ padding: '16px 12px 8px', borderBottom: '1px solid var(--surface-rule)' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
          Trading Agents
        </div>
        <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 2 }}>
          Paper · Shadow · Research
        </div>
      </div>

      {/* Nav */}
      <nav style={{ padding: '10px 8px', flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV.map(item => {
          if (item.adminOnly && !isAdmin) return null
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              id={item.id}
              className={({ isActive }) =>
                `nav-item${isActive ? ' active' : ''}`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          )
        })}
      </nav>

      {/* Footer: user info + theme toggle */}
      <div style={{ padding: 12, borderTop: '1px solid var(--surface-rule)', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {user && (
          <button
            onClick={onOpenOnboarding}
            title="Re-open onboarding tour"
            style={{
              width: '100%', background: 'none', border: '1px solid var(--surface-rule)',
              borderRadius: 6, padding: '6px 8px', cursor: 'pointer', textAlign: 'left',
            }}
          >
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>{user.role}</div>
            <div style={{ fontSize: 11, color: 'var(--ink-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} id="auth-user-name">{user.name || user.email}</div>
          </button>
        )}
        <button
          onClick={toggle}
          style={{
            width: '100%', background: 'var(--surface-raised)', border: '1px solid var(--surface-rule)',
            borderRadius: 6, color: 'var(--ink-muted)', fontSize: 11, padding: '5px 8px',
            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          {mode === 'dark' ? '☀ Light mode' : '🌙 Dark mode'}
        </button>
      </div>
    </aside>
  )
}
