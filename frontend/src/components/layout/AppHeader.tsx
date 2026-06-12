import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Search, TrendingUp } from 'lucide-react'
import { useOverlayStore } from '@/components/ui/GlobalOverlays'

// ── Route → title / kicker ──────────────────────────────────────────────────
const PAGE_META: Record<string, { title: string; kicker: string }> = {
  '/':         { title: 'Dashboard',            kicker: 'Live cockpit'       },
  '/analyze':  { title: 'Analyze',              kicker: 'AI stock analysis'  },
  '/paper':    { title: 'Paper Trading',        kicker: 'Simulated trading'  },
  '/backtest': { title: 'Backtest & Screener',  kicker: 'Historical testing' },
  '/history':  { title: 'History',              kicker: 'Analysis records'   },
  '/broker':   { title: 'Real Broker',          kicker: 'Fidelity · Webull'  },
  '/ml':       { title: 'Machine Learning',     kicker: 'Model & portfolio'  },
  '/rl':       { title: 'RL Agent',             kicker: 'Reinforcement learning' },
  '/hil':      { title: 'HIL Approvals',        kicker: 'Human-in-the-loop'  },
  '/admin':    { title: 'Admin Console',        kicker: 'System management'  },
  '/settings': { title: 'Settings',             kicker: 'Configuration'      },
  '/terms':    { title: 'Terms of Service',     kicker: 'Legal'              },
  '/privacy':  { title: 'Privacy Policy',       kicker: 'Legal'              },
}

function formatDateTime(d: Date) {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    + '  '
    + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' })
}

interface AppHeaderProps {
  onToggleSidebar?: () => void
}

