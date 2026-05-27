import { useState, useRef, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { wsUrl } from '@/api/client'

// ── Provider model map ────────────────────────────────────────────────────────

const PROVIDER_MODELS: Record<string, string[]> = {
  cloudflare: ['@cf/meta/llama-3.1-8b-instruct', '@cf/meta/llama-3.3-70b-instruct-fp8-fast'],
  openrouter: ['meta-llama/llama-3.1-8b-instruct:free', 'google/gemma-2-9b-it:free', 'mistralai/mistral-7b-instruct:free'],
  openai: ['gpt-4o-mini', 'gpt-4o', 'o1-mini'],
  anthropic: ['claude-haiku-4-5-20251001', 'claude-sonnet-4-6', 'claude-opus-4-7'],
  google: ['gemini-2.0-flash-exp', 'gemini-1.5-flash', 'gemini-1.5-pro'],
  nvidia: ['nvidia/llama-3.1-nemotron-70b-instruct'],
  xai: ['grok-2-latest', 'grok-3'],
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
  qwen: ['qwen/qwen-2.5-72b-instruct'],
  glm: ['glm-4-flash', 'glm-4-plus'],
  azure: ['gpt-4o', 'gpt-4o-mini'],
  ollama: ['llama3.1:8b', 'mistral:7b', 'qwen2.5:7b'],
}

// ── Types ─────────────────────────────────────────────────────────────────────

type Mode = 'agent' | 'algorithm' | 'machine_learning' | 'algorithm_ml'
type AgentStatus = 'waiting' | 'running' | 'done' | 'error'

interface AgentPill {
  name: string
  status: AgentStatus
}

interface ReportEntry {
  agent: string
  content: string
}

interface LiveMessage {
  id: number
  agent: string
  text: string
}

const TODAY = new Date().toISOString().split('T')[0]

// ── Spinner SVG ───────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" style={{ animation: 'spin 0.8s linear infinite', display: 'inline-block' }}>
      <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeDasharray="14 6" />
    </svg>
  )
}

// ── Play icon SVG ─────────────────────────────────────────────────────────────

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" style={{ marginRight: 6 }}>
      <polygon points="3,1 13,7 3,13" />
    </svg>
  )
}

// ── TradingView Modal ─────────────────────────────────────────────────────────

function TvModal({ onClose }: { onClose: () => void }) {
  const [symbol, setSymbol] = useState('AAPL')
  const [input, setInput] = useState('AAPL')
  const [interval, setInterval] = useState('D')
  const [style, setStyle] = useState('1')

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
    zIndex: 50, display: 'flex', flexDirection: 'column',
  }
  const header: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '10px 16px', background: 'var(--surface)',
    borderBottom: '1px solid var(--surface-rule)',
  }
  const body: React.CSSProperties = { flex: 1, overflow: 'hidden' }

  return (
    <div style={overlay} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={header}>
        <input
          className="input"
          style={{ width: 120, height: 32, textTransform: 'uppercase' }}
          value={input}
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') setSymbol(input) }}
          placeholder="Symbol"
        />
        <button className="btn-primary" style={{ height: 32, padding: '0 12px' }} onClick={() => setSymbol(input)}>
          Load
        </button>
        <select
          className="input"
          style={{ height: 32 }}
          value={interval}
          onChange={e => setInterval(e.target.value)}
        >
          <option value="1">1m</option>
          <option value="5">5m</option>
          <option value="15">15m</option>
          <option value="60">1h</option>
          <option value="D">1D</option>
          <option value="W">1W</option>
        </select>
        <select
          className="input"
          style={{ height: 32 }}
          value={style}
          onChange={e => setStyle(e.target.value)}
        >
          <option value="1">Candles</option>
          <option value="2">Bars</option>
          <option value="3">Line</option>
          <option value="8">Heiken Ashi</option>
        </select>
        <div style={{ flex: 1 }} />
        <button className="btn-secondary" style={{ height: 32, padding: '0 12px' }} onClick={onClose}>
          Close
        </button>
      </div>
      <div style={body}>
        <iframe
          key={`${symbol}-${interval}-${style}`}
          src={`https://www.tradingview.com/widgetembed/?symbol=${symbol}&interval=${interval}&theme=dark&style=${style}&locale=en`}
          style={{ width: '100%', height: '100%', border: 'none' }}
          allowFullScreen
        />
      </div>
    </div>
  )
}

