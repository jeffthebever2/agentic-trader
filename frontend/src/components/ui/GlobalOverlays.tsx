/* eslint-disable react-refresh/only-export-components */
/**
 * Global keyboard-driven overlays:
 *  - PositionSizer   (⌘⇧S)
 *  - KeyboardShortcuts (?)
 *  - CommandPalette  (⌘K)
 * Plus the global keyboard shortcut listener (navigation + actions).
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { create } from 'zustand'
import { useNavigate } from 'react-router-dom'
import { Calculator, Search } from 'lucide-react'
import { showToast } from './Toast'

// ── Store ─────────────────────────────────────────────────────────────────────
interface OverlayState {
  sizer: boolean
  kb: boolean
  cmd: boolean
  setSizer: (v: boolean) => void
  setKb: (v: boolean) => void
  setCmd: (v: boolean) => void
  closeAll: () => void
}

export const useOverlayStore = create<OverlayState>(set => ({
  sizer: false, kb: false, cmd: false,
  setSizer: v => set({ sizer: v }),
  setKb:    v => set({ kb: v }),
  setCmd:   v => set({ cmd: v }),
  closeAll: ()  => set({ sizer: false, kb: false, cmd: false }),
}))

// ── Shared styles ─────────────────────────────────────────────────────────────
const backdropStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 80,
  background: 'rgba(0,0,0,.5)', backdropFilter: 'blur(3px)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
}
const boxStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--surface-rule)',
  borderRadius: 14, boxShadow: '0 30px 80px rgba(0,0,0,.35)',
  overflow: 'hidden',
}
const headerStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '16px 20px', borderBottom: '1px solid var(--surface-rule)',
}
const closeBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: 'var(--ink-faint)',
  fontSize: 20, lineHeight: 1, cursor: 'pointer',
}
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '8px 10px', background: 'var(--surface-soft)',
  border: '1px solid var(--surface-rule)', borderRadius: 6,
  color: 'var(--ink)', fontSize: 14, fontFamily: 'var(--font-mono)',
  boxSizing: 'border-box',
}
const labelStyle: React.CSSProperties = {
  fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4,
}

// ── Position Sizer ────────────────────────────────────────────────────────────
export function PositionSizer() {
  const { sizer, setSizer } = useOverlayStore()
  const [acct, setAcct]   = useState('10000')
  const [risk, setRisk]   = useState('1')
  const [entry, setEntry] = useState('')
  const [stop, setStop]   = useState('')

  if (!sizer) return null

  const RISK_PRESETS = [0.5, 1, 2, 5]

  const calc = () => {
    const a = parseFloat(acct) || 0
    const r = parseFloat(risk) || 1
    const e = parseFloat(entry) || 0
    const s = parseFloat(stop) || 0
    if (!a || !e || !s || e <= s) return null
    const riskD = a * (r / 100)
    const rps   = e - s
    const sh    = Math.floor(riskD / rps)
    const posV  = sh * e
    const pct   = (posV / a) * 100
    return { sh, posV, riskD, pct }
  }

  const result = calc()

  return (
    <div style={backdropStyle} id="ta-sizer-bg" onClick={e => { if (e.target === e.currentTarget) setSizer(false) }}>
      <div style={{ ...boxStyle, width: 'min(400px, 100%)' }}>
        <div style={headerStyle}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Calculator size={15} strokeWidth={2} color="var(--accent)" />
            Position Sizer
          </h2>
          <button style={closeBtn} onClick={() => setSizer(false)}>×</button>
        </div>
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={labelStyle}>Account Size ($)</label>
              <input style={inputStyle} type="number" value={acct} onChange={e => setAcct(e.target.value)} placeholder="10000" />
            </div>
            <div>
              <label style={labelStyle}>Risk Per Trade (%)</label>
              <input style={inputStyle} type="number" value={risk} min="0.1" max="100" step="0.1" onChange={e => setRisk(e.target.value)} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {RISK_PRESETS.map(p => (
              <button key={p} onClick={() => setRisk(String(p))} style={{
                padding: '4px 10px', borderRadius: 4, border: '1px solid',
                background: parseFloat(risk) === p ? 'var(--accent)' : 'var(--surface-raised)',
                color: parseFloat(risk) === p ? '#fff' : 'var(--ink-muted)',
                borderColor: parseFloat(risk) === p ? 'var(--accent)' : 'var(--surface-rule)',
                fontWeight: 600, fontSize: 12, cursor: 'pointer',
              }}>{p}%</button>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={labelStyle}>Entry Price ($)</label>
              <input style={inputStyle} type="number" step="0.01" value={entry} onChange={e => setEntry(e.target.value)} placeholder="150.00" />
            </div>
            <div>
              <label style={labelStyle}>Stop Price ($)</label>
              <input style={inputStyle} type="number" step="0.01" value={stop} onChange={e => setStop(e.target.value)} placeholder="145.00" />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, background: 'var(--surface-raised)', borderRadius: 8, padding: 14 }}>
            {[
              { label: 'Shares', value: result ? result.sh.toLocaleString() : '—', hot: true },
              { label: 'Position Value', value: result ? '$' + result.posV.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—' },
              { label: 'Risk ($)', value: result ? '$' + result.riskD.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—' },
              { label: '% of Portfolio', value: result ? result.pct.toFixed(1) + '%' : '—' },
            ].map(m => (
              <div key={m.label}>
                <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 2 }}>{m.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: m.hot ? 'var(--accent)' : 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{m.value}</div>
              </div>
            ))}
            <div style={{ gridColumn: '1 / -1' }}>
              <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginBottom: 6 }}>Portfolio Allocation</div>
              <div style={{ height: 8, background: 'var(--surface-rule)', borderRadius: 99, overflow: 'hidden' }}>
	                <div style={{
	                  height: '100%', width: '100%', borderRadius: 99, transformOrigin: 'left center',
	                  transform: `scaleX(${result ? Math.min(result.pct, 100) / 100 : 0})`,
	                  transition: 'transform .3s var(--ease-out), background .3s var(--ease-out)',
	                  background: !result ? 'var(--accent)' : result.pct > 25 ? '#ef4444' : result.pct > 15 ? '#f59e0b' : 'var(--accent)',
	                }} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Keyboard Shortcuts ────────────────────────────────────────────────────────
const KB_SHORTCUTS = [
  { cat: 'Navigation (G then…)', items: [
    { desc: 'Dashboard',     keys: ['G','D'] },
    { desc: 'Analyze',       keys: ['G','A'] },
    { desc: 'Paper Trading', keys: ['G','T'] },
    { desc: 'Backtest',      keys: ['G','B'] },
    { desc: 'History',       keys: ['G','H'] },
    { desc: 'Broker',        keys: ['G','K'] },
    { desc: 'ML',            keys: ['G','M'] },
    { desc: 'Settings',      keys: ['G','S'] },
  ]},
  { cat: 'Actions', items: [
    { desc: 'Command Palette', keys: ['⌘','K'] },
    { desc: 'Position Sizer',  keys: ['⌘','⇧','S'] },
    { desc: 'Run Analysis',    keys: ['⌘','↵'] },
    { desc: 'This help',       keys: ['?'] },
    { desc: 'Close / Cancel',  keys: ['Esc'] },
  ]},
]

export function KeyboardShortcuts() {
  const { kb, setKb } = useOverlayStore()
  if (!kb) return null
  return (
    <div style={backdropStyle} onClick={e => { if (e.target === e.currentTarget) setKb(false) }}>
      <div style={{ ...boxStyle, width: 'min(520px, 100%)' }}>
        <div style={headerStyle}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', margin: 0 }}>Keyboard Shortcuts</h2>
          <button style={closeBtn} onClick={() => setKb(false)}>×</button>
        </div>
        <div style={{ padding: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          {KB_SHORTCUTS.map(section => (
            <div key={section.cat}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink-faint)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 10 }}>
                {section.cat}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {section.items.map(item => (
                  <div key={item.desc} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{item.desc}</span>
                    <span style={{ display: 'flex', gap: 3 }}>
                      {item.keys.map((k, i) => (
                        <kbd key={i} style={{
                          padding: '2px 7px', background: 'var(--surface-raised)',
                          border: '1px solid var(--surface-rule)', borderRadius: 4,
                          fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--ink)',
                          fontWeight: 600,
                        }}>{k}</kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Command Palette ───────────────────────────────────────────────────────────
const CMD_PAGES = [
  { id: '/',         label: 'Dashboard',          sub: 'Overview & market feed',    icon: '⊞' },
  { id: '/analyze',  label: 'Analyze',             sub: 'AI stock analysis',         icon: '⌕' },
  { id: '/paper',    label: 'Paper Trading',       sub: 'Simulated trading',         icon: '◈' },
  { id: '/backtest', label: 'Backtest & Screener', sub: 'Historical testing',        icon: '↗' },
  { id: '/history',  label: 'History',             sub: 'Analysis records',          icon: '◷' },
  { id: '/broker',   label: 'Real Broker',         sub: 'Fidelity or Webull',        icon: '$' },
  { id: '/ml',       label: 'Machine Learning',    sub: 'Model training & status',   icon: '◎' },
  { id: '/rl',       label: 'RL Agent',            sub: 'Reinforcement learning',    icon: '⚡' },
  { id: '/hil',      label: 'HIL Approvals',       sub: 'Human-in-the-loop trades',  icon: '✓' },
  { id: '/settings', label: 'Settings',            sub: 'API keys & preferences',    icon: '⚙' },
  { id: '/admin',    label: 'Admin',               sub: 'System administration',     icon: '🛡' },
]

export function CommandPalette() {
  const { cmd, setCmd, setSizer, setKb } = useOverlayStore()
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 40)
  }, [])

  const filtered = q
    ? CMD_PAGES.filter(p =>
        p.label.toLowerCase().includes(q.toLowerCase()) ||
        p.sub.toLowerCase().includes(q.toLowerCase())
      )
    : CMD_PAGES

  const actions = q === '' ? [
    { label: 'Position Sizer',    sub: 'Risk-based share calculator', icon: '⊟', fn: () => { setCmd(false); setSizer(true) } },
    { label: 'Keyboard Shortcuts',sub: 'View all hotkeys',            icon: '⌨', fn: () => { setCmd(false); setKb(true) } },
  ] : []

  const allItems = [
    ...filtered.map(p => ({ ...p, type: 'page' as const })),
    ...actions.map(a => ({ id: a.label, label: a.label, sub: a.sub, icon: a.icon, type: 'action' as const, fn: a.fn })),
  ]

  const exec = (item: typeof allItems[0]) => {
    if (item.type === 'action' && 'fn' in item) {
      (item as { fn: () => void }).fn()
    } else {
      navigate(item.id)
      showToast(`→ ${item.label}`, 'info', '', 1200)
    }
    setCmd(false)
  }

  if (!cmd) return null

  return (
    <div style={{ ...backdropStyle, alignItems: 'flex-start', paddingTop: '15vh' }}
         onClick={e => { if (e.target === e.currentTarget) setCmd(false) }}>
      <div style={{ ...boxStyle, width: 'min(560px, 100%)', maxHeight: '60vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <Search size={14} strokeWidth={2} color="var(--ink-faint)" />
          <input
            ref={inputRef}
            value={q}
            onChange={e => { setQ(e.target.value); setIdx(0) }}
            placeholder="Search pages and actions…"
            style={{ flex: 1, background: 'none', border: 'none', outline: 'none', fontSize: 15, color: 'var(--ink)', fontFamily: 'inherit' }}
            onKeyDown={e => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setIdx(i => Math.min(i + 1, allItems.length - 1)) }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx(i => Math.max(i - 1, 0)) }
              else if (e.key === 'Enter') { if (allItems[idx]) exec(allItems[idx]) }
              else if (e.key === 'Escape') setCmd(false)
            }}
          />
          <kbd style={{ padding: '2px 6px', background: 'var(--surface-raised)', border: '1px solid var(--surface-rule)', borderRadius: 4, fontSize: 11, color: 'var(--ink-faint)' }}>Esc</kbd>
        </div>
        <div style={{ overflowY: 'auto', padding: 6 }}>
          {allItems.length === 0 ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', fontSize: 13, color: 'var(--ink-faint)' }}>No results</div>
          ) : allItems.map((item, i) => (
            <div
              key={item.id}
              onClick={() => exec(item)}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px',
                borderRadius: 8, cursor: 'pointer',
                background: i === idx ? 'var(--surface-raised)' : 'transparent',
              }}
              onMouseEnter={() => setIdx(i)}
            >
              <span style={{ fontSize: 16, width: 20, textAlign: 'center', flexShrink: 0 }}>{item.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{item.label}</div>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.sub}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Global keyboard listener ──────────────────────────────────────────────────
export function GlobalKeyboardShortcuts() {
  const { closeAll, setSizer, setKb, setCmd } = useOverlayStore()
  const navigate = useNavigate()
  const gPending = useRef(false)
  const gTimer   = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const onKey = useCallback((e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement)?.tagName?.toLowerCase() ?? ''
    const inInput = tag === 'input' || tag === 'textarea' || tag === 'select'
      || (e.target as HTMLElement)?.isContentEditable

    if (e.key === 'Escape') { closeAll(); return }

    // ⌘K — command palette
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setCmd(true); return }

    // ⌘⇧S — position sizer
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 's' || e.key === 'S')) {
      e.preventDefault(); setSizer(true); return
    }

    if (inInput) return

    // ? — keyboard help
    if (e.key === '?') { e.preventDefault(); setKb(true); return }

    // G navigation chord
    if (e.key === 'g' || e.key === 'G') {
      gPending.current = true
      clearTimeout(gTimer.current)
      gTimer.current = setTimeout(() => { gPending.current = false }, 1500)
      return
    }

    if (gPending.current) {
      clearTimeout(gTimer.current)
      gPending.current = false
      const map: Record<string, string> = {
        d: '/', a: '/analyze', t: '/paper', b: '/backtest',
        h: '/history', k: '/broker', m: '/ml', r: '/rl', s: '/settings',
      }
      const dest = map[e.key.toLowerCase()]
      if (dest) {
        navigate(dest)
        showToast('→ ' + dest.replace('/', '') || 'dashboard', 'info', '', 1200)
      }
    }
  }, [closeAll, setSizer, setKb, setCmd, navigate])

  useEffect(() => {
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onKey])

  return null
}

// ── Bundle export ─────────────────────────────────────────────────────────────
export function GlobalOverlays() {
  const { cmd } = useOverlayStore()
  return (
    <>
      <GlobalKeyboardShortcuts />
      <PositionSizer />
      <KeyboardShortcuts />
      <CommandPalette key={cmd ? 'open' : 'closed'} />
    </>
  )
}
