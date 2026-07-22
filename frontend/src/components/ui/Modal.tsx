import React, { useEffect, useId, useRef } from 'react'
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

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'

export function Modal({ open, onClose, title, children, size = 'md' }: ModalProps) {
  const contentRef = useRef<HTMLDivElement>(null)
  const titleId = useId()

  useEffect(() => {
    if (!open) return
    const prevFocus = document.activeElement as HTMLElement | null
    // Move focus into the dialog on open (keyboard + screen-reader users land here).
    contentRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key !== 'Tab') return
      const el = contentRef.current
      if (!el) return
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(n => n.offsetParent !== null)
      if (items.length === 0) { e.preventDefault(); return }
      const first = items[0], last = items[items.length - 1], active = document.activeElement
      if (e.shiftKey && (active === first || active === el)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      prevFocus?.focus?.()  // restore focus to the trigger on close
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
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
        ref={contentRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className="card modal-pop"
        style={{
          background: 'var(--surface)',
          width: '100%',
          maxWidth: sizeMap[size] ?? sizeMap.md,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          outline: 'none',
        }}
      >
        {title && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--surface-rule)',
          }}>
            <h2 id={titleId} style={{ fontWeight: 600, fontSize: 13, color: 'var(--ink)', margin: 0 }}>{title}</h2>
            <button
              onClick={onClose}
              aria-label="Close"
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
