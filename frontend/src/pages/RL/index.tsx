import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import api, { wsUrl } from '@/api/client'
import { LoadingState } from '@/components/shared/LoadingState'

interface RLStatus {
  checkpoint_exists: boolean
  checkpoint_dir?: string
  tickers?: string[]
  total_steps?: number
  hidden?: number[]
  max_position_size?: number
  files?: string[]
}

const cardStyle: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 10,
  padding: 20,
  marginBottom: 0,
}

const statBoxStyle: React.CSSProperties = {
  background: 'rgba(30,41,59,0.6)',
  borderRadius: 8,
  padding: 12,
}

const labelStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--ink-faint)',
  marginBottom: 4,
}

const valueStyle: React.CSSProperties = {
  fontSize: 13,
  color: 'var(--ink)',
  fontFamily: 'monospace',
}

const preStyle: React.CSSProperties = {
  background: '#020509',
  borderRadius: 6,
  padding: 12,
  fontFamily: 'monospace',
  fontSize: 12,
  overflowX: 'auto',
  whiteSpace: 'pre',
  margin: 0,
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'var(--surface)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 6,
  padding: '6px 10px',
  fontSize: 13,
  color: 'var(--ink)',
  boxSizing: 'border-box',
}

const selectStyle: React.CSSProperties = {
  ...inputStyle,
}

const btnPrimaryStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 6,
  padding: '7px 16px',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

const btnSecondaryStyle: React.CSSProperties = {
  background: 'transparent',
  color: 'var(--ink-faint)',
  border: '1px solid var(--surface-rule)',
  borderRadius: 6,
  padding: '5px 12px',
  fontSize: 12,
  cursor: 'pointer',
}

