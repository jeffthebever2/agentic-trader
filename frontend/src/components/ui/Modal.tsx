import React, { useEffect } from 'react'
import { createPortal } from 'react-dom'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
}

const sizeMap: Record<string, string> = {
  sm:   '420px',
  md:   '560px',
  lg:   '720px',
  xl:   '960px',
  full: 'calc(100vw - 32px)',
}

export function Modal({ open, onClose, title, children, size = 'md' }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
      className="ta-backdrop-blur backdrop-fade"
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,.55)',
        zIndex: 1050,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 24,
      }}
    >
      <div
        className="card modal-pop"
        style={{
          background: 'var(--surface)',
          width: '100%',
          maxWidth: sizeMap[size] ?? sizeMap.md,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {title && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--surface-rule)',
          }}>
            <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink)' }}>{title}</div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: 'none', color: 'var(--ink-faint)',
                       fontSize: 18, cursor: 'pointer', lineHeight: 1, padding: 4 }}
            >✕</button>
          </div>
        )}
        <div style={{ overflow: 'auto', flex: 1 }}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
