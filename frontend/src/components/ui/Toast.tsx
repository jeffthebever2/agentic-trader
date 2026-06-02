/* eslint-disable react-refresh/only-export-components */
/**
 * Global toast notification system.
 * Usage: const { toast } = useToast()  →  toast.success('msg')
 * Or import showToast directly for non-component use.
 */
import { useRef } from 'react'
import { create } from 'zustand'
import { CheckCircle, XCircle, AlertTriangle, Info } from 'lucide-react'

type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  msg: string
  type: ToastType
  title?: string
}

interface ToastStore {
  items: ToastItem[]
  add: (msg: string, type?: ToastType, title?: string, ms?: number) => void
  remove: (id: number) => void
}

let _nextId = 1

export const useToastStore = create<ToastStore>((set) => ({
  items: [],
  add(msg, type = 'info', title, ms = 4000) {
    const id = _nextId++
    set(s => ({ items: [...s.items, { id, msg, type, title }] }))
    setTimeout(() => set(s => ({ items: s.items.filter(t => t.id !== id) })), ms)
  },
  remove(id) {
    set(s => ({ items: s.items.filter(t => t.id !== id) }))
  },
}))

export const showToast = (msg: string, type: ToastType = 'info', title?: string, ms?: number) =>
  useToastStore.getState().add(msg, type, title, ms)

export function useToast() {
  const { add } = useToastStore()
  return {
    toast: {
      success: (msg: string, title?: string) => add(msg, 'success', title),
      error:   (msg: string, title?: string) => add(msg, 'error',   title),
      warning: (msg: string, title?: string) => add(msg, 'warning', title),
      info:    (msg: string, title?: string) => add(msg, 'info',    title),
    }
  }
}

const ICON_COLOR: Record<ToastType, string> = {
  success: '#10b981',
  error:   '#ef4444',
  warning: '#f59e0b',
  info:    'var(--accent)',
}

const ICON_CMP: Record<ToastType, React.ElementType> = {
  success: CheckCircle,
  error:   XCircle,
  warning: AlertTriangle,
  info:    Info,
}

const LABELS: Record<ToastType, string> = {
  success: 'Done',
  error:   'Error',
  warning: 'Warning',
  info:    'Info',
}

function ToastItem({ item }: { item: ToastItem }) {
  const { remove } = useToastStore()
  const ref = useRef<HTMLDivElement>(null)
  const IconCmp = ICON_CMP[item.type]

  return (
    <div
      ref={ref}
      role="alert"
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        background: 'var(--surface)', border: '1px solid var(--surface-rule)',
        borderRadius: 10, padding: '12px 14px', boxShadow: '0 8px 24px rgba(0,0,0,.15)',
        fontSize: 13, minWidth: 240, maxWidth: 380, position: 'relative', overflow: 'hidden',
        animation: 'toast-in .2s ease-out',
      }}
    >
      <IconCmp size={16} strokeWidth={2.5} color={ICON_COLOR[item.type]} style={{ flexShrink: 0, marginTop: 1 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, color: 'var(--ink)', marginBottom: 2 }}>
          {item.title || LABELS[item.type]}
        </div>
        <div style={{ color: 'var(--ink-muted)', lineHeight: 1.4 }}>{item.msg}</div>
      </div>
      <button
        onClick={() => remove(item.id)}
        style={{ background: 'none', border: 'none', color: 'var(--ink-faint)', cursor: 'pointer',
                 fontSize: 16, lineHeight: 1, padding: 0, flexShrink: 0 }}
      >×</button>
      <style>{`@keyframes toast-in { from { opacity: 0; transform: translateX(40px) } to { opacity: 1; transform: translateX(0) } }`}</style>
    </div>
  )
}

export function ToastRegion() {
  const { items } = useToastStore()
  return (
    <div
      id="ta-toast-region"
      aria-live="polite"
      style={{
        position: 'fixed', bottom: 20, right: 20, zIndex: 9999,
        display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end',
        pointerEvents: 'none',
      }}
    >
      {items.map(item => (
        <div key={item.id} style={{ pointerEvents: 'auto' }}>
          <ToastItem item={item} />
        </div>
      ))}
    </div>
  )
}
