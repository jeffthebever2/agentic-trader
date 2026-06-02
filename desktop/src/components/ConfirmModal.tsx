import { AlertTriangle, X } from 'lucide-react'

interface ConfirmModalProps {
  title: string
  body: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmModal({ title, body, confirmLabel = 'Confirm', danger = true, onConfirm, onCancel }: ConfirmModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl bg-gray-900 ring-1 ring-gray-700 p-6 shadow-2xl">
        <div className="flex items-start gap-4">
          <div className={`mt-0.5 flex-shrink-0 rounded-full p-2 ${danger ? 'bg-red-500/15' : 'bg-yellow-500/15'}`}>
            <AlertTriangle className={`h-5 w-5 ${danger ? 'text-red-400' : 'text-yellow-400'}`} />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-semibold text-white">{title}</h2>
            <p className="mt-1 text-sm text-gray-400">{body}</p>
          </div>
          <button onClick={onCancel} className="text-gray-600 hover:text-gray-400">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg bg-gray-800 text-gray-300 text-sm hover:bg-gray-700 transition"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 rounded-lg text-white text-sm font-semibold transition ${
              danger ? 'bg-red-600 hover:bg-red-500' : 'bg-yellow-600 hover:bg-yellow-500'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
