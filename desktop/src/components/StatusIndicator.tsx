type StatusLevel = 'ok' | 'warn' | 'error' | 'unknown' | 'running' | 'stopped'

interface StatusIndicatorProps {
  status: StatusLevel | boolean | null | undefined
  label?: string
  size?: 'sm' | 'md'
}

const STATUS_STYLE: Record<StatusLevel, { dot: string; badge: string; label: string }> = {
  ok:      { dot: 'bg-green-400', badge: 'bg-green-500/15 text-green-400', label: 'OK' },
  running: { dot: 'bg-green-400 animate-pulse', badge: 'bg-green-500/15 text-green-400', label: 'Running' },
  warn:    { dot: 'bg-yellow-400', badge: 'bg-yellow-500/15 text-yellow-400', label: 'Warning' },
  error:   { dot: 'bg-red-400', badge: 'bg-red-500/15 text-red-400', label: 'Error' },
  stopped: { dot: 'bg-gray-500', badge: 'bg-gray-700 text-gray-400', label: 'Stopped' },
  unknown: { dot: 'bg-gray-600', badge: 'bg-gray-800 text-gray-500', label: 'Unknown' },
}

function resolveStatus(s: StatusLevel | boolean | null | undefined): StatusLevel {
  if (s === null || s === undefined) return 'unknown'
  if (typeof s === 'boolean') return s ? 'ok' : 'error'
  return s
}

export function StatusDot({ status, size = 'sm' }: StatusIndicatorProps) {
  const s = resolveStatus(status)
  const sz = size === 'sm' ? 'h-2 w-2' : 'h-2.5 w-2.5'
  return <span className={`inline-block rounded-full ${sz} ${STATUS_STYLE[s].dot}`} />
}

export function StatusBadge({ status, label, size = 'sm' }: StatusIndicatorProps) {
  const s = resolveStatus(status)
  const displayLabel = label ?? STATUS_STYLE[s].label
  const sz = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${sz} ${STATUS_STYLE[s].badge}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_STYLE[s].dot.replace(' animate-pulse', '')}`} />
      {displayLabel}
    </span>
  )
}
