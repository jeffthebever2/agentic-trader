import type { ReactNode } from 'react'
import {
  LayoutGrid, Search, BarChart3, TrendingUp, Clock,
  Landmark, BarChart2, Lightbulb, ClipboardCheck, ShieldCheck, Settings,
  Zap, ScrollText, Layers, Activity,
} from 'lucide-react'

/**
 * Single source of truth for primary navigation. Rendered by the Sidebar and
 * driven into the ⌘K command palette — keep routes here so the two never drift.
 */
export interface NavItem {
  to:        string
  label:     string
  id:        string
  adminOnly?: boolean
  icon:      ReactNode
}

const IC = { size: 17, strokeWidth: 1.75 }

export const NAV: NavItem[] = [
  { to: '/',          id: 'nav-dashboard', label: 'Dashboard',           icon: <LayoutGrid    {...IC} /> },
  { to: '/thematic',  id: 'nav-thematic', label: 'Thematic Portfolio',  icon: <Layers        {...IC} /> },
  { to: '/signals',   id: 'nav-signals',  label: 'Signals',             icon: <Zap           {...IC} /> },
  { to: '/paper',    id: 'nav-paper',     label: 'Paper Trading',       icon: <BarChart3     {...IC} /> },
  { to: '/analyze',  id: 'nav-analyze',   label: 'Analyze',             icon: <Search        {...IC} />, adminOnly: true },
  { to: '/backtest', id: 'nav-backtest',  label: 'Research & Backtest', icon: <TrendingUp    {...IC} />, adminOnly: true },
  { to: '/history',  id: 'nav-history',  label: 'History',              icon: <Clock         {...IC} />, adminOnly: true },
  { to: '/broker',   id: 'nav-broker',   label: 'Real Broker',          icon: <Landmark      {...IC} /> },
  { to: '/performance', id: 'nav-performance', label: 'Performance',       icon: <Activity      {...IC} /> },
  { to: '/ml',       id: 'nav-ml',       label: 'Models & Stats',       icon: <BarChart2     {...IC} />, adminOnly: true },
  { to: '/logs',     id: 'nav-logs',     label: 'Logs',                 icon: <ScrollText    {...IC} />, adminOnly: true },
  { to: '/rl',       id: 'nav-rl',       label: 'RL Agent',             icon: <Lightbulb    {...IC} />, adminOnly: true },
  { to: '/hil',      id: 'nav-hil',      label: 'HIL Approvals',        icon: <ClipboardCheck {...IC} /> },
  { to: '/admin',    id: 'nav-admin',    label: 'Admin',                icon: <ShieldCheck   {...IC} />, adminOnly: true },
  { to: '/settings', id: 'nav-settings', label: 'Settings',             icon: <Settings      {...IC} /> },
]
