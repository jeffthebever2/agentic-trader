import { Construction } from 'lucide-react'

interface EndpointDoc {
  method: string
  path: string
  body?: string
  response?: string
  notes?: string
}

interface NotImplementedProps {
  feature: string
  reason?: string
  endpoint?: EndpointDoc
}

export function NotImplemented({ feature, reason, endpoint }: NotImplementedProps) {
  return (
    <div className="rounded-xl bg-gray-900/60 ring-1 ring-dashed ring-gray-700 p-5">
      <div className="flex items-start gap-3">
        <Construction className="h-5 w-5 text-yellow-500 mt-0.5 shrink-0" />
        <div className="space-y-2 flex-1">
          <p className="text-sm font-medium text-yellow-400">{feature}</p>
          {reason && <p className="text-xs text-gray-500">{reason}</p>}
          {endpoint && (
            <div className="mt-3 rounded-lg bg-gray-800 p-3 space-y-1 font-mono text-xs text-gray-400">
              <p><span className="text-indigo-400">{endpoint.method}</span> {endpoint.path}</p>
              {endpoint.body && <p><span className="text-gray-500">body:</span> {endpoint.body}</p>}
              {endpoint.response && <p><span className="text-gray-500">→</span> {endpoint.response}</p>}
              {endpoint.notes && <p className="text-yellow-600 not-italic"># {endpoint.notes}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
