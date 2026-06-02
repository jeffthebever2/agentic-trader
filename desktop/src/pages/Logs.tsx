import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchLogsSystem, fetchLogsSources, LOG_SOURCES, LOG_SOURCE_LABELS } from '../api/queries'
import type { LogSource } from '../api/queries'
import { Loading, ErrorState, Empty } from '../components/StateViews'
import { RefreshCw, Search, X } from 'lucide-react'

const LOG_LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type LogLevel = typeof LOG_LEVELS[number]

function levelColor(line: string): string {
  if (line.includes('ERROR') || line.includes('CRITICAL')) return 'text-red-400'
  if (line.includes('WARNING') || line.includes('WARN')) return 'text-yellow-400'
  if (line.includes('DEBUG')) return 'text-gray-500'
  return 'text-gray-300'
}

export function Logs() {
  const [source, setSource] = useState<LogSource>('web')
  const [lines, setLines] = useState(200)
  const [search, setSearch] = useState('')
  const [levelFilter, setLevelFilter] = useState<LogLevel>('')

  const sources = useQuery({ queryKey: ['logs-sources'], queryFn: fetchLogsSources })
  const logs = useQuery({
    queryKey: ['logs-system', source, lines],
    queryFn: () => fetchLogsSystem(source, lines),
    refetchInterval: 30_000,
  })

  const filtered = useMemo(() => {
    const entries = logs.data?.entries ?? []
    return entries.filter(line => {
      if (levelFilter && !line.toUpperCase().includes(levelFilter)) return false
      if (search && !line.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [logs.data?.entries, search, levelFilter])

  const sourceExists = sources.data?.find(s => s.name === source)?.exists

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Logs</h1>
          <p className="text-sm text-gray-500 mt-1">Private server logs — auto-refreshes every 30s</p>
        </div>
        <button
          onClick={() => logs.refetch()}
          disabled={logs.isFetching}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm transition"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${logs.isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap gap-2">
        <select
          value={source}
          onChange={e => setSource(e.target.value as LogSource)}
          className="rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          {LOG_SOURCES.map(s => (
            <option key={s} value={s}>
              {LOG_SOURCE_LABELS[s]}
              {sources.data?.find(x => x.name === s)?.exists === false ? ' ✗' : ''}
            </option>
          ))}
        </select>

        <select
          value={levelFilter}
          onChange={e => setLevelFilter(e.target.value as LogLevel)}
          className="rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          <option value="">All levels</option>
          {LOG_LEVELS.filter(Boolean).map(l => <option key={l} value={l}>{l}</option>)}
        </select>

        <select
          value={lines}
          onChange={e => setLines(Number(e.target.value))}
          className="rounded-lg bg-gray-800 border border-gray-700 text-white text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        >
          {[100, 200, 500, 1000].map(n => <option key={n} value={n}>{n} lines</option>)}
        </select>

        {/* Search */}
        <div className="relative flex-1 min-w-40">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search logs…"
            className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white text-sm pl-9 pr-8 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 placeholder:text-gray-600"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Stats bar */}
      {logs.data && (
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span>{filtered.length} / {logs.data.entries.length} lines</span>
          {search && <span>matching "{search}"</span>}
          {levelFilter && <span>level: {levelFilter}</span>}
          {sourceExists === false && (
            <span className="text-yellow-500">⚠ Log file not found on server</span>
          )}
        </div>
      )}

      {/* Log output */}
      <div className="flex-1 overflow-hidden rounded-xl bg-gray-950 border border-gray-800">
        {logs.isLoading ? (
          <Loading label="Fetching logs…" />
        ) : logs.error ? (
          <ErrorState error={logs.error} retry={logs.refetch} />
        ) : !filtered.length ? (
          <Empty label={search || levelFilter ? `No entries matching filter` : `No log entries for ${LOG_SOURCE_LABELS[source]}`} />
        ) : (
          <div className="h-full overflow-y-auto p-4 font-mono text-xs space-y-0.5">
            {filtered.map((line, i) => (
              <div key={i} className={`leading-relaxed hover:bg-gray-800/50 px-1 rounded ${levelColor(line)}`}>
                {line}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
