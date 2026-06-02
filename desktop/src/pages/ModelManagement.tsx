import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchMLStatus, fetchMLHistory, triggerRetrain } from '../api/queries'
import { StatusBadge } from '../components/StatusIndicator'
import { ConfirmModal } from '../components/ConfirmModal'
import { SafeModeBar } from '../components/SafeModeBar'
import { NotImplemented } from '../components/NotImplemented'
import { Loading, ErrorState, Empty } from '../components/StateViews'
import { useAuthStore } from '../store/auth'
import { Brain, RefreshCw, Zap, TrendingUp } from 'lucide-react'

function fmtDate(s: string | null | undefined) {
  if (!s) return '—'
  return new Date(s).toLocaleString()
}

export function ModelManagement() {
  const { safeMode } = useAuthStore()
  const qc = useQueryClient()
  const [showRetrainConfirm, setShowRetrainConfirm] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'error' } | null>(null)

  const ml = useQuery({ queryKey: ['ml-status'], queryFn: fetchMLStatus, staleTime: 60_000 })
  const history = useQuery({ queryKey: ['ml-history'], queryFn: fetchMLHistory, staleTime: 120_000 })

  const retrainMut = useMutation({
    mutationFn: () => triggerRetrain('all_tickers.txt'),
    onSuccess: (r) => {
      setToast({ msg: `${r.message}${r.pid ? ` (PID ${r.pid})` : ''}. Monitor in Logs → Retrain.`, type: 'ok' })
      qc.invalidateQueries({ queryKey: ['ml-status'] })
    },
    onError: (e: Error) => setToast({ msg: `Error: ${e.message}`, type: 'error' }),
    onSettled: () => { setShowRetrainConfirm(false); setTimeout(() => setToast(null), 8000) },
  })

  const m = ml.data

  return (
    <div className="space-y-6">
      {showRetrainConfirm && (
        <ConfirmModal
          title="Start ML Retrain"
          body="This will start a full model retrain using all_tickers.txt. It takes 3–5 hours and uses significant CPU. The existing deployed model remains active until the new one passes the gate (WF ROC ≥ 0.49)."
          confirmLabel="Start Retrain"
          danger={false}
          onConfirm={() => retrainMut.mutate()}
          onCancel={() => setShowRetrainConfirm(false)}
        />
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">AI / Model Management</h1>
          <p className="text-sm text-gray-500 mt-1">Live model status, retrain history, and operations</p>
        </div>
        <button
          onClick={() => { qc.invalidateQueries({ queryKey: ['ml-status'] }); qc.invalidateQueries({ queryKey: ['ml-history'] }) }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${ml.isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <SafeModeBar />

      {toast && (
        <div className={`rounded-lg px-4 py-3 text-sm ${toast.type === 'ok' ? 'bg-green-900/40 border border-green-700 text-green-300' : 'bg-red-900/40 border border-red-700 text-red-300'}`}>
          {toast.msg}
        </div>
      )}

      {/* Model status */}
      {ml.isLoading ? <Loading label="Loading model status…" /> : ml.error ? (
        <ErrorState error={ml.error} retry={ml.refetch} />
      ) : m ? (
        <>
          {/* Status card */}
          <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
            <div className="flex items-center gap-3 mb-4">
              <Brain className="h-5 w-5 text-indigo-400" />
              <h2 className="text-sm font-semibold text-white">Deployed Model</h2>
              <StatusBadge
                status={m.up_to_date ? 'ok' : m.bundle_exists ? 'warn' : 'error'}
                label={m.status_label}
              />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <div>
                <p className="text-xs text-gray-500">WF ROC (AUC)</p>
                <p className={`text-2xl font-bold mt-1 ${m.metrics?.win_probability?.roc_auc && m.metrics.win_probability.roc_auc >= 0.49 ? 'text-green-400' : 'text-red-400'}`}>
                  {m.metrics?.win_probability?.roc_auc?.toFixed(4) ?? '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Created</p>
                <p className="text-sm text-gray-200 mt-1">{fmtDate(m.created_at)}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Age</p>
                <p className={`text-sm mt-1 ${m.days_old !== null && m.days_old > 7 ? 'text-yellow-400' : 'text-gray-200'}`}>
                  {m.days_old !== null ? `${m.days_old}d` : '—'}
                </p>
              </div>
            </div>

            {/* Disclaimer */}
            <p className="mt-4 text-xs text-gray-600 italic border-t border-gray-800 pt-3">
              This is the live deployed model used for paper trading signals. Not a demo or backtest model.
              WF ROC ≥ 0.49 required for deployment gate.
            </p>
          </div>

          {/* Feature importance */}
          {m.feature_importance && m.feature_importance.length > 0 && (
            <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
              <div className="flex items-center gap-2 mb-3">
                <TrendingUp className="h-4 w-4 text-gray-400" />
                <h2 className="text-sm font-semibold text-gray-300">Top Feature Importance</h2>
              </div>
              <div className="space-y-2">
                {m.feature_importance.slice(0, 10).map((f, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <span className="text-xs text-gray-500 w-4">{i + 1}.</span>
                    <span className="text-xs text-gray-300 flex-1 truncate">{f.feature}</span>
                    <div className="w-24 h-1.5 bg-gray-800 rounded-full">
                      <div
                        className="h-1.5 bg-indigo-500 rounded-full"
                        style={{ width: `${Math.min((f.importance / (m.feature_importance![0]?.importance ?? 1)) * 100, 100)}%` }}
                      />
                    </div>
                    <span className="text-xs text-gray-500 w-12 text-right">{f.importance.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : null}

      {/* Retrain trigger */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Zap className="h-4 w-4 text-yellow-400" />
          <h2 className="text-sm font-semibold text-gray-300">Trigger Retrain</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Runs a full weekly retrain with <code className="text-indigo-400">all_tickers.txt</code> (~5869 tickers, 3–5 hours).
          The existing model stays deployed until the new one passes WF ROC ≥ 0.49.
          Monitor progress in <strong className="text-gray-300">Logs → Retrain</strong>.
        </p>
        <button
          onClick={() => { if (!safeMode) setShowRetrainConfirm(true) }}
          disabled={retrainMut.isPending || safeMode}
          className="px-5 py-2 rounded-lg bg-yellow-600 hover:bg-yellow-500 disabled:opacity-40 text-white text-sm font-semibold transition"
        >
          {retrainMut.isPending ? 'Starting…' : 'Trigger Retrain (all_tickers.txt)'}
        </button>
      </div>

      {/* Retrain history */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Retrain History</h2>
        {history.isLoading ? <Loading label="Loading history…" /> : history.error ? (
          <ErrorState error={history.error} />
        ) : !history.data?.length ? (
          <Empty label="No retrain history found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left pb-2 pr-4">Date</th>
                  <th className="text-left pb-2 pr-4">WF ROC</th>
                  <th className="text-left pb-2 pr-4">Rows</th>
                  <th className="text-left pb-2 pr-4">Outcome</th>
                  <th className="text-left pb-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-2 pr-4 text-gray-400">{r.retrain_date ?? '—'}</td>
                    <td className={`py-2 pr-4 font-mono ${r.win_roc_wf !== undefined && r.win_roc_wf >= 0.49 ? 'text-green-400' : 'text-red-400'}`}>
                      {r.win_roc_wf?.toFixed(4) ?? '—'}
                    </td>
                    <td className="py-2 pr-4 text-gray-300">{r.csv_rows ?? '—'}</td>
                    <td className="py-2 pr-4">
                      <span className={`px-1.5 py-0.5 rounded-full text-xs ${r.outcome === 'deployed' ? 'bg-green-500/15 text-green-400' : r.outcome === 'gate_failed' ? 'bg-red-500/15 text-red-400' : 'bg-gray-700 text-gray-400'}`}>
                        {r.outcome ?? 'unknown'}
                      </span>
                    </td>
                    <td className="py-2 text-gray-500 truncate max-w-32">{r.notes ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Inference errors — missing endpoint */}
      <div className="rounded-xl bg-gray-900 ring-1 ring-gray-700 p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Recent Inference Errors</h2>
        <NotImplemented
          feature="Inference error log not available"
          reason="No backend endpoint for ML prediction errors."
          endpoint={{
            method: 'GET',
            path: '/api/ml/errors/recent',
            response: '{ errors: [{ ts, ticker, error, model_version }] }',
            notes: 'admin auth; read-only; useful for model debugging',
          }}
        />
      </div>
    </div>
  )
}
