import React, { useEffect } from 'react'
import { createPortal } from 'react-dom'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
}

const sizeClass = {
  sm:   'max-w-sm',
  md:   'max-w-lg',
  lg:   'max-w-2xl',
  xl:   'max-w-4xl',
  full: 'max-w-full m-4',
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
      className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-6"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className={`card w-full ${sizeClass[size]} max-h-[90vh] flex flex-col overflow-hidden`}
        style={{ background: 'var(--surface)' }}
      >
        {title && (
          <div className="flex items-center justify-between px-5 py-4 border-b"
               style={{ borderColor: 'var(--surface-rule)' }}>
            <div className="font-semibold text-sm" style={{ color: 'var(--ink)' }}>{title}</div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: 'none', color: 'var(--ink-faint)',
                       fontSize: 18, cursor: 'pointer', lineHeight: 1, padding: 4 }}
            >✕</button>
          </div>
        )}
        <div className="overflow-auto flex-1">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}
