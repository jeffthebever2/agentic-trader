import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchSystemServices, fetchPaperStatus,
  restartWebServer, startTunnel, stopTunnel,
  startPaperTrader, stopPaperTrader,
} from '../api/queries'
import { StatusBadge } from '../components/StatusIndicator'
import { ConfirmModal } from '../components/ConfirmModal'
import { SafeModeBar } from '../components/SafeModeBar'
import { Loading, ErrorState } from '../components/StateViews'
import { useAuthStore } from '../store/auth'
import { RefreshCw } from 'lucide-react'

interface Pending {
  title: string
  body: string
  danger?: boolean
  label: string
  action: () => Promise<unknown>
}

function Toast({ msg, type }: { msg: string; type: 'ok' | 'error' }) {
  return (
    <div className={`rounded-lg px-4 py-3 text-sm ${type === 'ok' ? 'bg-green-900/40 border border-green-700 text-green-300' : 'bg-red-900/40 border border-red-700 text-red-300'}`}>
      {msg}
    </div>
  )
}

export function Services() {
  const { safeMode } = useAuthStore()
  const qc = useQueryClient()
  const [pending, setPending] = useState<Pending | null>(null)
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'error' } | null>(null)

  function showToast(msg: string, type: 'ok' | 'error' = 'ok') {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 4000)
  }

  const services = useQuery({ queryKey: ['system-services'], queryFn: fetchSystemServices, refetchInterval: 10_000 })
  const paper = useQuery({ queryKey: ['paper-status'], queryFn: fetchPaperStatus, refetchInterval: 15_000 })

  const runAction = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => {
      showToast('Action completed', 'ok')
      qc.invalidateQueries({ queryKey: ['system-services'] })
      qc.invalidateQueries({ queryKey: ['paper-status'] })
    },
    onError: (e: Error) => showToast(`Error: ${e.message}`, 'error'),
    onSettled: () => setPending(null),
  })

  function confirm(p: Pending) {
    if (safeMode) { showToast('Safe mode active — write actions disabled', 'error'); return }
    setPending(p)
  }

  const s = services.data

  return (
    <div className="space-y-6">
      {pending && (
        <ConfirmModal
          title={pending.title}
          body={pending.body}
          confirmLabel={pending.label}
          danger={pending.danger ?? true}
          onConfirm={() => runAction.mutate(pending.action)}
          onCancel={() => setPending(null)}
        />
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Services</h1>
          <p className="text-sm text-gray-500 mt-1">Manage backend processes — all actions require confirmation</p>
        </div>
        <button
          onClick={() => { qc.invalidateQueries({ queryKey: ['system-services'] }); qc.invalidateQueries({ queryKey: ['paper-status'] }) }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${services.isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <SafeModeBar />
      {toast && <Toast msg={toast.msg} type={toast.type} />}

      {/* Web Server */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-white">Web Server (FastAPI)</h2>
            <p className="text-xs text-gray-500 mt-0.5">Main backend API — port 8001</p>
          </div>
          <StatusBadge status="ok" label="Running" />
        </div>
        <button
          onClick={() => confirm({
            title: 'Restart Web Server',
            body: 'The API will be unavailable for a few seconds during restart. Active WebSocket sessions will be dropped.',
            label: 'Restart Now',
            danger: true,
            action: () => restartWebServer(8001),
          })}
          disabled={runAction.isPending || safeMode}
          className="px-4 py-2 rounded-lg bg-red-700/80 hover:bg-red-600 disabled:opacity-40 text-white text-sm font-medium transition"
        >
          Restart Web Server
        </button>
      </div>

      {/* Cloudflare Tunnel */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-white">Cloudflare Tunnel</h2>
            <p className="text-xs text-gray-500 mt-0.5">Public HTTPS access</p>
          </div>
          {services.isLoading ? null : <StatusBadge status={s?.cloudflare_tunnel?.running ?? null} label={s?.cloudflare_tunnel?.running ? 'Online' : 'Offline'} />}
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => confirm({
              title: 'Start Cloudflare Tunnel',
              body: 'This will expose the server to the internet via Cloudflare.',
              label: 'Start Tunnel',
              danger: false,
              action: () => startTunnel(8001),
            })}
            disabled={runAction.isPending || safeMode || s?.cloudflare_tunnel?.running}
            className="px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 disabled:opacity-40 text-white text-sm font-medium transition"
          >
            Start
          </button>
          <button
            onClick={() => confirm({
              title: 'Stop Cloudflare Tunnel',
              body: 'The app will be unreachable from the internet until the tunnel is restarted.',
              label: 'Stop Tunnel',
              danger: true,
              action: () => stopTunnel(),
            })}
            disabled={runAction.isPending || safeMode || !s?.cloudflare_tunnel?.running}
            className="px-4 py-2 rounded-lg bg-red-700/80 hover:bg-red-600 disabled:opacity-40 text-white text-sm font-medium transition"
          >
            Stop
          </button>
        </div>
      </div>

      {/* Paper Trader */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-sm font-semibold text-white">Paper Trader</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {paper.data?.process?.pid ? `PID ${paper.data.process.pid}` : 'Not running'}
              {paper.data?.process?.started_at && ` · started ${new Date(paper.data.process.started_at).toLocaleTimeString()}`}
            </p>
          </div>
          <StatusBadge status={paper.data?.process?.running ?? null} label={paper.data?.process?.running ? 'Running' : 'Stopped'} />
        </div>
        {paper.isLoading && <Loading label="Loading paper status…" />}
        {paper.error && <ErrorState error={paper.error} />}
        <div className="flex gap-3 mt-4">
          <button
            onClick={() => confirm({
              title: 'Start Paper Trader',
              body: 'This will start the paper trading process. Ensure model and settings are configured.',
              label: 'Start',
              danger: false,
              action: () => startPaperTrader(),
            })}
            disabled={runAction.isPending || safeMode || paper.data?.process?.running}
            className="px-4 py-2 rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm font-medium transition"
          >
            Start
          </button>
          <button
            onClick={() => confirm({
              title: 'Stop Paper Trader',
              body: 'Open positions will remain but no new trades will be evaluated until restarted.',
              label: 'Stop Paper Trader',
              danger: true,
              action: () => stopPaperTrader(),
            })}
            disabled={runAction.isPending || safeMode || !paper.data?.process?.running}
            className="px-4 py-2 rounded-lg bg-red-700/80 hover:bg-red-600 disabled:opacity-40 text-white text-sm font-medium transition"
          >
            Stop
          </button>
        </div>
      </div>

      {/* Autofix — read-only status, no direct control exposed */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-white">Autofix Monitor</h2>
            <p className="text-xs text-gray-500 mt-0.5">Watches paper trader and auto-restarts it. Managed by screen session.</p>
          </div>
          <StatusBadge status={s?.autofix_monitor?.running ?? null} />
        </div>
        <p className="mt-3 text-xs text-gray-600 italic">
          Direct start/stop not exposed — autofix is managed by a screen session launched at deploy time.
          To restart: use Deployments → Restart Web Server (which re-launches the managed stack).
        </p>
      </div>
    </div>
  )
}
