import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Command } from 'cmdk'
import { useAuth } from '@/hooks/useAuth'
import { NAV } from '@/components/layout/nav'

/**
 * ⌘K / Ctrl-K command palette. Navigation-first: drives off the same `NAV`
 * the sidebar renders, so routes never drift out of sync. Also opens on a
 * `open-command-palette` document event (wire any button to it).
 *
 * Extra `keywords` make routes findable by what a trader calls them
 * ("holdings" → Broker, "pnl" → Performance) rather than only the menu label.
 */
const KEYWORDS: Record<string, string[]> = {
  '/': ['home', 'overview', 'portfolio'],
  '/broker': ['holdings', 'positions', 'fidelity', 'account', 'shares'],
  '/performance': ['pnl', 'returns', 'equity', 'profit', 'p&l'],
  '/hil': ['approvals', 'approve', 'pending', 'human in the loop'],
  '/paper': ['leaderboard', 'competition', 'portfolios'],
  '/thematic': ['social', 'momentum', 'buzz'],
  '/signals': ['ideas', 'candidates'],
  '/ml': ['model', 'stats', 'machine learning', 'win rate'],
  '/rl': ['reinforcement', 'agent'],
  '/logs': ['activity', 'events'],
  '/settings': ['config', 'preferences'],
}

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const { isAdmin } = useAuth()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen(o => !o)
      }
    }
    const onOpen = () => setOpen(true)
    document.addEventListener('keydown', onKey)
    document.addEventListener('open-command-palette', onOpen)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('open-command-palette', onOpen)
    }
  }, [])

  const items = NAV.filter(n => !n.adminOnly || isAdmin)
  const go = (to: string) => { setOpen(false); navigate(to) }

  return (
    <Command.Dialog open={open} onOpenChange={setOpen} label="Command menu">
      <Command.Input placeholder="Jump to a page…" autoFocus />
      <Command.List>
        <Command.Empty>No matches found.</Command.Empty>
        <Command.Group heading="Navigate">
          {items.map(item => (
            <Command.Item
              key={item.to}
              value={item.label}
              keywords={[item.to.replace('/', '') || 'dashboard', ...(KEYWORDS[item.to] ?? [])]}
              onSelect={() => go(item.to)}
            >
              <span className="cmdk-ic">{item.icon}</span>
              <span className="cmdk-label">{item.label}</span>
              <span className="cmdk-path">{item.to}</span>
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  )
}
