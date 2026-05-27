import React, { useEffect } from 'react'
import { createPortal } from 'react-dom'

interface DrawerProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  width?: string
}

export function Drawer({ open, onClose, title, children, width = '420px' }: DrawerProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        id="portfolio-drawer-backdrop"
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,.6)',
          zIndex: 1040,
          opacity: open ? 1 : 0,
          pointerEvents: open ? 'auto' : 'none',
          transition: 'opacity .2s',
        }}
      />
      {/* Panel */}
      <div
        id="portfolio-drawer"
        style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          zIndex: 1050,
          width,
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--surface)',
          borderLeft: '1px solid var(--surface-rule)',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.25s var(--ease-out)',
          boxShadow: open ? 'var(--shadow-3)' : 'none',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 20px',
          borderBottom: '1px solid var(--surface-rule)',
        }}>
          <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink)' }}>{title}</div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--ink-faint)',
                     fontSize: 22, cursor: 'pointer', lineHeight: 1, padding: 4 }}
          >×</button>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {children}
        </div>
      </div>
    </>,
    document.body,
  )
}