export function AppHeader({ onToggleSidebar }: AppHeaderProps) {
  const { pathname } = useLocation()
  const { setCmd } = useOverlayStore()
  const meta = PAGE_META[pathname] ?? { title: pathname.slice(1) || 'Dashboard', kicker: '' }

  const [datetime, setDatetime] = useState(() => formatDateTime(new Date()))
  const [cmdValue, setCmdValue] = useState('')
  const cmdRef = useRef<HTMLInputElement>(null)

  // Clock tick
  useEffect(() => {
    const t = setInterval(() => setDatetime(formatDateTime(new Date())), 1000)
    return () => clearInterval(t)
  }, [])

  // If command palette opens externally, blur local input
  const handleCmdFocus = () => {
    cmdRef.current?.blur()
    setCmd(true)
  }

  const openTVChart = () => {
    const ticker = cmdValue.trim().toUpperCase() || 'SPY'
    window.open(`https://www.tradingview.com/chart/?symbol=${ticker}`, '_blank')
  }

  return (
    <header
      id="ta-app-header"
      style={{
        background: 'var(--surface)',
        borderBottom: '1px solid var(--surface-rule)',
        padding: '0 24px',
        minHeight: 52,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
        gap: 12,
      }}
    >
      {/* Left: hamburger + title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          id="mobile-menu-btn"
          onClick={onToggleSidebar}
          aria-label="Toggle navigation menu"
          style={{
            display: 'none', // shown via CSS at <980px
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 5,
            width: 32,
            height: 32,
            background: 'none',
            border: '1px solid var(--surface-rule)',
            borderRadius: 6,
            cursor: 'pointer',
            padding: 0,
            flexShrink: 0,
          }}
          className="mobile-menu-btn"
        >
          <span style={{ display: 'block', width: 16, height: 1.5, background: 'var(--ink)', borderRadius: 2 }} />
          <span style={{ display: 'block', width: 16, height: 1.5, background: 'var(--ink)', borderRadius: 2 }} />
          <span style={{ display: 'block', width: 16, height: 1.5, background: 'var(--ink)', borderRadius: 2 }} />
        </button>

        {/* Mobile logo — only visible on narrow screens */}
        <img
          className="ta-mobile-logo"
          src="/app/agentic-trader-icon.png"
          alt="Agentic Trader"
          style={{ display: 'none' }}
          onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
        />

        <div key={pathname} className="stat-swap">
          <h1
            id="page-title"
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--ink)',
              letterSpacing: '-0.02em',
              margin: 0,
              lineHeight: 1.2,
            }}
          >
            {meta.title}
          </h1>
          {meta.kicker && (
            <div
              className="ta-page-kicker"
              style={{
                marginTop: 2,
                fontSize: 10,
                lineHeight: 1,
                color: 'var(--ink-faint)',
                fontWeight: 700,
                letterSpacing: '.11em',
                textTransform: 'uppercase',
              }}
            >
              {meta.kicker}
            </div>
          )}
        </div>
      </div>

      {/* Mobile search button — only visible on narrow screens, opens command palette */}
      <button
        id="ta-mobile-search-btn"
        type="button"
        aria-label="Search actions"
        onClick={handleCmdFocus}
        style={{
          display: 'none', // shown via CSS at <980px
          width: 36,
          height: 36,
          background: 'var(--surface-soft)',
          border: '1px solid var(--surface-rule)',
          borderRadius: 8,
          cursor: 'pointer',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          marginLeft: 'auto',
        }}
        className="mobile-search-btn"
      >
        <Search size={18} strokeWidth={2} aria-hidden="true" />
      </button>

      {/* Command bar */}
      <div
        className="ta-commandbar"
        role="search"
        style={{
          width: 'min(440px, 38vw)',
          minWidth: 250,
          height: 38,
          display: 'flex',
          alignItems: 'center',
          gap: 9,
          padding: '0 10px 0 12px',
          border: '1px solid var(--surface-rule)',
          borderRadius: 8,
          background: 'var(--surface-soft)',
          color: 'var(--ink-faint)',
          cursor: 'text',
          flexShrink: 1,
        }}
        onClick={handleCmdFocus}
      >
        <Search size={14} strokeWidth={2} aria-hidden="true" />
        <input
          ref={cmdRef}
          value={cmdValue}
          onChange={e => setCmdValue(e.target.value)}
          placeholder="Search action or ticker"
          aria-label="Search action or ticker"
          id="global-command"
          style={{
            width: '100%',
            height: '100%',
            border: 0,
            outline: 0,
            background: 'transparent',
            color: 'var(--ink)',
            font: '600 12.5px/1 var(--font-sans)',
            cursor: 'text',
          }}
          onFocus={handleCmdFocus}
        />
        <kbd style={{
          minWidth: 20,
          height: 20,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: '1px solid var(--surface-rule)',
          borderRadius: 5,
          background: 'var(--surface)',
          color: 'var(--ink-faint)',
          font: '700 10px/1 var(--font-mono)',
          flexShrink: 0,
        }}>⌘K</kbd>
      </div>

      {/* Right: market chip + chart btn + datetime */}
      <div
        className="ta-header-actions"
        style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}
      >
        <div
          className="ta-market-chip"
          title="Connection context for this dashboard"
          style={{
            minHeight: 32,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            padding: '0 10px',
            border: '1px solid var(--surface-rule)',
            borderRadius: 999,
            background: 'var(--surface-soft)',
            color: 'var(--ink-muted)',
            fontSize: 11,
            fontWeight: 750,
            whiteSpace: 'nowrap',
          }}
        >
          <span
            className="ta-market-pulse"
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              background: 'var(--accent)',
              flexShrink: 0,
              position: 'relative',
            }}
          />
          <span id="ta-preview-label">Preview</span>
        </div>

        <button
          className="btn-secondary"
          title="Open interactive TradingView chart"
          onClick={openTVChart}
          style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px' }}
        >
          <TrendingUp size={13} strokeWidth={2} color="var(--accent)" aria-hidden="true" />
          Chart
        </button>

        <span
          id="current-datetime"
          style={{ fontSize: 11.5, color: 'var(--ink-faint)', fontWeight: 500, whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.02em' }}
        >
          {datetime}
        </span>
      </div>
    </header>
  )
}
