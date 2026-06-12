import { useState, useRef, useEffect } from 'react'
import api, { wsUrl } from '@/api/client'
import { EquityAreaChart } from '@/components/charts/EquityAreaChart'
import { DrawdownChart } from '@/components/charts/DrawdownChart'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScannerResult {
  symbol: string
  score: number
  entry: number
  target: number
  stop: number
  rr: number
  atr_pct: number
  rsi9: number
  vol_x: number
  ml_win_pct: number
  ml_exp_ret: number
  regime: string
  ml_pass: boolean
}

interface ScreenerResult {
  ticker: string
  score: number
  trend: number
  momentum: number
  rsi: number
  vol: number
  macd: number
  status: string
  entry: number
  target: number
  stop: number
}

interface AlgoStats {
  total_trades: number
  direction_accuracy: number
  avg_return: number
  profit_factor: number
  max_drawdown: number
  sortino: number
  account_final: number
  account_return: number
}

interface AlgoHoldRow { days: number; count: number; avg_ret: number }
interface AlgoTickerRow { ticker: string; trades: number; win_rate: number; avg_ret: number }
interface EquityPoint { x: string; y: number }

interface LlmDecision { ticker: string; date: string; decision: string }

interface HistoryItem { filename: string; created: string; tickers: string; size: string }

const PROVIDER_MODELS: Record<string, string[]> = {
  cloudflare: ['@cf/meta/llama-3.1-8b-instruct'],
  openrouter: ['meta-llama/llama-3.1-8b-instruct:free', 'google/gemma-2-9b-it:free'],
  openai: ['gpt-4o-mini', 'gpt-4o'],
  anthropic: ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6'],
  google: ['gemini-2.0-flash-exp', 'gemini-1.5-flash'],
  nvidia: ['nvidia/llama-3.1-nemotron-70b-instruct'],
  xai: ['grok-2-latest'],
  deepseek: ['deepseek-chat'],
  qwen: ['qwen/qwen-2.5-72b-instruct'],
  ollama: ['llama3.1:8b', 'mistral:7b'],
}

const TODAY = new Date().toISOString().split('T')[0]

// ── Shared style helpers ──────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 8,
  padding: 16,
  marginBottom: 16,
}

const formRow: React.CSSProperties = {
  display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12,
}

const fieldGroup: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4 }
const label: React.CSSProperties = { fontSize: 11, color: 'var(--ink-faint)', fontWeight: 500 }

const progressBarWrap: React.CSSProperties = {
  height: 6, background: 'var(--surface-raised)', borderRadius: 99, overflow: 'hidden', marginTop: 6,
}

const statCard: React.CSSProperties = {
  background: 'var(--surface-raised)',
  borderRadius: 8,
  padding: '12px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
}

const tbl: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 12 }
const th: React.CSSProperties = {
  padding: '6px 8px', textAlign: 'left', fontSize: 11, color: 'var(--ink-faint)',
  borderBottom: '1px solid var(--surface-rule)', fontWeight: 600, whiteSpace: 'nowrap',
}
const td: React.CSSProperties = {
  padding: '5px 8px', borderBottom: '1px solid var(--surface-rule)',
  color: 'var(--ink-muted)', whiteSpace: 'nowrap',
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'scanner',  label: 'Live Scanner' },
  { id: 'screen',   label: 'Technical Screener' },
  { id: 'algo',     label: 'Algorithm Backtest' },
  { id: 'llm',      label: 'LLM Backtest' },
  { id: 'results',  label: 'Past Results' },
]

// ── Sub-tab: Live Scanner ─────────────────────────────────────────────────────

