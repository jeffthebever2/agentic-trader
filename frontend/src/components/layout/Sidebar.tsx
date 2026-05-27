import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
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

// ── Connection status ────────────────────────────────────────────────────────
type ConnState = 'pending' | 'connected' | 'disconnected'

function useConnectionState(): ConnState {
  const [state, setState] = useState<ConnState>('pending')
  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        const r = await fetch('/api/auth/me', { credentials: 'include', signal: AbortSignal.timeout(4000) })
        if (!alive) return
        setState(r.status < 500 ? 'connected' : 'disconnected')
      } catch {
        if (alive) setState('disconnected')
      }
    }
    check()
    const t = setInterval(check, 15_000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  return state
}

const CONN_LABEL: Record<ConnState, string> = {
  pending:      'Connecting...',
  connected:    'Connected',
  disconnected: 'Offline',
}

const CONN_COLORS: Record<ConnState, { dot: string; value: string; border: string; bg: string }> = {
  pending:      { dot: '#8890A4', value: 'var(--ink-muted)', border: 'var(--surface-rule)', bg: 'var(--surface-soft)' },
  connected:    { dot: '#047857', value: '#047857', border: 'rgba(4,120,87,.24)',  bg: 'rgba(52,211,153,.09)' },
  disconnected: { dot: '#b91c1c', value: '#b91c1c', border: 'rgba(185,28,28,.24)', bg: 'rgba(248,113,113,.09)' },
}

interface SidebarProps {
  onOpenOnboarding?: () => void
  mobileOpen?: boolean
  onMobileClose?: () => void
}

export function Sidebar({ onOpenOnboarding, mobileOpen, onMobileClose }: SidebarProps) {
  const { user, isAdmin } = useAuth()
  const { mode, toggle } = useThemeStore()
  const navigate = useNavigate()
  const connState = useConnectionState()
  const conn = CONN_COLORS[connState]

  return (
    <aside
      id="ta-sidebar"
      data-mobile-open={mobileOpen ? 'true' : undefined}
      style={{
        width: 220,
        flexShrink: 0,
        borderRight: '1px solid var(--surface-rule)',
        background: 'var(--surface)',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        position: 'sticky',
        top: 0,
        zIndex: 20,
      }}
    >
      {/* Logo */}
      <div style={{ padding: '16px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <img
          className="ta-brand-mark"
          src="/static/agentic-trader-icon.png"
          alt="Agentic Trader"
          style={{ width: 28, height: 28, flexShrink: 0 }}
          onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
        />
        <div>
          <div style={{ fontWeight: 700, color: 'var(--ink)', fontSize: 14, lineHeight: 1.2, letterSpacing: '-0.01em' }}>
            Agentic Trader
          </div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-faint)', marginTop: 1, fontWeight: 500 }}>
            Personal Trading Suite
          </div>
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
              onClick={onMobileClose}
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

      {/* Footer */}
      <div style={{ padding: 12, borderTop: '1px solid var(--surface-rule)' }}>
        {/* Connection status card */}
        <div
          id="api-status"
          className={`ta-connection-card${connState === 'connected' ? ' live' : ''}`}
          data-state={connState}
          style={{
            minHeight: 42,
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            padding: '8px 10px',
            border: `1px solid ${conn.border}`,
            borderRadius: 8,
            background: conn.bg,
            color: 'var(--ink-faint)',
          }}
        >
          <span
            className="ta-status-dot"
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              background: conn.dot,
              flexShrink: 0,
              position: 'relative',
            }}
          />
          <span style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
            <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
              Local server
            </span>
            <span style={{ fontSize: 12, fontWeight: 700, color: conn.value }}>
              {CONN_LABEL[connState]}
            </span>
          </span>
        </div>

        {/* Auth user info */}
        {user && (
          <div
            id="auth-user"
            style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-faint)', lineHeight: 1.4 }}
          >
            <div
              id="auth-user-name"
              style={{ color: 'var(--ink)', fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
            >
              {user.name || user.email}
            </div>
            <div
              id="auth-user-email"
              style={{ color: 'var(--ink-faint)', fontWeight: 500, fontSize: 10, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
            >
              {user.email}
            </div>
            <div id="auth-user-role" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
              <span
                id="auth-user-role-badge"
                style={{
                  padding: '1px 6px',
                  borderRadius: 99,
                  fontSize: 9,
                  fontWeight: 700,
                  background: 'var(--surface-raised)',
                  color: 'var(--ink-faint)',
                  textTransform: 'uppercase',
                  letterSpacing: '.05em',
                }}
              >
                {user.role}
              </span>
            </div>
          </div>
        )}

        {/* Legal links */}
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, marginBottom: 2 }}>
          <a
            href="mailto:support@agentictrader.org?subject=Agentic%20Trader%20Support"
            style={{ color: 'var(--ink-faint)', fontSize: 11, fontWeight: 600, textDecoration: 'none' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--accent)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--ink-faint)')}
          >
            Support
          </a>
          <button
            onClick={() => navigate('/terms')}
            style={{ color: 'var(--ink-faint)', fontSize: 11, fontWeight: 600, background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--accent)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--ink-faint)')}
          >
            Terms
          </button>
          <button
            onClick={() => navigate('/privacy')}
            style={{ color: 'var(--ink-faint)', fontSize: 11, fontWeight: 600, background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
            onMouseEnter={e => (e.currentTarget.style.color = 'var(--accent)')}
            onMouseLeave={e => (e.currentTarget.style.color = 'var(--ink-faint)')}
          >
            Privacy
          </button>
          <button
            onClick={toggle}
            title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className={`appearance-toggle${mode === 'dark' ? ' dark' : ''}`}
            style={{ marginLeft: 'auto' }}
            aria-label={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            <span className="appearance-toggle-thumb" />
          </button>
        </div>

        {/* Onboarding re-trigger */}
        {user && onOpenOnboarding && (
          <button
            onClick={onOpenOnboarding}
            title="Re-open onboarding tour"
            style={{
              width: '100%', background: 'none', border: 'none',
              padding: 0, cursor: 'pointer', textAlign: 'left',
              marginTop: 6,
            }}
          />
        )}
      </div>
    </aside>
  )
}
