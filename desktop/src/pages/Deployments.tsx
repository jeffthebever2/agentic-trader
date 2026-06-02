import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchDiagnostics, fetchRuntimeStatus, restartWebServer } from '../api/queries'
import { ConfirmModal } from '../components/ConfirmModal'
import { SafeModeBar } from '../components/SafeModeBar'
import { NotImplemented } from '../components/NotImplemented'
import { Loading, ErrorState } from '../components/StateViews'
import { useAuthStore } from '../store/auth'
import { GitBranch, RotateCcw, RefreshCw, Terminal } from 'lucide-react'

export function Deployments() {
  const { safeMode } = useAuthStore()
  const qc = useQueryClient()
  const [showRestartConfirm, setShowRestartConfirm] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const diag = useQuery({ queryKey: ['diagnostics'], queryFn: fetchDiagnostics, staleTime: 30_000 })
  const runtime = useQuery({ queryKey: ['runtime-status'], queryFn: fetchRuntimeStatus, staleTime: 30_000 })

  const restartMut = useMutation({
    mutationFn: () => restartWebServer(8001),
    onSuccess: (r) => { setToast(r.message); qc.invalidateQueries({ queryKey: ['system-services'] }) },
    onError: (e: Error) => setToast(`Error: ${e.message}`),
    onSettled: () => { setShowRestartConfirm(false); setTimeout(() => setToast(null), 5000) },
  })

  const d = diag.data
  const r = runtime.data

  return (
    <div className="space-y-6">
      {showRestartConfirm && (
        <ConfirmModal
          title="Restart Web Server"
          body="The API will be offline for a few seconds. All WebSocket sessions will be dropped. This manager app will also reconnect automatically."
          confirmLabel="Restart Now"
          danger
          onConfirm={() => restartMut.mutate()}
          onCancel={() => setShowRestartConfirm(false)}
        />
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Deployments</h1>
          <p className="text-sm text-gray-500 mt-1">Version info, git status, and safe restart actions</p>
        </div>
        <button
          onClick={() => { qc.invalidateQueries({ queryKey: ['diagnostics'] }); qc.invalidateQueries({ queryKey: ['runtime-status'] }) }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${diag.isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <SafeModeBar />

      {toast && (
        <div className="rounded-lg bg-indigo-900/40 border border-indigo-700 px-4 py-3 text-sm text-indigo-300">{toast}</div>
      )}

      {diag.isLoading ? <Loading label="Loading runtime diagnostics…" /> : diag.error ? (
        <ErrorState error={diag.error} retry={diag.refetch} />
      ) : (
        <>
          {/* Git status */}
          <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
            <div className="flex items-center gap-2 mb-4">
              <GitBranch className="h-4 w-4 text-indigo-400" />
              <h2 className="text-sm font-semibold text-gray-300">Git Status</h2>
              {d?.git.branch && (
                <span className="ml-auto text-xs bg-indigo-900/50 ring-1 ring-indigo-700 px-2 py-0.5 rounded-full text-indigo-300">
                  {d.git.branch}
                </span>
              )}
            </div>
            {d?.git.error ? (
              <p className="text-xs text-red-400">{d.git.error}</p>
            ) : d?.git.status_short.length ? (
              <pre className="text-xs font-mono text-yellow-300 whitespace-pre-wrap">
                {d.git.status_short.join('\n')}
              </pre>
            ) : (
              <p className="text-sm text-green-400">Working tree clean</p>
            )}
          </div>

          {/* Runtime info */}
          <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
            <div className="flex items-center gap-2 mb-4">
              <Terminal className="h-4 w-4 text-gray-400" />
              <h2 className="text-sm font-semibold text-gray-300">Runtime</h2>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {[
                ['Python', r?.python as string],
                ['Platform', r?.platform as string],
                ['Port', String(r?.port ?? '—')],
                ['Root', r?.root as string],
                ['Web PIDs', (r?.web_pids as string[])?.join(', ') || 'none'],
                ['Screen sessions', (r?.screen_sessions as string[])?.length ? (r?.screen_sessions as string[]).join(', ') : 'none'],
              ].map(([label, val]) => (
                <div key={label}>
                  <span className="text-xs text-gray-500">{label}</span>
                  <p className="text-gray-200 truncate text-xs mt-0.5" title={val}>{val || '—'}</p>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Safe actions */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center gap-2 mb-4">
          <RotateCcw className="h-4 w-4 text-yellow-400" />
          <h2 className="text-sm font-semibold text-gray-300">Safe Actions</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          All actions are audited. Destructive actions require confirmation. Safe mode disables all writes.
        </p>
        <button
          onClick={() => {
            if (safeMode) return
            setShowRestartConfirm(true)
          }}
          disabled={restartMut.isPending || safeMode}
          className="px-5 py-2 rounded-lg bg-red-700/80 hover:bg-red-600 disabled:opacity-40 text-white text-sm font-medium transition"
        >
          {restartMut.isPending ? 'Restarting…' : 'Restart Web Server'}
        </button>
      </div>

      {/* Deploy history — missing endpoint */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Deploy History</h2>
        <NotImplemented
          feature="Deploy history not available"
          reason="No backend endpoint records deployment events."
          endpoint={{
            method: 'GET',
            path: '/api/admin/deploy/history',
            response: '{ deploys: [{ ts, commit, branch, triggered_by, outcome }] }',
            notes: 'admin auth required; append on each deploy',
          }}
        />
      </div>

      {/* One-click deploy — missing, by design */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Remote Deploy</h2>
        <NotImplemented
          feature="Remote deploy action not implemented"
          reason="Deploying from the desktop app requires a safe backend endpoint that runs git pull + rebuild in a controlled way."
          endpoint={{
            method: 'POST',
            path: '/api/admin/deploy',
            body: '{ branch?: string, build?: boolean }',
            response: '{ ok, job_id, message }',
            notes: 'admin only; must audit log; dry-run flag recommended; arbitrary shell exec NOT acceptable',
          }}
        />
      </div>
    </div>
  )
}
