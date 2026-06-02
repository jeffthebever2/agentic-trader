interface ServiceBadgeProps {
  label: string
  running: boolean
  detail?: string
}

export function ServiceBadge({ label, running, detail }: ServiceBadgeProps) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-gray-800 px-4 py-3">
      <div>
        <p className="text-sm font-medium text-white">{label}</p>
        {detail && <p className="text-xs text-gray-500 mt-0.5">{detail}</p>}
      </div>
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
          running ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${running ? 'bg-green-400' : 'bg-red-400'}`} />
        {running ? 'Running' : 'Stopped'}
      </span>
    </div>
  )
}
