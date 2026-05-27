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
        className={`fixed inset-0 bg-black/60 z-40 transition-opacity ${open ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
        id="portfolio-drawer-backdrop"
      />
      {/* Panel */}
      <div
        id="portfolio-drawer"
        className="fixed top-0 right-0 bottom-0 z-50 flex flex-col"
        style={{
          width,
          background: 'var(--surface)',
          borderLeft: '1px solid var(--surface-rule)',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.25s var(--ease-out)',
          boxShadow: open ? 'var(--shadow-3)' : 'none',
        }}
      >
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: '1px solid var(--surface-rule)' }}>
          <div className="font-semibold text-sm" style={{ color: 'var(--ink)' }}>{title}</div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--ink-faint)',
                     fontSize: 22, cursor: 'pointer', lineHeight: 1, padding: 4 }}
          >×</button>
        </div>
        <div className="flex-1 overflow-auto">
          {children}
        </div>
      </div>
    </>,
    document.body,
  )
}
