import { AlertCircle, Loader2, ServerOff } from 'lucide-react'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-gray-400">
      <Loader2 className="h-8 w-8 animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-red-400">
      <AlertCircle className="h-8 w-8" />
      <span className="text-sm max-w-sm text-center">{msg}</span>
      {retry && (
        <button
          onClick={retry}
          className="mt-2 px-4 py-1.5 rounded-md bg-gray-800 text-gray-300 text-sm hover:bg-gray-700 transition"
        >
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ label = 'No data' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-gray-500">
      <ServerOff className="h-8 w-8" />
      <span className="text-sm">{label}</span>
    </div>
  )
}