export default function RLPage() {
  const logRef = useRef<HTMLPreElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const [running, setRunning] = useState(false)
  const [logVisible, setLogVisible] = useState(false)

  const [tickers, setTickers] = useState('NVDA AAPL MSFT GOOGL AMZN')
  const [startDate, setStartDate] = useState('2018-01-01')
  const [endDate, setEndDate] = useState('2023-12-31')
  const [iterations, setIterations] = useState(500000)
  const [device, setDevice] = useState('cpu')
  const [checkpoint, setCheckpoint] = useState('')

  const { data: status, isLoading, refetch } = useQuery<RLStatus>({
    queryKey: ['rl-status'],
    queryFn: () => api.get('/rl/status').then(r => r.data),
    refetchOnWindowFocus: false,
  })

  function getBadgeStyle(s: RLStatus | undefined): React.CSSProperties {
    if (!s) return { background: 'rgba(51,65,85,1)', color: '#94a3b8', borderRadius: 999, padding: '6px 12px', fontSize: 13, fontWeight: 600, display: 'inline-block' }
    if (s.checkpoint_exists) return { background: 'rgba(6,78,59,0.6)', color: '#6ee7b7', borderRadius: 999, padding: '6px 12px', fontSize: 13, fontWeight: 600, display: 'inline-block' }
    return { background: 'rgba(51,65,85,1)', color: '#94a3b8', borderRadius: 999, padding: '6px 12px', fontSize: 13, fontWeight: 600, display: 'inline-block' }
  }

  function getBadgeText(s: RLStatus | undefined): string {
    if (!s) return 'Loading…'
    if (s.checkpoint_exists) return 'Checkpoint Ready'
    return 'No Checkpoint'
  }

  function startTraining() {
    if (wsRef.current) return
    const cfg = {
      tickers: tickers.trim().split(/\s+/).filter(Boolean),
      start: startDate,
      end: endDate,
      iterations,
      device,
      checkpoint: checkpoint.trim() || undefined,
    }
    setLogVisible(true)
    setRunning(true)
    if (logRef.current) logRef.current.textContent = ''

    const ws = new WebSocket(wsUrl('/ws/rl-train'))
    wsRef.current = ws

    ws.onopen = () => ws.send(JSON.stringify(cfg))

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as { type: string; text?: string; message?: string }
      const el = logRef.current
      if (!el) return
      if (msg.type === 'log' || msg.type === 'info') {
        el.textContent += (msg.text ?? msg.message ?? '') + '\n'
        el.scrollTop = el.scrollHeight
      } else if (msg.type === 'error') {
        el.textContent += '⚠ ' + (msg.message ?? '') + '\n'
        el.scrollTop = el.scrollHeight
        trainDone()
      } else if (msg.type === 'complete') {
        el.textContent += '\n✅ Training complete.\n'
        el.scrollTop = el.scrollHeight
        trainDone()
        refetch()
      }
    }

    ws.onclose = () => trainDone()
    ws.onerror = () => {
      if (logRef.current) logRef.current.textContent += '⚠ WebSocket error\n'
      trainDone()
    }
  }

  function stopTraining() {
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null }
    trainDone()
  }

  function trainDone() {
    wsRef.current = null
    setRunning(false)
  }

  const tickersDisplay = status?.tickers?.join(', ') || '—'
  const stepsDisplay = status?.total_steps != null ? status.total_steps.toLocaleString() : '—'
  const networkDisplay = status?.hidden?.length ? status.hidden.join('×') : '—'
  const maxPosDisplay = status?.max_position_size != null ? (status.max_position_size * 100).toFixed(0) + '%' : '—'

  return (
    <div style={{ padding: 24, maxWidth: 900, display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* TD3 Checkpoint Status */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>TD3 Checkpoint Status</div>
          <button style={btnSecondaryStyle} onClick={() => refetch()}>Refresh</button>
        </div>

        {isLoading ? (
          <LoadingState message="Loading status…" />
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
              <span style={getBadgeStyle(status)}>{getBadgeText(status)}</span>
              <span style={{ fontSize: 13, color: 'var(--ink-faint)' }}>{status?.checkpoint_dir ?? ''}</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
              <div style={statBoxStyle}>
                <div style={labelStyle}>Tickers</div>
                <div style={{ ...valueStyle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tickersDisplay}</div>
              </div>
              <div style={statBoxStyle}>
                <div style={labelStyle}>Steps Trained</div>
                <div style={valueStyle}>{stepsDisplay}</div>
              </div>
              <div style={statBoxStyle}>
                <div style={labelStyle}>Network</div>
                <div style={valueStyle}>{networkDisplay}</div>
              </div>
              <div style={statBoxStyle}>
                <div style={labelStyle}>Max Pos Size</div>
                <div style={valueStyle}>{maxPosDisplay}</div>
              </div>
            </div>

            {status?.files && status.files.length > 0 && (
              <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {status.files.map(f => (
                  <span key={f} style={{ fontSize: 11, fontFamily: 'monospace', padding: '2px 8px', borderRadius: 4, background: 'rgba(30,41,59,1)', color: '#94a3b8' }}>{f}</span>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* How It Works */}
      <div style={cardStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 8 }}>How It Works</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <p style={{ margin: 0 }}>The <strong style={{ color: 'var(--ink)' }}>TD3 (Twin-Delayed DDPG)</strong> agent learns a continuous portfolio allocation policy directly from OHLCV price data — no LLMs required.</p>
          <p style={{ margin: 0 }}>At inference time <code style={{ color: '#fb923c', background: '#0f172a', padding: '0 4px', borderRadius: 3 }}>rl_signal.py</code> loads the checkpoint and produces a score in <strong style={{ color: 'var(--ink)' }}>[-1 … +1]</strong> per ticker (Strong Sell → Strong Buy). This score is injected as a markdown block into every LLM analyst prompt as a supplemental signal.</p>
          <p style={{ margin: 0 }}>The agent uses log-returns, RSI, MACD and volume ratio over a lookback window — purely technical, no fundamentals.</p>
        </div>
      </div>

      {/* Training Commands */}
      <div style={cardStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Training Commands</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>Run from project root in terminal. GPU recommended.</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Quick start — 5 tickers, 500k iterations (default):</div>
            <pre style={{ ...preStyle, color: '#6ee7b7' }}>{`python scripts/train_rl_agent.py --tickers NVDA AAPL MSFT GOOGL AMZN \\
    --start 2018-01-01 --end 2023-12-31 --iterations 500000`}</pre>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Resume from existing checkpoint:</div>
            <pre style={{ ...preStyle, color: '#6ee7b7' }}>{`python scripts/train_rl_agent.py --tickers NVDA AAPL MSFT GOOGL AMZN \\
    --checkpoint rl_models/td3_checkpoint`}</pre>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Full S&amp;P 100 (slow — use GPU):</div>
            <pre style={{ ...preStyle, color: '#6ee7b7' }}>{`python scripts/train_rl_agent.py --tickers-file tickers/sp100.txt \\
    --start 2015-01-01 --end 2023-12-31 --iterations 1000000 --device cuda`}</pre>
          </div>
        </div>
      </div>

      {/* ML Model Training Commands */}
      <div style={cardStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>ML Model Training Commands</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 12 }}>Generates training data directly from Yahoo Finance — no prior backtest needed. This is the correct path if you haven't generated data yet.</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>✅ <strong style={{ color: 'var(--ink)' }}>New way</strong> — downloads OHLCV, labels it, and trains all in one step:</div>
            <pre style={{ ...preStyle, color: '#67e8f9' }}>{`python scripts/train_ml_from_stock_data.py \\
    --tickers all_tickers.txt \\
    --start 2019-01-01 \\
    --end 2024-12-31 \\
    --output-dir ml_models/stock_universe`}</pre>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Quick test — 100 tickers only:</div>
            <pre style={{ ...preStyle, color: '#67e8f9' }}>{`python scripts/train_ml_from_stock_data.py \\
    --tickers all_tickers.txt --max-tickers 100 \\
    --start 2019-01-01 --end 2024-12-31 \\
    --output-dir ml_models/stock_universe`}</pre>
          </div>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Old way — only if you have a backtest CSV/JSON export:</div>
            <pre style={{ ...preStyle, color: '#64748b' }}>{`python scripts/train_ml_models.py \\
    --input backtest_results_YYYYMMDD.json \\
    --output-dir ml_models/stock_universe --hold 3`}</pre>
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>
            💡 Re-running the new script reuses the downloaded CSV — it won't re-hit Yahoo unless you add <code style={{ color: '#fb923c', background: '#0f172a', padding: '0 4px', borderRadius: 3 }}>--rebuild-price-cache</code>
          </div>
        </div>
      </div>

      {/* Launch RL Training */}
      <div style={cardStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Launch RL Training</div>
        <div style={{ fontSize: 12, color: 'var(--ink-faint)', marginBottom: 16 }}>
          Streams output from <code style={{ color: '#fb923c', background: '#0f172a', padding: '0 4px', borderRadius: 3 }}>train_rl_agent.py</code> in real time. Training runs in the background — closing this panel won't stop it.
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 12 }}>
          <div style={{ gridColumn: '1 / -1' }}>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>Tickers (space-separated)</label>
            <input style={{ ...inputStyle, fontFamily: 'monospace' }} value={tickers} onChange={e => setTickers(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>Start Date</label>
            <input style={inputStyle} value={startDate} onChange={e => setStartDate(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>End Date</label>
            <input style={inputStyle} value={endDate} onChange={e => setEndDate(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>Iterations</label>
            <input style={inputStyle} type="number" value={iterations} min={10000} step={10000} onChange={e => setIterations(parseInt(e.target.value) || 500000)} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>Device</label>
            <select style={selectStyle} value={device} onChange={e => setDevice(e.target.value)}>
              <option value="cpu">CPU (Intel Mac — only option on macOS AMD)</option>
              <option value="cuda">CUDA (NVIDIA GPU only)</option>
            </select>
          </div>
          <div style={{ gridColumn: 'span 2' }}>
            <label style={{ fontSize: 11, color: 'var(--ink-faint)', display: 'block', marginBottom: 4 }}>Resume Checkpoint (optional)</label>
            <input style={{ ...inputStyle, fontFamily: 'monospace' }} value={checkpoint} onChange={e => setCheckpoint(e.target.value)} placeholder="rl_models/td3_checkpoint" />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
          <button style={{ ...btnPrimaryStyle, opacity: running ? 0.6 : 1 }} onClick={startTraining} disabled={running}>
            Start Training
          </button>
          {running && (
            <button style={btnSecondaryStyle} onClick={stopTraining}>
              Stop
            </button>
          )}
        </div>

        {logVisible && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 12, color: 'var(--ink-faint)' }}>Training output</span>
              <button style={{ fontSize: 12, color: 'var(--ink-faint)', background: 'none', border: 'none', cursor: 'pointer' }} onClick={() => { if (logRef.current) logRef.current.textContent = '' }}>Clear</button>
            </div>
            <pre ref={logRef} style={{ background: '#020509', borderRadius: 8, padding: 16, fontSize: 12, fontFamily: 'monospace', color: '#cbd5e1', maxHeight: 288, overflow: 'auto', whiteSpace: 'pre-wrap', margin: 0 }} />
          </div>
        )}
      </div>

    </div>
  )
}