// ── Full Report Modal ─────────────────────────────────────────────────────────

function ReportModal({ reports, onClose }: { reports: ReportEntry[]; onClose: () => void }) {
  const [tab, setTab] = useState(0)

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
    zIndex: 50, display: 'flex', flexDirection: 'column',
  }
  const container: React.CSSProperties = {
    margin: 'auto', width: '90vw', maxWidth: 900, maxHeight: '90vh',
    background: 'var(--bg)', border: '1px solid var(--surface-rule)',
    borderRadius: 10, display: 'flex', flexDirection: 'column', overflow: 'hidden',
  }
  const header: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)',
  }
  const tabBar: React.CSSProperties = {
    display: 'flex', overflowX: 'auto', borderBottom: '1px solid var(--surface-rule)',
    background: 'var(--surface)',
  }
  const body: React.CSSProperties = {
    flex: 1, overflow: 'auto', padding: 20,
    fontFamily: 'var(--font-mono)', fontSize: 13, whiteSpace: 'pre-wrap', color: 'var(--ink)',
  }

  return (
    <div style={overlay} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={container}>
        <div style={header}>
          <span style={{ fontWeight: 700, fontSize: 16 }}>Full Analysis Report</span>
          <button className="btn-secondary" style={{ height: 30, padding: '0 12px' }} onClick={onClose}>Close</button>
        </div>
        {reports.length > 0 && (
          <div style={tabBar}>
            {reports.map((r, i) => (
              <button
                key={i}
                onClick={() => setTab(i)}
                style={{
                  padding: '8px 16px', border: 'none', background: 'none', cursor: 'pointer',
                  color: tab === i ? 'var(--accent)' : 'var(--ink-muted)',
                  borderBottom: tab === i ? '2px solid var(--accent)' : '2px solid transparent',
                  fontWeight: tab === i ? 700 : 400, fontSize: 13, whiteSpace: 'nowrap',
                }}
              >
                {r.agent}
              </button>
            ))}
          </div>
        )}
        <div style={body}>
          {reports.length === 0
            ? 'No reports available.'
            : (reports[tab]?.content ?? '')}
        </div>
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function AnalyzePage() {
  const [searchParams] = useSearchParams()

  // Config state
  const [mode, setMode] = useState<Mode>('agent')
  const [ticker, setTicker] = useState(() => searchParams.get('ticker') ?? '')
  const [date, setDate] = useState(() => searchParams.get('date') ?? TODAY)
  const [analysts, setAnalysts] = useState({ market: true, social: true, news: true, fundamentals: true })
  const [provider, setProvider] = useState('openai')
  const [deepModel, setDeepModel] = useState('gpt-4o')
  const [quickModel, setQuickModel] = useState('gpt-4o-mini')
  const [depth, setDepth] = useState('3')
  const [language, setLanguage] = useState('English')
  const [algoConfig, setAlgoConfig] = useState('try_all')
  const [threshold, setThreshold] = useState('65')
  const [mlProb, setMlProb] = useState('0.50')
  const [useMl, setUseMl] = useState(true)

  // Runtime state
  const [running, setRunning] = useState(false)
  const [statusText, setStatusText] = useState('Ready — enter a ticker and click Run Analysis.')
  const [agentProgress, setAgentProgress] = useState('')
  const [agents, setAgents] = useState<AgentPill[]>([])
  const [messages, setMessages] = useState<LiveMessage[]>([])
  const [reports, setReports] = useState<ReportEntry[]>([])
  const [decision, setDecision] = useState<{ decision: string; text: string } | null>(null)
  const [timerSecs, setTimerSecs] = useState(0)
  const [showTv, setShowTv] = useState(false)
  const [showReport, setShowReport] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const msgIdRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Update model defaults when provider changes
  useEffect(() => {
    const models = PROVIDER_MODELS[provider] ?? []
    setDeepModel(models[1] ?? models[0] ?? '')
    setQuickModel(models[0] ?? '')
  }, [provider])

  // Auto-scroll live feed
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const startTimer = useCallback(() => {
    setTimerSecs(0)
    timerRef.current = setInterval(() => setTimerSecs(s => s + 1), 1000)
  }, [])

  const handleStop = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    setRunning(false)
    stopTimer()
    setStatusText('Stopped.')
  }, [stopTimer])

  const handleRun = useCallback(() => {
    if (!ticker.trim()) return

    // Reset
    setMessages([])
    setAgents([])
    setReports([])
    setDecision(null)
    setAgentProgress('')
    setStatusText('Connecting…')
    setRunning(true)
    startTimer()

    const ws = new WebSocket(wsUrl('/analyze'))
    wsRef.current = ws

    ws.onopen = () => {
      const selectedAnalysts = Object.entries(analysts)
        .filter(([, v]) => v)
        .map(([k]) => k)
      ws.send(JSON.stringify({
        ticker: ticker.trim().toUpperCase(),
        mode,
        date,
        analysts: selectedAnalysts,
        provider,
        deep_model: deepModel,
        quick_model: quickModel,
        depth: Number(depth),
        language,
        algo_config: algoConfig,
        threshold: Number(threshold),
        ml_prob: Number(mlProb),
        use_ml: useMl,
      }))
      setStatusText('Running analysis…')
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string)
        if (msg.type === 'agent_start') {
          setAgents(prev => {
            const exists = prev.find(a => a.name === msg.agent)
            if (exists) return prev.map(a => a.name === msg.agent ? { ...a, status: 'running' } : a)
            return [...prev, { name: msg.agent, status: 'running' }]
          })
        } else if (msg.type === 'agent_done') {
          setAgents(prev => prev.map(a => a.name === msg.agent ? { ...a, status: 'done' } : a))
        } else if (msg.type === 'message') {
          const id = ++msgIdRef.current
          setMessages(prev => [...prev, { id, agent: msg.agent ?? '', text: msg.text ?? '' }])
        } else if (msg.type === 'report') {
          setReports(prev => {
            const exists = prev.find(r => r.agent === msg.agent)
            if (exists) return prev.map(r => r.agent === msg.agent ? { ...r, content: msg.content } : r)
            return [...prev, { agent: msg.agent, content: msg.content }]
          })
        } else if (msg.type === 'decision') {
          setDecision({ decision: msg.decision, text: msg.text ?? '' })
        } else if (msg.type === 'done') {
          setRunning(false)
          stopTimer()
          setStatusText('Analysis complete.')
        } else if (msg.type === 'error') {
          setStatusText(`Error: ${msg.text ?? 'Unknown error'}`)
          setRunning(false)
          stopTimer()
          setAgents(prev => prev.map(a => a.status === 'running' ? { ...a, status: 'error' } : a))
        } else if (msg.type === 'status') {
          setStatusText(msg.text ?? '')
        } else if (msg.type === 'progress') {
          setAgentProgress(`${msg.agent} ${msg.done}/${msg.total}`)
        }
      } catch (_) { /* ignore parse errors */ }
    }

    ws.onerror = () => {
      setStatusText('WebSocket error.')
      setRunning(false)
      stopTimer()
    }

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null
    }
  }, [ticker, mode, date, analysts, provider, deepModel, quickModel, depth, language, algoConfig, threshold, mlProb, useMl, startTimer, stopTimer])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close()
      stopTimer()
    }
  }, [stopTimer])

  // ── Styles ──────────────────────────────────────────────────────────────────

  const container: React.CSSProperties = {
    display: 'flex', height: '100%', overflow: 'hidden',
  }

  const leftPanel: React.CSSProperties = {
    width: 252, flexShrink: 0, overflowY: 'auto',
    background: 'var(--surface)', borderRight: '1px solid var(--surface-rule)',
    padding: '12px 12px 20px',
    display: 'flex', flexDirection: 'column', gap: 10,
  }

  const rightPanel: React.CSSProperties = {
    flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
  }

  const label: React.CSSProperties = {
    fontSize: 11, fontWeight: 600, color: 'var(--ink-muted)',
    textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4,
  }

  const section: React.CSSProperties = { display: 'flex', flexDirection: 'column' }

  const models = PROVIDER_MODELS[provider] ?? []

  const showAgent = mode === 'agent'
  const showProviderSelect = mode === 'agent' || mode === 'algorithm_ml'
  const showAlgo = mode === 'algorithm' || mode === 'machine_learning' || mode === 'algorithm_ml'

  const formatTimer = (s: number) => {
    const m = Math.floor(s / 60).toString().padStart(2, '0')
    const sec = (s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }

  const pillStyle = (status: AgentStatus): React.CSSProperties => {
    const base: React.CSSProperties = {
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 500,
      whiteSpace: 'nowrap', flexShrink: 0,
    }
    if (status === 'running') return { ...base, background: 'rgba(0,200,220,0.15)', color: 'var(--accent)', border: '1px solid var(--accent)' }
    if (status === 'done') return { ...base, background: 'rgba(0,200,100,0.12)', color: '#0c9', border: '1px solid #0c9' }
    if (status === 'error') return { ...base, background: 'rgba(240,60,60,0.12)', color: '#f44', border: '1px solid #f44' }
    return { ...base, background: 'rgba(120,120,120,0.12)', color: 'var(--ink-muted)', border: '1px solid var(--surface-rule)' }
  }

  const decisionColor = (d: string) => {
    const upper = d.toUpperCase()
    if (upper.includes('BUY')) return '#0c9'
    if (upper.includes('SELL')) return '#f44'
    return '#fa0'
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <>
      {/* Global spinner animation */}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>

      <div style={container}>
        {/* ── LEFT PANEL ── */}
        <div style={leftPanel}>

          {/* 1. Mode */}
          <div style={section}>
            <div style={label}>Analysis Mode</div>
            <select className="input" value={mode} onChange={e => setMode(e.target.value as Mode)}>
              <option value="agent">AI Analysis</option>
              <option value="algorithm">Algorithm</option>
              <option value="machine_learning">Machine Learning</option>
              <option value="algorithm_ml">Algorithm + ML</option>
            </select>
          </div>

          {/* 2. Ticker */}
          <div style={section}>
            <div style={label}>Ticker</div>
            <input
              className="input"
              style={{ height: 42, fontSize: 18, fontWeight: 700, textTransform: 'uppercase' }}
              placeholder="e.g. AAPL"
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
            />
          </div>

          {/* 3. Date */}
          <div style={section}>
            <div style={label}>Analysis Date</div>
            <input className="input" type="date" value={date} onChange={e => setDate(e.target.value)} />
          </div>

          {/* 4. Analysts (agent only) */}
          {showAgent && (
            <div style={section}>
              <div style={label}>Analysts</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 5 }}>
                {(['market', 'social', 'news', 'fundamentals'] as const).map(k => (
                  <label key={k} className="a-analyst-pill">
                    <input
                      type="checkbox"
                      checked={analysts[k]}
                      onChange={e => setAnalysts(prev => ({ ...prev, [k]: e.target.checked }))}
                    />
                    {k.charAt(0).toUpperCase() + k.slice(1)}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* 5. Provider (agent or algorithm_ml) */}
          {showProviderSelect && (
            <div style={section}>
              <div style={label}>AI Provider</div>
              <select className="input" value={provider} onChange={e => setProvider(e.target.value)}>
                {['cloudflare','openrouter','nvidia','openai','anthropic','google','xai','deepseek','qwen','glm','azure','ollama'].map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          )}

          {/* 6. Advanced (agent only) */}
          {showAgent && (
            <details style={{ borderTop: '1px solid var(--surface-rule)', paddingTop: 8 }}>
              <summary style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-muted)', cursor: 'pointer', userSelect: 'none', marginBottom: 8 }}>
                Advanced
              </summary>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={section}>
                  <div style={label}>Deep Think Model</div>
                  <select className="input" value={deepModel} onChange={e => setDeepModel(e.target.value)}>
                    {models.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div style={section}>
                  <div style={label}>Quick Think Model</div>
                  <select className="input" value={quickModel} onChange={e => setQuickModel(e.target.value)}>
                    {models.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div style={section}>
                  <div style={label}>Research Depth</div>
                  <select className="input" value={depth} onChange={e => setDepth(e.target.value)}>
                    <option value="1">1 round (fast)</option>
                    <option value="3">3 rounds (default)</option>
                    <option value="5">5 rounds (thorough)</option>
                  </select>
                </div>
                <div style={section}>
                  <div style={label}>Output Language</div>
                  <select className="input" value={language} onChange={e => setLanguage(e.target.value)}>
                    {['English','Chinese','Spanish','French','German','Japanese','Korean'].map(l => (
                      <option key={l} value={l}>{l}</option>
                    ))}
                  </select>
                </div>
              </div>
            </details>
          )}

          {/* 7. Algorithm / ML config */}
          {showAlgo && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--surface-rule)', paddingTop: 8 }}>
              <div style={section}>
                <div style={label}>Strategy Config</div>
                <select className="input" value={algoConfig} onChange={e => setAlgoConfig(e.target.value)}>
                  <option value="try_all">Try All</option>
                  <option value="breakout">Breakout</option>
                  <option value="confirmed_pullback">Confirmed Pullback</option>
                  <option value="mean_reversion">Mean Reversion</option>
                </select>
              </div>
              <div style={section}>
                <div style={label}>Threshold</div>
                <input className="input" type="number" value={threshold} onChange={e => setThreshold(e.target.value)} />
              </div>
              <div style={section}>
                <div style={label}>ML Min Win Prob</div>
                <input className="input" type="number" step="0.01" value={mlProb} onChange={e => setMlProb(e.target.value)} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={useMl} onChange={e => setUseMl(e.target.checked)} />
                Use ML Gate
              </label>
            </div>
          )}

          {/* 8. Run button */}
          <button
            className="btn-primary"
            style={{ height: 44, fontSize: 15, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            disabled={!ticker.trim() || running}
            onClick={handleRun}
          >
            <PlayIcon />
            Run Analysis
          </button>

          {/* 9. Stop button */}
          {running && (
            <button className="btn-danger" style={{ height: 36 }} onClick={handleStop}>
              Stop
            </button>
          )}

          {/* 10. Timer */}
          {running && (
            <div style={{ textAlign: 'center', fontSize: 14, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>
              ⏱ {formatTimer(timerSecs)}
            </div>
          )}

          {/* Spacer */}
          <div style={{ flex: 1 }} />

          {/* 11. Open Chart */}
          <button className="btn-secondary" style={{ height: 36, width: '100%' }} onClick={() => setShowTv(true)}>
            Open Chart
          </button>
        </div>

        {/* ── RIGHT PANEL ── */}
        <div style={rightPanel}>

          {/* Status bar */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12,
            padding: '8px 16px', borderBottom: '1px solid var(--surface-rule)',
            background: 'var(--surface)', flexShrink: 0,
          }}>
            <span style={{ flex: 1, fontSize: 13, color: 'var(--ink-muted)' }}>{statusText}</span>
            {agentProgress && (
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent)' }}>{agentProgress}</span>
            )}
          </div>

          {/* Agent pipeline strip */}
          <div style={{
            borderBottom: '1px solid var(--surface-rule)',
            overflowX: 'auto', flexShrink: 0,
            padding: '6px 12px',
            background: 'var(--bg)',
          }}>
            <div id="a-agent-list" style={{ display: 'flex', gap: 6, alignItems: 'center', minHeight: 30 }}>
              {agents.length === 0 && (
                <span style={{ fontSize: 12, color: 'var(--ink-muted)', fontStyle: 'italic' }}>No agents running yet.</span>
              )}
              {agents.map((a, i) => (
                <div key={i} style={pillStyle(a.status)}>
                  {a.status === 'running' && <Spinner />}
                  {a.name}
                </div>
              ))}
            </div>
          </div>

          {/* Decision banner */}
          {decision && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12,
              padding: '8px 16px', borderBottom: '1px solid var(--surface-rule)',
              background: 'var(--surface)', flexShrink: 0,
            }}>
              <span style={{ fontSize: 12, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                AI Verdict
              </span>
              <span style={{
                padding: '3px 14px', borderRadius: 20, fontWeight: 700, fontSize: 14,
                background: `${decisionColor(decision.decision)}22`,
                color: decisionColor(decision.decision),
                border: `1px solid ${decisionColor(decision.decision)}`,
              }}>
                {decision.decision}
              </span>
              <span style={{ fontSize: 13, color: 'var(--ink-muted)', flex: 1 }}>{decision.text}</span>
              <button
                className="btn-secondary"
                style={{ height: 30, padding: '0 12px', fontSize: 12 }}
                onClick={() => setShowReport(true)}
              >
                View Full Report
              </button>
            </div>
          )}

          {/* Split area */}
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

            {/* Live Feed */}
            <div style={{
              flex: 1, display: 'flex', flexDirection: 'column',
              borderRight: '1px solid var(--surface-rule)', overflow: 'hidden',
            }}>
              <div style={{
                padding: '8px 14px', fontWeight: 700, fontSize: 13,
                borderBottom: '1px solid var(--surface-rule)', background: 'var(--surface)', flexShrink: 0,
              }}>
                Live Feed
              </div>
              <div
                id="a-messages"
                style={{ flex: 1, overflowY: 'auto', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}
              >
                {messages.length === 0 && (
                  <span style={{ fontSize: 13, color: 'var(--ink-muted)', fontStyle: 'italic' }}>No messages yet.</span>
                )}
                {messages.map(m => (
                  <div key={m.id} style={{ fontSize: 13, lineHeight: 1.5 }}>
                    {m.agent && (
                      <span style={{ color: 'var(--accent)', fontWeight: 600, marginRight: 6 }}>[{m.agent}]</span>
                    )}
                    <span style={{ color: 'var(--ink)' }}>{m.text}</span>
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Analysis Report */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{
                padding: '8px 14px', fontWeight: 700, fontSize: 13,
                borderBottom: '1px solid var(--surface-rule)', background: 'var(--surface)', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}>
                <span>Analysis Report</span>
                {reports.length > 0 && (
                  <button
                    className="btn-secondary"
                    style={{ height: 26, padding: '0 10px', fontSize: 11 }}
                    onClick={() => setShowReport(true)}
                  >
                    View All
                  </button>
                )}
              </div>
              <div
                id="a-current-report"
                style={{
                  flex: 1, overflowY: 'auto', padding: '10px 14px',
                  fontFamily: 'var(--font-mono)', fontSize: 12, whiteSpace: 'pre-wrap', color: 'var(--ink)',
                }}
              >
                {reports.length === 0
                  ? <span style={{ color: 'var(--ink-muted)', fontStyle: 'italic' }}>No report yet.</span>
                  : reports[reports.length - 1].content}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* TradingView Modal */}
      {showTv && <TvModal onClose={() => setShowTv(false)} />}

      {/* Full Report Modal */}
      {showReport && <ReportModal reports={reports} onClose={() => setShowReport(false)} />}
    </>
  )
}
