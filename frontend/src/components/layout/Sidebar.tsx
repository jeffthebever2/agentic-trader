import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutGrid, Search, BarChart3, TrendingUp, Clock,
  Landmark, BarChart2, Lightbulb, ClipboardCheck, ShieldCheck, Settings,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '@/hooks/useAuth'
import { useThemeStore } from '@/store/theme'
import { useLiveStore } from '@/store/live'
import api from '@/api/client'

interface NavItem {
  to:        string
  label:     string
  id:        string
  adminOnly?: boolean
  icon:      React.ReactNode
}

const IC = { size: 17, strokeWidth: 1.75 }

const NAV: NavItem[] = [
  { to: '/',         id: 'nav-dashboard', label: 'Dashboard',         icon: <LayoutGrid    {...IC} /> },
  { to: '/analyze',  id: 'nav-analyze',   label: 'Analyze',           icon: <Search        {...IC} />, adminOnly: true },
  { to: '/paper',    id: 'nav-paper',     label: 'Paper Trading',     icon: <BarChart3     {...IC} /> },
  { to: '/backtest', id: 'nav-backtest',  label: 'Backtest & Screener', icon: <TrendingUp  {...IC} />, adminOnly: true },
  { to: '/history',  id: 'nav-history',  label: 'History',            icon: <Clock         {...IC} />, adminOnly: true },
  { to: '/broker',   id: 'nav-broker',   label: 'Real Broker',        icon: <Landmark      {...IC} /> },
  { to: '/ml',       id: 'nav-ml',       label: 'Statistics',         icon: <BarChart2     {...IC} />, adminOnly: true },
  { to: '/rl',       id: 'nav-rl',       label: 'RL Agent',           icon: <Lightbulb     {...IC} />, adminOnly: true },
  { to: '/hil',      id: 'nav-hil',      label: 'HIL Approvals',      icon: <ClipboardCheck {...IC} /> },
  { to: '/admin',    id: 'nav-admin',    label: 'Admin',              icon: <ShieldCheck   {...IC} />, adminOnly: true },
  { to: '/settings', id: 'nav-settings', label: 'Settings',           icon: <Settings      {...IC} /> },
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

// Prefetch config keyed by route path
const PREFETCH_MAP: Record<string, { queryKey: unknown[]; queryFn: () => Promise<unknown> }> = {
  '/':        { queryKey: ['paper', 'status'],  queryFn: () => api.get('/paper/status').then(r => r.data) },
  '/paper':   { queryKey: ['paper', 'status'],  queryFn: () => api.get('/paper/status').then(r => r.data) },
  '/history': { queryKey: ['history-stats'],    queryFn: () => api.get('/history/stats').then(r => r.data) },
  '/ml':      { queryKey: ['ml', 'status'],     queryFn: () => api.get('/ml/status').then(r => r.data) },
  '/hil':     { queryKey: ['hil', 'pending'],   queryFn: () => api.get('/hil/pending').then(r => r.data) },
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
  const qc = useQueryClient()
  const hilPending = useLiveStore(s => s.hilPending)

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
          const prefetch = PREFETCH_MAP[item.to]
          const isHil = item.to === '/hil'
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
              onMouseEnter={() => {
                if (prefetch) {
                  qc.prefetchQuery({ queryKey: prefetch.queryKey, queryFn: prefetch.queryFn })
                }
              }}
            >
              {item.icon}
              <span style={{ flex: 1 }}>{item.label}</span>
              {isHil && hilPending > 0 && (
                <span style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minWidth: 18,
                  height: 18,
                  borderRadius: 999,
                  background: '#f59e0b',
                  color: '#fff',
                  fontSize: 10,
                  fontWeight: 700,
                  padding: '0 4px',
                  flexShrink: 0,
                }}>
                  {hilPending}
                </span>
              )}
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