function ScannerTab() {
  const [tickerFiles, setTickerFiles] = useState<string[]>(['all_tickers.txt'])
  const [tickerFile, setTickerFile] = useState('all_tickers.txt')
  const [scoreMode, setScoreMode] = useState('Breakout')
  const [threshold, setThreshold] = useState(65)
  const [maxTickers, setMaxTickers] = useState(0)
  const [targetMult, setTargetMult] = useState(2.0)
  const [stopMult, setStopMult] = useState(1.0)
  const [mlWinMin, setMlWinMin] = useState(0.50)
  const [useMlGate, setUseMlGate] = useState(true)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [progress, setProgress] = useState(0)
  const [stats, setStats] = useState({ scanned: 0, signals: 0, mlPass: 0, asOf: '' })
  const [results, setResults] = useState<ScannerResult[]>([])
  const [log, setLog] = useState('')
  const [showLog, setShowLog] = useState(false)
  const [mlOnly, setMlOnly] = useState(false)
  const ws = useRef<WebSocket | null>(null)
  const startTime = useRef<number>(0)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  function fetchFiles() {
    api.get('/scanner/ticker-files')
      .then(r => setTickerFiles(r.data?.files ?? ['all_tickers.txt']))
      .catch(() => {})
  }

  useEffect(() => { fetchFiles() }, [])

  function stop() {
    ws.current?.close()
    if (timer.current) clearInterval(timer.current)
    setRunning(false)
  }

  function scan() {
    setResults([])
    setLog('')
    setProgress(0)
    setStatus('Connecting…')
    setElapsed(0)
    setStats({ scanned: 0, signals: 0, mlPass: 0, asOf: '' })
    setRunning(true)
    startTime.current = Date.now()
    timer.current = setInterval(() => setElapsed(Math.round((Date.now() - startTime.current) / 1000)), 1000)

    ws.current = new WebSocket(wsUrl('/ws/scanner/scan'))
    ws.current.onopen = () => {
      ws.current!.send(JSON.stringify({
        tickers_file: tickerFile, score_mode: scoreMode, threshold,
        max_tickers: maxTickers, target_mult: targetMult, stop_mult: stopMult,
        ml_prob_min: mlWinMin, use_ml: useMlGate,
      }))
    }
    ws.current.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'progress') {
        setStatus(msg.status ?? '')
        setProgress(msg.pct ?? 0)
        setStats(s => ({
          ...s,
          scanned: msg.scanned ?? s.scanned,
          signals: msg.signals ?? s.signals,
          mlPass: msg.ml_pass ?? s.mlPass,
          asOf: msg.as_of ?? s.asOf,
        }))
        if (msg.log) setLog(prev => prev + msg.log + '\n')
      } else if (msg.type === 'result') {
        setResults(prev => [...prev, msg.data as ScannerResult])
      } else if (msg.type === 'done') {
        setStatus('Done')
        setProgress(100)
        stop()
      } else if (msg.type === 'error') {
        setStatus(`Error: ${msg.message ?? ''}`)
        stop()
      }
    }
    ws.current.onclose = () => { stop() }
  }

  const exportCsv = () => {
    const rows = [
      ['Symbol','Score','Entry','Target','Stop','R:R','ATR%','RSI9','Vol×','ML Win%','ML Exp Ret','Regime','ML Pass'],
      ...results.map(r => [r.symbol, r.score, r.entry, r.target, r.stop, r.rr, r.atr_pct, r.rsi9, r.vol_x, r.ml_win_pct, r.ml_exp_ret, r.regime, r.ml_pass ? 'yes' : 'no']),
    ]
    const csv = rows.map(r => r.join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = 'scanner_results.csv'
    a.click()
  }

  const displayed = mlOnly ? results.filter(r => r.ml_pass) : results

  return (
    <div id="bt-tab-scanner">
      <div style={card}>
        <div style={formRow}>
          <div style={fieldGroup}>
            <span style={label}>Tickers File</span>
            <div style={{ display: 'flex', gap: 4 }}>
              <select className="input" style={{ minWidth: 180 }} value={tickerFile} onChange={e => setTickerFile(e.target.value)}>
                {tickerFiles.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
              <button className="btn-secondary" onClick={fetchFiles}>Refresh</button>
            </div>
          </div>
          <div style={fieldGroup}>
            <span style={label}>Score Mode</span>
            <select className="input" value={scoreMode} onChange={e => setScoreMode(e.target.value)}>
              <option value="Breakout">Breakout</option>
              <option value="Confirmed Pullback">Confirmed Pullback</option>
            </select>
          </div>
          <div style={fieldGroup}>
            <span style={label}>Threshold</span>
            <input className="input" type="number" style={{ width: 80 }} value={threshold} onChange={e => setThreshold(+e.target.value)} />
          </div>
        </div>

        <details style={{ marginBottom: 12 }}>
          <summary style={{ fontSize: 12, color: 'var(--ink-faint)', cursor: 'pointer', userSelect: 'none' }}>Advanced</summary>
          <div style={{ ...formRow, marginTop: 10 }}>
            <div style={fieldGroup}>
              <span style={label}>Max Tickers</span>
              <input className="input" type="number" style={{ width: 80 }} value={maxTickers} onChange={e => setMaxTickers(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Target Mult × ATR</span>
              <input className="input" type="number" step="0.1" style={{ width: 80 }} value={targetMult} onChange={e => setTargetMult(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Stop Mult × ATR</span>
              <input className="input" type="number" step="0.1" style={{ width: 80 }} value={stopMult} onChange={e => setStopMult(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>ML Win Prob Min</span>
              <input className="input" type="number" step="0.01" style={{ width: 80 }} value={mlWinMin} onChange={e => setMlWinMin(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Use ML Gate</span>
              <input type="checkbox" checked={useMlGate} onChange={e => setUseMlGate(e.target.checked)} style={{ marginTop: 6 }} />
            </div>
          </div>
        </details>

        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-primary" onClick={scan} disabled={running}>Scan Now</button>
          {running && <button className="btn-secondary" onClick={stop}>Stop</button>}
        </div>
      </div>

      {(running || status) && (
        <div style={card}>
          <div style={{ fontSize: 12, color: 'var(--ink-muted)', marginBottom: 4 }}>{status}</div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 4 }}>Elapsed: {elapsed}s</div>
          <div style={progressBarWrap}>
            <div style={{ width: '100%', height: '100%', background: 'var(--accent)', transformOrigin: 'left center', transform: `scaleX(${progress / 100})`, transition: 'transform .3s var(--ease-out)' }} />
          </div>
          <div style={{ display: 'flex', gap: 20, marginTop: 10, flexWrap: 'wrap' }}>
            {[
              ['Scanned', stats.scanned],
              ['Signals Found', stats.signals],
              ['ML Pass', stats.mlPass],
              ['Score Mode', scoreMode],
              ['As Of Date', stats.asOf || '—'],
            ].map(([k, v]) => (
              <div key={String(k)}>
                <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>{k}</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--accent)' }}>{String(v)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div style={card}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
            <button className="btn-secondary" onClick={exportCsv} style={{ fontSize: 11 }}>Export CSV</button>
            <label style={{ fontSize: 12, color: 'var(--ink-muted)', display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={mlOnly} onChange={e => setMlOnly(e.target.checked)} />
              ML pass only
            </label>
            <span style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 'auto' }}>{displayed.length} signals</span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={tbl}>
              <thead>
                <tr>
                  {['Symbol','Score','Entry','Target','Stop','R:R','ATR%','RSI9','Vol×','ML Win%','ML Exp Ret','Regime','ML','Actions'].map(h => (
                    <th key={h} style={th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayed.map((r, i) => (
                  <tr key={i}>
                    <td style={{ ...td, fontWeight: 600, color: 'var(--ink)' }}>{r.symbol}</td>
                    <td style={td}>{r.score}</td>
                    <td style={td}>${r.entry?.toFixed(2)}</td>
                    <td style={td}>${r.target?.toFixed(2)}</td>
                    <td style={td}>${r.stop?.toFixed(2)}</td>
                    <td style={td}>{r.rr?.toFixed(2)}</td>
                    <td style={td}>{r.atr_pct?.toFixed(1)}%</td>
                    <td style={td}>{r.rsi9?.toFixed(1)}</td>
                    <td style={td}>{r.vol_x?.toFixed(1)}×</td>
                    <td style={td}>{(r.ml_win_pct * 100)?.toFixed(1)}%</td>
                    <td style={td}>{r.ml_exp_ret?.toFixed(3)}</td>
                    <td style={td}>{r.regime}</td>
                    <td style={td}>
                      <span style={{ color: r.ml_pass ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        {r.ml_pass ? '✓' : '✗'}
                      </span>
                    </td>
                    <td style={td}>
                      <button className="btn-secondary" style={{ fontSize: 10, padding: '2px 6px' }}
                        onClick={() => window.open(`https://finance.yahoo.com/quote/${r.symbol}`, '_blank')}>
                        Chart
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {log && (
        <div style={card}>
          <button className="btn-secondary" style={{ fontSize: 11, marginBottom: 8 }} onClick={() => setShowLog(v => !v)}>
            {showLog ? 'Hide' : 'Show'} Log
          </button>
          {showLog && (
            <pre style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--ink-faint)',
                          background: 'var(--canvas)', borderRadius: 6, padding: 10,
                          maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap', margin: 0 }}>
              {log}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── Sub-tab: Technical Screener ───────────────────────────────────────────────

function ScreenerTab() {
  const [tickers, setTickers] = useState('AAPL, MSFT, NVDA, TSLA, SPY')
  const [date, setDate] = useState(TODAY)
  const [mode, setMode] = useState('Standard')
  const [threshold, setThreshold] = useState(75)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<ScreenerResult[]>([])
  const [err, setErr] = useState('')

  const run = async () => {
    setLoading(true)
    setErr('')
    setResults([])
    try {
      const r = await api.post('/scanner/screen', {
        tickers: tickers.split(',').map(t => t.trim()).filter(Boolean),
        date, mode, threshold,
      })
      setResults(r.data?.results ?? r.data ?? [])
    } catch (e: unknown) {
      setErr((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div style={card}>
        <div style={formRow}>
          <div style={{ ...fieldGroup, flex: 1, minWidth: 220 }}>
            <span style={label}>Tickers (comma-separated)</span>
            <input className="input" value={tickers} onChange={e => setTickers(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Date</span>
            <input className="input" type="date" value={date} onChange={e => setDate(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Mode</span>
            <select className="input" value={mode} onChange={e => setMode(e.target.value)}>
              <option value="Standard">Standard</option>
              <option value="Swing">Swing</option>
            </select>
          </div>
          <div style={fieldGroup}>
            <span style={label}>Threshold</span>
            <input className="input" type="number" style={{ width: 80 }} value={threshold} onChange={e => setThreshold(+e.target.value)} />
          </div>
          <button className="btn-primary" onClick={run} disabled={loading} style={{ alignSelf: 'flex-end' }}>
            {loading ? 'Running…' : 'Run Screener'}
          </button>
        </div>
        {err && <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 4 }}>{err}</div>}
      </div>

      {results.length > 0 && (
        <div style={card}>
          <div style={{ overflowX: 'auto' }}>
            <table style={tbl}>
              <thead>
                <tr>
                  {['Ticker','Score','Trend/30','Mom/25','RSI/20','Vol/15','MACD/10','Status','Entry','Target','Stop'].map(h => (
                    <th key={h} style={th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i}>
                    <td style={{ ...td, fontWeight: 600, color: 'var(--ink)' }}>{r.ticker}</td>
                    <td style={td}>{r.score}</td>
                    <td style={td}>{r.trend}</td>
                    <td style={td}>{r.momentum}</td>
                    <td style={td}>{r.rsi}</td>
                    <td style={td}>{r.vol}</td>
                    <td style={td}>{r.macd}</td>
                    <td style={td}>
                      <span style={{ color: r.status === 'BUY' ? 'var(--green)' : r.status === 'SELL' ? 'var(--red)' : 'var(--ink-faint)', fontWeight: 600 }}>
                        {r.status}
                      </span>
                    </td>
                    <td style={td}>{r.entry ? `$${r.entry.toFixed(2)}` : '—'}</td>
                    <td style={td}>{r.target ? `$${r.target.toFixed(2)}` : '—'}</td>
                    <td style={td}>{r.stop ? `$${r.stop.toFixed(2)}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Sub-tab: Algorithm Backtest ───────────────────────────────────────────────

function AlgoTab() {
  const [tickers, setTickers] = useState('AAPL, MSFT, NVDA')
  const [startDate, setStartDate] = useState('2022-01-01')
  const [endDate, setEndDate] = useState(TODAY)
  const [scoreThreshold, setScoreThreshold] = useState(65)
  const [scoreMode, setScoreMode] = useState('Breakout')
  const [scanFreq, setScanFreq] = useState(1)
  const [holdPeriod, setHoldPeriod] = useState('2')
  const [accountSize, setAccountSize] = useState(10000)
  const [minPrice, setMinPrice] = useState(1)
  const [maxPrice, setMaxPrice] = useState(999999)
  const [mlWinMin, setMlWinMin] = useState(0.50)
  const [mlMaxLoss, setMlMaxLoss] = useState(0.35)
  const [mlMinExpRet, setMlMinExpRet] = useState(-0.01)
  const [skipCache, setSkipCache] = useState(false)
  const [skipMl, setSkipMl] = useState(false)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [progress, setProgress] = useState(0)
  const [log, setLog] = useState('')
  const [stats, setStats] = useState<AlgoStats | null>(null)
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const [holdRows, setHoldRows] = useState<AlgoHoldRow[]>([])
  const [tickerRows, setTickerRows] = useState<AlgoTickerRow[]>([])
  const ws = useRef<WebSocket | null>(null)
  const startTime = useRef<number>(0)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  function stop() {
    ws.current?.close()
    if (timer.current) clearInterval(timer.current)
    setRunning(false)
  }

  function run() {
    setLog('')
    setStats(null)
    setEquity([])
    setHoldRows([])
    setTickerRows([])
    setProgress(0)
    setStatus('Starting…')
    setElapsed(0)
    setRunning(true)
    startTime.current = Date.now()
    timer.current = setInterval(() => setElapsed(Math.round((Date.now() - startTime.current) / 1000)), 1000)

    ws.current = new WebSocket(wsUrl('/ws/algo-backtest'))
    ws.current.onopen = () => {
      ws.current!.send(JSON.stringify({
        tickers: tickers.split(',').map(t => t.trim()).filter(Boolean),
        start_date: startDate, end_date: endDate,
        threshold: scoreThreshold, score_mode: scoreMode,
        freq: scanFreq, hold_periods: [+holdPeriod], primary_hold: +holdPeriod,
        account_size: accountSize, min_price: minPrice, max_price: maxPrice,
        ml_probability_threshold: mlWinMin, ml_large_loss_max: mlMaxLoss,
        ml_expected_return_min: mlMinExpRet,
        no_cache: skipCache, no_ml: skipMl,
      }))
    }
    ws.current.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'log' || msg.type === 'progress') {
        if (msg.text) setLog(prev => prev + msg.text + '\n')
        if (msg.status) setStatus(msg.status)
        if (msg.pct !== undefined) setProgress(msg.pct)
      } else if (msg.type === 'results') {
        setStats(msg.stats ?? null)
        setEquity(msg.equity ?? [])
        setHoldRows(msg.hold_periods ?? [])
        setTickerRows(msg.per_ticker ?? [])
        setStatus('Done')
        setProgress(100)
        stop()
      } else if (msg.type === 'done') {
        setStatus('Done')
        setProgress(100)
        stop()
      } else if (msg.type === 'error') {
        setStatus(`Error: ${msg.message ?? ''}`)
        if (msg.text) setLog(prev => prev + msg.text + '\n')
        stop()
      }
    }
    ws.current.onclose = () => { stop() }
  }

  return (
    <div>
      <div style={{ ...card, background: 'var(--blue-faint, #e8f0fe)', borderColor: 'var(--blue, #4a90d9)' }}>
        <div style={{ fontSize: 12, color: 'var(--blue, #4a90d9)' }}>
          Algorithm Backtest uses pure technical analysis — no LLM calls required. Results are based on scoring signals and simulated entries/exits.
        </div>
      </div>

      <div style={card}>
        <div style={formRow}>
          <div style={{ ...fieldGroup, flex: 1, minWidth: 200 }}>
            <span style={label}>Tickers</span>
            <input className="input" value={tickers} onChange={e => setTickers(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Start Date</span>
            <input className="input" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>End Date</span>
            <input className="input" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Score Threshold</span>
            <input className="input" type="number" style={{ width: 80 }} value={scoreThreshold} onChange={e => setScoreThreshold(+e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Score Mode</span>
            <select className="input" value={scoreMode} onChange={e => setScoreMode(e.target.value)}>
              <option value="Breakout">Breakout</option>
              <option value="Confirmed Pullback">Confirmed Pullback</option>
              <option value="Mean Reversion">Mean Reversion</option>
            </select>
          </div>
        </div>

        <details style={{ marginBottom: 12 }}>
          <summary style={{ fontSize: 12, color: 'var(--ink-faint)', cursor: 'pointer', userSelect: 'none' }}>Advanced</summary>
          <div style={{ ...formRow, marginTop: 10 }}>
            <div style={fieldGroup}>
              <span style={label}>Scan Freq (days)</span>
              <input className="input" type="number" style={{ width: 80 }} value={scanFreq} onChange={e => setScanFreq(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Primary Hold Period</span>
              <select className="input" value={holdPeriod} onChange={e => setHoldPeriod(e.target.value)}>
                <option value="1">1 day</option>
                <option value="2">2 days</option>
                <option value="3">3 days</option>
                <option value="5">5 days</option>
              </select>
            </div>
            <div style={fieldGroup}>
              <span style={label}>Account Size ($)</span>
              <input className="input" type="number" style={{ width: 100 }} value={accountSize} onChange={e => setAccountSize(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Min Price</span>
              <input className="input" type="number" style={{ width: 80 }} value={minPrice} onChange={e => setMinPrice(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Max Price</span>
              <input className="input" type="number" style={{ width: 80 }} value={maxPrice} onChange={e => setMaxPrice(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>ML Win Prob Min</span>
              <input className="input" type="number" step="0.01" style={{ width: 80 }} value={mlWinMin} onChange={e => setMlWinMin(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>ML Max Loss Prob</span>
              <input className="input" type="number" step="0.01" style={{ width: 80 }} value={mlMaxLoss} onChange={e => setMlMaxLoss(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>ML Min Exp Return</span>
              <input className="input" type="number" step="0.001" style={{ width: 90 }} value={mlMinExpRet} onChange={e => setMlMinExpRet(+e.target.value)} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Skip Cache</span>
              <input type="checkbox" checked={skipCache} onChange={e => setSkipCache(e.target.checked)} style={{ marginTop: 6 }} />
            </div>
            <div style={fieldGroup}>
              <span style={label}>Skip ML</span>
              <input type="checkbox" checked={skipMl} onChange={e => setSkipMl(e.target.checked)} style={{ marginTop: 6 }} />
            </div>
          </div>
        </details>

        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-primary" onClick={run} disabled={running}>Run Backtest</button>
          {running && <button className="btn-secondary" onClick={stop}>Stop</button>}
        </div>
      </div>

      {(running || log) && (
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            {running && <div style={{ width: 14, height: 14, borderRadius: '50%', border: '2px solid var(--accent)', borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />}
            <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{status}</div>
            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 'auto' }}>Elapsed: {elapsed}s</div>
          </div>
          <div style={progressBarWrap}>
            <div style={{ width: '100%', height: '100%', background: 'var(--accent)', transformOrigin: 'left center', transform: `scaleX(${progress / 100})`, transition: 'transform .3s var(--ease-out)' }} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', textAlign: 'right', marginTop: 2 }}>{progress}%</div>
          {log && (
            <pre style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--ink-faint)',
                          background: 'var(--canvas)', borderRadius: 6, padding: 10,
                          maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap', margin: '10px 0 0' }}>
              {log}
            </pre>
          )}
        </div>
      )}

      {stats && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 10, marginBottom: 16 }}>
            {[
              { label: 'Total Trades', value: String(stats.total_trades ?? '—') },
              { label: 'Direction Accuracy', value: stats.direction_accuracy != null ? `${(stats.direction_accuracy * 100).toFixed(1)}%` : '—' },
              { label: 'Avg Return', value: stats.avg_return != null ? `${(stats.avg_return * 100).toFixed(2)}%` : '—' },
              { label: 'Profit Factor', value: stats.profit_factor != null ? stats.profit_factor.toFixed(2) : '—' },
              { label: 'Max Drawdown', value: stats.max_drawdown != null ? `${(stats.max_drawdown * 100).toFixed(1)}%` : '—' },
              { label: 'Sortino Ratio', value: stats.sortino != null ? stats.sortino.toFixed(2) : '—' },
              { label: 'Account Final', value: stats.account_final != null ? `$${stats.account_final.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—' },
              { label: 'Total Return', value: stats.account_return != null ? `${(stats.account_return * 100).toFixed(1)}%` : '—' },
            ].map(s => (
              <div key={s.label} style={statCard}>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{s.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--accent)' }}>{s.value}</div>
              </div>
            ))}
          </div>

          {equity.length > 0 && (
            <div style={{ ...card, paddingBottom: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 10 }}>Equity Curve</div>
              <EquityAreaChart
                series={[{ label: 'Account Value', data: equity, color: 'var(--accent)' }]}
                height={220}
                yFormatter={v => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
              />
            </div>
          )}

          {equity.length > 1 && (
            <div style={{ ...card, paddingBottom: 8 }}>
              <DrawdownChart
                equityData={equity}
                startingCash={stats.account_final / (1 + stats.account_return) || 10000}
                height={120}
              />
            </div>
          )}

          {holdRows.length > 0 && (
            <div style={card}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 10 }}>Hold Period Breakdown</div>
              <table style={tbl}>
                <thead>
                  <tr>
                    <th style={th}>Hold Days</th>
                    <th style={th}>Trades</th>
                    <th style={th}>Avg Return</th>
                  </tr>
                </thead>
                <tbody>
                  {holdRows.map((r, i) => (
                    <tr key={i}>
                      <td style={td}>{r.days}</td>
                      <td style={td}>{r.count}</td>
                      <td style={{ ...td, color: r.avg_ret >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {(r.avg_ret * 100).toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {tickerRows.length > 0 && (
            <div style={card}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 10 }}>Per-Ticker Results</div>
              <table style={tbl}>
                <thead>
                  <tr>
                    <th style={th}>Ticker</th>
                    <th style={th}>Trades</th>
                    <th style={th}>Win Rate</th>
                    <th style={th}>Avg Return</th>
                  </tr>
                </thead>
                <tbody>
                  {tickerRows.map((r, i) => (
                    <tr key={i}>
                      <td style={{ ...td, fontWeight: 600, color: 'var(--ink)' }}>{r.ticker}</td>
                      <td style={td}>{r.trades}</td>
                      <td style={td}>{(r.win_rate * 100).toFixed(1)}%</td>
                      <td style={{ ...td, color: r.avg_ret >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {(r.avg_ret * 100).toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Sub-tab: LLM Backtest ─────────────────────────────────────────────────────

function LlmTab() {
  const [tickers, setTickers] = useState('AAPL, MSFT')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState(TODAY)
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o-mini')
  const [freq, setFreq] = useState('weekly')
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [progress, setProgress] = useState(0)
  const [counts, setCounts] = useState({ total: 0, buy: 0, hold: 0, sell: 0 })
  const [decisions, setDecisions] = useState<LlmDecision[]>([])
  const ws = useRef<WebSocket | null>(null)
  const startTime = useRef<number>(0)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const models = PROVIDER_MODELS[provider] ?? []

  function changeProvider(p: string) {
    setProvider(p)
    setModel((PROVIDER_MODELS[p] ?? [])[0] ?? '')
  }

  function stop() {
    ws.current?.close()
    if (timer.current) clearInterval(timer.current)
    setRunning(false)
  }

  function run() {
    setDecisions([])
    setCounts({ total: 0, buy: 0, hold: 0, sell: 0 })
    setProgress(0)
    setStatus('Starting…')
    setElapsed(0)
    setRunning(true)
    startTime.current = Date.now()
    timer.current = setInterval(() => setElapsed(Math.round((Date.now() - startTime.current) / 1000)), 1000)

    ws.current = new WebSocket(wsUrl('/ws/backtest'))
    ws.current.onopen = () => {
      ws.current!.send(JSON.stringify({
        tickers: tickers.split(',').map(t => t.trim()).filter(Boolean),
        start_date: startDate, end_date: endDate,
        provider, model, freq,
      }))
    }
    ws.current.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'progress') {
        setStatus(msg.status ?? '')
        setProgress(msg.pct ?? 0)
      } else if (msg.type === 'decision') {
        const d: LlmDecision = { ticker: msg.ticker, date: msg.date, decision: msg.decision }
        setDecisions(prev => [...prev, d])
        setCounts(c => ({
          total: c.total + 1,
          buy: c.buy + (msg.decision === 'BUY' ? 1 : 0),
          hold: c.hold + (msg.decision === 'HOLD' ? 1 : 0),
          sell: c.sell + (msg.decision === 'SELL' ? 1 : 0),
        }))
      } else if (msg.type === 'done') {
        setStatus('Done')
        setProgress(100)
        stop()
      } else if (msg.type === 'error') {
        setStatus(`Error: ${msg.message ?? ''}`)
        stop()
      }
    }
    ws.current.onclose = () => { stop() }
  }

  return (
    <div>
      <div style={{ ...card, background: 'var(--yellow-faint, #fff8e1)', borderColor: 'var(--yellow, #f0c040)' }}>
        <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
          Warning: LLM Backtest makes real API calls to language model providers and may incur costs. Each date/ticker combination is one LLM call. Use short date ranges and few tickers to control spend.
        </div>
      </div>

      <div style={card}>
        <div style={formRow}>
          <div style={{ ...fieldGroup, flex: 1, minWidth: 200 }}>
            <span style={label}>Tickers</span>
            <input className="input" value={tickers} onChange={e => setTickers(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>Start Date</span>
            <input className="input" type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>
          <div style={fieldGroup}>
            <span style={label}>End Date</span>
            <input className="input" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
        </div>
        <div style={formRow}>
          <div style={fieldGroup}>
            <span style={label}>LLM Provider</span>
            <select className="input" value={provider} onChange={e => changeProvider(e.target.value)}>
              {Object.keys(PROVIDER_MODELS).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div style={{ ...fieldGroup, minWidth: 240 }}>
            <span style={label}>Model</span>
            <select className="input" value={model} onChange={e => setModel(e.target.value)}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div style={fieldGroup}>
            <span style={label}>Frequency</span>
            <select className="input" value={freq} onChange={e => setFreq(e.target.value)}>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="daily">Daily</option>
            </select>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-primary" onClick={run} disabled={running}>Start Backtest</button>
          {running && <button className="btn-secondary" onClick={stop}>Stop</button>}
        </div>
      </div>

      {(running || status) && (
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            {running && <div style={{ width: 14, height: 14, borderRadius: '50%', border: '2px solid var(--accent)', borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />}
            <div style={{ fontSize: 12, color: 'var(--ink-muted)' }}>{status}</div>
            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginLeft: 'auto' }}>Elapsed: {elapsed}s</div>
          </div>
          <div style={progressBarWrap}>
            <div style={{ width: '100%', height: '100%', background: 'var(--accent)', transformOrigin: 'left center', transform: `scaleX(${progress / 100})`, transition: 'transform .3s var(--ease-out)' }} />
          </div>
        </div>
      )}

      {counts.total > 0 && (
        <div style={card}>
          <div style={{ display: 'flex', gap: 16, marginBottom: 14, flexWrap: 'wrap' }}>
            {[
              { label: 'Total', value: counts.total, color: 'var(--accent)' },
              { label: 'BUY', value: counts.buy, color: 'var(--green)' },
              { label: 'HOLD', value: counts.hold, color: 'var(--ink-faint)' },
              { label: 'SELL', value: counts.sell, color: 'var(--red)' },
            ].map(s => (
              <div key={s.label} style={statCard}>
                <div style={{ fontSize: 11, color: 'var(--ink-faint)' }}>{s.label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>{s.value}</div>
              </div>
            ))}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={tbl}>
              <thead>
                <tr>
                  <th style={th}>Ticker</th>
                  <th style={th}>Date</th>
                  <th style={th}>Decision</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i}>
                    <td style={{ ...td, fontWeight: 600, color: 'var(--ink)' }}>{d.ticker}</td>
                    <td style={td}>{d.date}</td>
                    <td style={{ ...td, fontWeight: 600, color: d.decision === 'BUY' ? 'var(--green)' : d.decision === 'SELL' ? 'var(--red)' : 'var(--ink-faint)' }}>
                      {d.decision}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Sub-tab: Past Results ─────────────────────────────────────────────────────

function ResultsTab() {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/backtest/results')
      .then(r => setItems(r.data?.results ?? r.data ?? []))
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 13 }}>Loading…</div>
  if (err) return <div style={{ padding: 20, color: 'var(--red)', fontSize: 13 }}>{err}</div>
  if (items.length === 0) return <div style={{ padding: 20, color: 'var(--ink-faint)', fontSize: 13 }}>No past backtest results found.</div>

  return (
    <div style={card}>
      <table style={tbl}>
        <thead>
          <tr>
            <th style={th}>File</th>
            <th style={th}>Created</th>
            <th style={th}>Tickers</th>
            <th style={th}>Size</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily: 'var(--font-mono)', fontSize: 11 }}>{item.filename}</td>
              <td style={td}>{item.created}</td>
              <td style={td}>{item.tickers}</td>
              <td style={td}>{item.size}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Root ──────────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  const [activeTab, setActiveTab] = useState('scanner')

  return (
    <div id="panel-backtest" style={{ padding: 24, maxWidth: 1100 }}>
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)', marginBottom: 20 }}>Backtest</div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--surface-rule)', marginBottom: 20 }}>
        {TABS.map(t => (
          <button
            key={t.id}
            id={`bt-tab-${t.id}`}
            onClick={() => setActiveTab(t.id)}
            style={{
              background: 'none',
              border: 'none',
              borderBottom: activeTab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
              padding: '8px 16px',
              fontSize: 13,
              fontWeight: activeTab === t.id ? 600 : 400,
              color: activeTab === t.id ? 'var(--accent)' : 'var(--ink-faint)',
              cursor: 'pointer',
              marginBottom: -1,
              transition: 'color .15s',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === 'scanner' && <ScannerTab />}
      {activeTab === 'screen'  && <ScreenerTab />}
      {activeTab === 'algo'    && <AlgoTab />}
      {activeTab === 'llm'     && <LlmTab />}
      {activeTab === 'results' && <ResultsTab />}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
