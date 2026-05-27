import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '@/api/client'
import { getPaperStatus, getPaperEquity, getPaperAnalytics, startPaperRunner, stopPaperRunner, getPaperAutostart, setPaperAutostart } from '@/api/paper'
import { Badge } from '@/components/ui/Badge'
import { Drawer } from '@/components/ui/Drawer'
import { LoadingState, ErrorState } from '@/components/shared/LoadingState'
import { LineChart } from '@/components/charts/LineChart'
import { EquityAreaChart } from '@/components/charts/EquityAreaChart'
import { WinLossBar } from '@/components/charts/WinLossBar'
import type { StrategyMetric } from '@/components/charts/WinLossBar'
import { useToast } from '@/components/ui/Toast'
import { useAuthStore } from '@/store/auth'
import { CandidatePanel } from '@/components/candidates/CandidatePanel'
import type { PaperAccount, CandidateRow } from '@/types'

const STRATEGY_COLORS: Record<string, string> = {
  algorithm:       '#22d3ee',
  machine_learning:'#a78bfa',
  ml_new:          '#60a5fa',
  combined:        '#34d399',
  pure_ai:         '#fb923c',
  long_hold:       '#f59e0b',
  unified_brain:   '#e879f9',
}

function fmt$(n: number) {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}
function fmtPct(n: number | null | undefined) {
  if (n == null) return '—'
  return (n >= 0 ? '+' : '') + (n * 100).toFixed(2) + '%'
}
function pnlColor(n: number) {
  return n >= 0 ? '#4ade80' : '#f87171'
}

// ── Account Card ─────────────────────────────────────────────────────────
function AccountCard({ account, onClick }: { account: PaperAccount; onClick: () => void }) {
  const s     = account.summary
  const start = Number(s?.starting_cash ?? 0)
  const total = Number(s?.total_value   ?? s?.cash ?? 0)
  const pnl   = start ? total - start : Number(s?.realized_pnl ?? 0)
  const open  = Array.isArray(s?.open_positions) ? s.open_positions.length : 0
  const color = STRATEGY_COLORS[account.strategy] ?? '#94a3b8'

  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--surface-rule)',
        borderRadius: 8,
        padding: 16,
        cursor: 'pointer',
        transition: 'border-color .15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = color)}
      onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--surface-rule)')}
      onClick={onClick}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{account.label}</div>
        <span style={{
          fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 999,
          background: `${color}22`, color, border: `1px solid ${color}44`,
        }}>
          {account.candidates?.count ?? 0}
        </span>
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink)' }}>{fmt$(total)}</div>
      <div style={{ fontSize: 12, fontWeight: 600, color: pnlColor(pnl), marginBottom: 16 }}>
        {fmt$(pnl)}{' '}
        <span style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
          {start > 0 ? fmtPct(pnl / start) : ''}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8, fontSize: 11 }}>
        <div>
          <div style={{ color: 'var(--ink-faint)' }}>Cash</div>
          <div style={{ color: 'var(--ink)' }}>{fmt$(s?.cash ?? 0)}</div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-faint)' }}>Open</div>
          <div style={{ color: 'var(--ink)' }}>{open}</div>
        </div>
        <div>
          <div style={{ color: 'var(--ink-faint)' }}>Closed</div>
          <div style={{ color: 'var(--ink)' }}>{s?.trades_closed ?? 0}</div>
        </div>
      </div>
      {s?.not_started && (
        <div style={{ marginTop: 10, fontSize: 11, color: 'var(--ink-faint)' }}>Not started</div>
      )}
      <div style={{ fontSize: 10, color: 'var(--ink-faint)', marginTop: 8, textAlign: 'right' }}>
        click for details →
      </div>
    </div>
  )
}

// ── Candidates Table ──────────────────────────────────────────────────────
function CandidatesTable({ rows, onSelect }: { rows: CandidateRow[]; onSelect: (r: CandidateRow) => void }) {
  if (!rows.length) {
    return <div style={{ padding: '24px 16px', color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No candidates</div>
  }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--surface-rule)' }}>
            {['Strategy','Ticker','Entry','Target','Stop','Score','R:R','ML%','Loss%','E.Return','Gate'].map(h => (
              <th key={h} style={{ padding: '8px 12px', fontWeight: 500,
                                   color: 'var(--ink-faint)', whiteSpace: 'nowrap',
                                   textAlign: h === 'Strategy' || h === 'Ticker' || h === 'Gate' ? 'left' : 'right' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody id="paper-candidates-tbody">
          {rows.slice(0, 100).map((row, i) => {
            const e = Number(row.entry), t = Number(row.target), s = Number(row.stop)
            const rr = (e > 0 && e > s) ? ((t - e) / (e - s)).toFixed(2) : '—'
            const rrColor = rr === '—' ? 'var(--ink-faint)' : Number(rr) >= 2 ? '#4ade80' : Number(rr) >= 1 ? '#fbbf24' : '#f87171'
            const llp = Number(row.large_loss_probability)
            const gateStatus = row.gate_status ?? ''
            const gateBg   = gateStatus === 'PASS' ? '#4ade8022' : '#fbbf2422'
            const gateText = gateStatus === 'PASS' ? '#4ade80'   : '#fbbf24'
            return (
              <tr key={i} className="tr-hover" onClick={() => onSelect(row)} style={{ borderBottom: '1px solid var(--surface-rule)', cursor: 'pointer' }}>
                <td style={{ padding: '7px 12px', color: 'var(--ink-faint)' }}>{row._stratLabel || row.account || ''}</td>
                <td style={{ padding: '7px 12px', fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{row.ticker}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>${Number(row.entry).toFixed(2)}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: '#4ade80', fontFamily: 'var(--font-mono)' }}>${Number(row.target).toFixed(2)}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: '#f87171', fontFamily: 'var(--font-mono)' }}>${Number(row.stop).toFixed(2)}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>{Math.round(Number(row.score))}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: rrColor }}>{rr}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: '#67e8f9', fontFamily: 'var(--font-mono)' }}>{fmtPct(Number(row.ml_probability))}</td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: llp > 0.3 ? '#f87171' : 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}>
                  {row.large_loss_probability != null ? fmtPct(llp) : '—'}
                </td>
                <td style={{ padding: '7px 12px', textAlign: 'right', color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{fmtPct(Number(row.expected_return))}</td>
                <td style={{ padding: '7px 12px' }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                    background: gateBg, color: gateText,
                  }}>
                    {gateStatus || '—'}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Portfolio Drawer ──────────────────────────────────────────────────────
function PortfolioDrawer({ account, open, onClose }: {
  account: PaperAccount | null; open: boolean; onClose: () => void
}) {
  const equityQuery = useQuery({
    queryKey: ['paper', 'equity'],
    queryFn: getPaperEquity,
    enabled: open,
    staleTime: 30_000,
  })

  const equityData = (equityQuery.data ?? [])
    .filter(pt => account && pt.strategy === account.strategy)
    .map(pt => ({ x: pt.t.slice(0, 10), y: pt.v }))

  return (
    <Drawer open={open} onClose={onClose} title={account?.label ?? 'Portfolio'} width="440px">
      {!account ? null : (
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Equity chart */}
          <div>
            <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginBottom: 8 }}>Equity Curve</div>
            {equityData.length > 1 ? (
              <LineChart
                datasets={[{ label: account.label, data: equityData,
                             color: STRATEGY_COLORS[account.strategy] ?? '#94a3b8', fill: true }]}
                height={160}
                yFormatter={v => '$' + Math.round(v).toLocaleString()}
              />
            ) : (
              <div style={{ fontSize: 11, color: 'var(--ink-faint)', padding: '24px 0', textAlign: 'center' }}>
                No equity data yet
              </div>
            )}
          </div>

          {/* Open positions */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-faint)', marginBottom: 8 }}>Open Positions</div>
            {(account.summary?.open_positions ?? []).length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--ink-faint)' }}>No open positions</div>
            ) : (
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                    {['Ticker','Shares','Avg Price'].map(h => (
                      <th key={h} style={{ padding: '6px 10px', fontWeight: 500, color: 'var(--ink-faint)',
                                          textAlign: h === 'Ticker' ? 'left' : 'right' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {account.summary!.open_positions.map((pos, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                      <td style={{ padding: '6px 10px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>{pos.ticker}</td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--ink)' }}>{pos.shares}</td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>
                        ${Number(pos.avg_price).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </Drawer>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────
// ── Runner Controls (admin only) ─────────────────────────────────────────
function RunnerControls({ running }: { running: boolean }) {
  const { toast } = useToast()
  const qc = useQueryClient()
  // Config state
  const [cash, setCash]           = useState('10000')
  const [scanInterval, setScanInterval] = useState('15')
  const [posMax, setPosMax]       = useState('25')
  const [posMin, setPosMin]       = useState('10')
  const [maxPos, setMaxPos]       = useState('5')
  const [mlThresh, setMlThresh]   = useState('0.72')
  const [mlLossMax, setMlLossMax] = useState('0.25')
  const [aiShortlist, setAiShortlist] = useState('30')
  const [aiPicks, setAiPicks]     = useState('5')
  const [model, setModel]         = useState('openai/gpt-4o-mini')
  const [tickerFile, setTickerFile] = useState('all_tickers.txt')
  const [holdOvernight, setHoldOvernight]     = useState(true)
  const [includeAi, setIncludeAi]             = useState(true)
  const [tradeFidelity, setTradeFidelity]     = useState(false)
  const [executeReal, setExecuteReal]         = useState(false)
  const [mlRetMin, setMlRetMin]               = useState('0.0')
  const [takeProfitPct, setTakeProfitPct]     = useState('0')
  const [stopLossPct, setStopLossPct]         = useState('0')
  const [highConfThresh, setHighConfThresh]   = useState('0.80')
  const [maxTickers, setMaxTickers]           = useState('0')
  const [modelBundle, setModelBundle]         = useState('ml_models/stock_universe_candidate_20260512/model_bundle.joblib')
  const [smsNumber, setSmsNumber]             = useState('')
  const [expanded, setExpanded]               = useState(false)
  const [autostartEnabled, setAutostartEnabled] = useState(false)
  const [warmupMins, setWarmupMins]           = useState('30')

  // Load autostart config
  const autostartQ = useQuery({
    queryKey: ['paper', 'autostart'],
    queryFn: getPaperAutostart,
    staleTime: 60_000,
  })
  useEffect(() => {
    const cfg = autostartQ.data as { enabled?: boolean; premarket_warmup_minutes?: number } | undefined
    if (cfg) {
      setAutostartEnabled(cfg.enabled ?? false)
      setWarmupMins(String(cfg.premarket_warmup_minutes ?? 30))
    }
  }, [autostartQ.data])

  const autostartMut = useMutation({
    mutationFn: (enabled: boolean) => setPaperAutostart(enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paper', 'autostart'] }),
  })

  const startMut = useMutation({
    mutationFn: () => startPaperRunner({
      starting_cash: Number(cash) || 10000,
      scan_interval_minutes: Number(scanInterval) || 15,
      position_cap_pct: Number(posMax) || 25,
      position_cap_min_pct: Number(posMin) || 10,
      max_open_positions: Number(maxPos) || 5,
      ml_probability_threshold: Number(mlThresh) || 0.72,
      ml_loss_max: Number(mlLossMax) || 0.25,
      ml_return_min: Number(mlRetMin) || 0,
      take_profit_pct: Number(takeProfitPct) || 0,
      stop_loss_pct: Number(stopLossPct) || 0,
      high_conf_threshold: Number(highConfThresh) || 0.80,
      max_tickers: Number(maxTickers) || 0,
      model_bundle: modelBundle,
      ai_shortlist: Number(aiShortlist) || 30,
      ai_picks: Number(aiPicks) || 5,
      openrouter_model: model,
      ticker_file: tickerFile,
      sms_number: smsNumber || undefined,
      hold_overnight: holdOvernight,
      include_pure_ai: includeAi,
      trade_fidelity: tradeFidelity,
      execute_real_trades: executeReal,
    }),
    onSuccess: () => { toast.success('Runner started'); qc.invalidateQueries({ queryKey: ['paper', 'status'] }) },
    onError: (e: unknown) => toast.error((e as Error)?.message ?? 'Start failed'),
  })
  const stopMut = useMutation({
    mutationFn: stopPaperRunner,
    onSuccess: () => { toast.success('Runner stopped'); qc.invalidateQueries({ queryKey: ['paper', 'status'] }) },
    onError: (e: unknown) => toast.error((e as Error)?.message ?? 'Stop failed'),
  })

  const inp: React.CSSProperties = {
    padding: '6px 9px', background: 'var(--surface-soft)', border: '1px solid var(--surface-rule)',
    borderRadius: 6, color: 'var(--ink)', fontSize: 12, fontFamily: 'inherit', width: '100%',
  }
  const lbl: React.CSSProperties = { fontSize: 10, color: 'var(--ink-faint)', display: 'block', marginBottom: 3 }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>Runner Control</div>
        <button onClick={() => setExpanded(e => !e)}
          style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>
          {expanded ? 'Hide config ▲' : 'Show config ▼'}
        </button>
      </div>

      {/* Main action row */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: expanded ? 14 : 0 }}>
        {!running ? (
          <button id="paper-start-btn"
            style={{ padding: '8px 22px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, fontWeight: 700, fontSize: 13, cursor: 'pointer' }}
            onClick={() => startMut.mutate()} disabled={startMut.isPending}>
            {startMut.isPending ? 'Starting…' : '▶ Start Runner'}
          </button>
        ) : (
          <button id="paper-stop-btn"
            style={{ padding: '8px 22px', background: '#ef4444', color: '#fff', border: 'none', borderRadius: 6, fontWeight: 700, fontSize: 13, cursor: 'pointer' }}
            onClick={() => stopMut.mutate()} disabled={stopMut.isPending}>
            {stopMut.isPending ? 'Stopping…' : '■ Stop Runner'}
          </button>
        )}
        {/* Autostart toggle */}
        <label id="paper-autostart-enabled" style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--ink-muted)', cursor: 'pointer', marginLeft: 8 }}>
          <input
            type="checkbox"
            checked={autostartEnabled}
            onChange={e => { setAutostartEnabled(e.target.checked); autostartMut.mutate(e.target.checked) }}
          />
          Auto-start on boot
        </label>
        {autostartEnabled && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--ink-faint)' }}>
            <label htmlFor="paper-premarket-warmup" style={{ whiteSpace: 'nowrap' }}>Warmup</label>
            <input
              id="paper-premarket-warmup"
              type="number"
              min="0"
              max="120"
              step="5"
              value={warmupMins}
              onChange={e => setWarmupMins(e.target.value)}
              style={{ ...inp, width: 60 }}
            />
            <span>min</span>
          </div>
        )}
      </div>

      {/* Config grid */}
      {expanded && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, paddingTop: 14, borderTop: '1px solid var(--surface-rule)' }}>
          <div><label style={lbl}>Starting Cash ($)</label>
            <input id="paper-starting-cash" type="number" min="100" step="100" value={cash} onChange={e => setCash(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Scan Every (min)</label>
            <input id="paper-scan-interval" type="number" min="1" step="1" value={scanInterval} onChange={e => setScanInterval(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Position Max %</label>
            <input id="paper-position-cap" type="number" min="1" max="100" step="1" value={posMax} onChange={e => setPosMax(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Position Min %</label>
            <input id="paper-position-cap-min" type="number" min="0.5" max="100" step="0.5" value={posMin} onChange={e => setPosMin(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Max Positions</label>
            <input id="paper-max-positions" type="number" min="1" max="50" step="1" value={maxPos} onChange={e => setMaxPos(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>ML Win Threshold</label>
            <input id="paper-ml-prob-threshold" type="number" min="0.1" max="0.99" step="0.01" value={mlThresh} onChange={e => setMlThresh(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>ML Loss Max</label>
            <input id="paper-ml-loss-max" type="number" min="0.01" max="0.99" step="0.01" value={mlLossMax} onChange={e => setMlLossMax(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>AI Shortlist</label>
            <input id="paper-ai-shortlist" type="number" min="1" step="1" value={aiShortlist} onChange={e => setAiShortlist(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>AI Picks</label>
            <input id="paper-ai-picks" type="number" min="1" step="1" value={aiPicks} onChange={e => setAiPicks(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>ML Return Min</label>
            <input id="paper-ml-ret-min" type="number" min="-0.5" max="0.5" step="0.01" value={mlRetMin} onChange={e => setMlRetMin(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Take Profit %</label>
            <input id="paper-take-profit-pct" type="number" min="0" max="50" step="0.5" value={takeProfitPct} onChange={e => setTakeProfitPct(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Stop Loss %</label>
            <input id="paper-stop-loss-pct" type="number" min="0" max="50" step="0.5" value={stopLossPct} onChange={e => setStopLossPct(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>High Conf Threshold</label>
            <input id="paper-high-conf-threshold" type="number" min="0.5" max="0.99" step="0.01" value={highConfThresh} onChange={e => setHighConfThresh(e.target.value)} style={inp} /></div>
          <div><label style={lbl}>Max Tickers (0=all)</label>
            <input id="paper-max-tickers" type="number" min="0" step="1" value={maxTickers} onChange={e => setMaxTickers(e.target.value)} style={inp} /></div>
          <div style={{ gridColumn: 'span 2' }}><label style={lbl}>OpenRouter Model</label>
            <input id="paper-openrouter-model" type="text" value={model} onChange={e => setModel(e.target.value)} style={{ ...inp, fontFamily: 'var(--font-mono)' }} /></div>
          <div style={{ gridColumn: 'span 2' }}><label style={lbl}>Ticker File</label>
            <input id="paper-tickers" type="text" value={tickerFile} onChange={e => setTickerFile(e.target.value)} style={{ ...inp, fontFamily: 'var(--font-mono)' }} /></div>
          <div style={{ gridColumn: '1 / -1' }}><label style={lbl}>Model Bundle Path</label>
            <input id="paper-new-model-bundle" type="text" value={modelBundle} onChange={e => setModelBundle(e.target.value)} style={{ ...inp, fontFamily: 'var(--font-mono)' }} placeholder="ml_models/..." /></div>
          <div style={{ gridColumn: '1 / -1' }}><label style={lbl}>SMS Number (override .env)</label>
            <input id="paper-sms-number" type="text" value={smsNumber} onChange={e => setSmsNumber(e.target.value)} style={{ ...inp, fontFamily: 'var(--font-mono)' }} placeholder="Uses TEXTNOW_PHONE or PAPER_SMS_NUMBER from .env if blank" /></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, gridColumn: '1 / -1' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--ink-muted)', cursor: 'pointer' }}>
              <input id="paper-include-ai" type="checkbox" checked={includeAi} onChange={e => setIncludeAi(e.target.checked)} />
              Pure AI
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--ink-muted)', cursor: 'pointer' }}>
              <input id="paper-hold-overnight" type="checkbox" checked={holdOvernight} onChange={e => setHoldOvernight(e.target.checked)} />
              Hold overnight to avoid day trading
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--ink-muted)', cursor: 'pointer' }}>
              <input id="paper-trade-fidelity" type="checkbox" checked={tradeFidelity} onChange={e => setTradeFidelity(e.target.checked)} />
              Send to Fidelity API (Preview Only)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: '#f87171', fontWeight: 600, cursor: 'pointer' }} title="Actually clicks Place Order on Fidelity API with real money! Requires SMS HIL approval">
              <input id="paper-trade-fidelity-execute" type="checkbox" checked={executeReal} onChange={e => setExecuteReal(e.target.checked)} />
              Execute Real Money Trades (Requires SMS HIL)
            </label>
          </div>
        </div>
      )}
    </div>
  )
}

export default function PaperPage() {
  const [drawerAccount, setDrawerAccount] = useState<PaperAccount | null>(null)
  const [candFilter, setCandFilter] = useState<string>('all')
  const [candView, setCandView] = useState<'live' | 'history'>('live')
  const [historyDate, setHistoryDate] = useState<string>('')
  const [selectedCand, setSelectedCand] = useState<CandidateRow | null>(null)
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'

  const historyQ = useQuery({
    queryKey: ['paper', 'candidates-history'],
    queryFn: () => api.get('/paper/candidates-history').then(r => Array.isArray(r.data) ? r.data : []).catch(() => []),
    staleTime: 300_000,
    enabled: candView === 'history',
  })

  const { data, isLoading, error } = useQuery({
    queryKey: ['paper', 'status'],
    queryFn: getPaperStatus,
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  if (isLoading) return <LoadingState message="Loading paper trading…" />
  if (error)     return <ErrorState message="Failed to load paper status." />

  const accounts  = data?.accounts ?? []
  const allCands  = accounts.flatMap(a =>
    (a.candidates?.rows ?? []).map(r => ({
      ...r,
      _stratLabel: a.label,
      _strategy: a.strategy,
    }))
  )
  const filteredCands = candFilter === 'all'
    ? allCands
    : allCands.filter(r => r._strategy === candFilter)

  return (
    <div id="panel-paper" style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>Paper Trading</div>
          <div style={{ fontSize: 11, color: 'var(--ink-faint)', marginTop: 2 }}>
            {data?.date ?? '—'} · {data?.process.running ? '🟢 Runner active' : '⚫ Runner stopped'}
          </div>
        </div>
        <Badge variant={data?.process.running ? 'success' : 'default'}>
          {data?.process.running ? 'RUNNING' : 'STOPPED'}
        </Badge>
      </div>

      {/* Runner controls — admin only */}
      {isAdmin && <RunnerControls running={!!data?.process.running} />}

      {/* Account cards */}
      <div id="paper-accounts" style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: 14,
      }}>
        {accounts.map(account => (
          <AccountCard
            key={account.strategy}
            account={account}
            onClick={() => setDrawerAccount(account)}
          />
        ))}
      </div>

      {/* Equity Chart */}
      <EquityChartPanel accounts={accounts} />

      {/* Analytics Panel */}
      <AnalyticsPanel accounts={accounts} />

      {/* Candidates */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--surface-rule)',
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>Candidates</div>
          {/* Live / History top-level toggle */}
          <div id="cand-view-tabs" style={{ display: 'flex', gap: 4 }}>
            {(['live', 'history'] as const).map(v => (
              <button
                key={v}
                style={{
                  padding: '2px 10px', fontSize: 11, fontWeight: 700,
                  border: '1px solid', borderRadius: 4, cursor: 'pointer',
                  background: candView === v ? 'var(--accent)' : 'transparent',
                  color:      candView === v ? '#fff' : 'var(--ink-faint)',
                  borderColor: candView === v ? 'var(--accent)' : 'var(--surface-rule)',
                  transition: 'background .1s, color .1s',
                  textTransform: 'capitalize',
                }}
                onClick={() => setCandView(v)}
              >
                {v}
              </button>
            ))}
          </div>
          {/* Strategy filter tabs — only in Live view */}
          {candView === 'live' && (
            <div id="cand-strategy-tabs" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {[{ id: 'all', label: `All (${allCands.length})` },
                ...accounts
                  .filter(a => (a.candidates?.count ?? 0) > 0)
                  .map(a => ({ id: a.strategy, label: `${a.label} (${a.candidates?.count ?? 0})` }))
              ].map(tab => (
                <button
                  key={tab.id}
                  className="cand-strat-tab"
                  style={{
                    padding: '2px 9px', fontSize: 11, fontWeight: 600,
                    border: '1px solid', borderRadius: 4, cursor: 'pointer',
                    background: candFilter === tab.id ? 'var(--accent)' : 'transparent',
                    color:      candFilter === tab.id ? '#fff' : 'var(--ink-faint)',
                    borderColor: candFilter === tab.id ? 'var(--accent)' : 'var(--surface-rule)',
                    transition: 'background .1s, color .1s',
                  }}
                  onClick={() => setCandFilter(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {candView === 'live' ? (
          <CandidatesTable rows={filteredCands} onSelect={(r) => setSelectedCand(r)} />
        ) : (
          <div>
            {/* Date filter */}
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--surface-rule)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <label style={{ fontSize: 11, color: 'var(--ink-faint)' }}>Filter by date:</label>
              <input
                type="date"
                value={historyDate}
                onChange={e => setHistoryDate(e.target.value)}
                style={{
                  padding: '4px 8px', background: 'var(--surface-soft)',
                  border: '1px solid var(--surface-rule)', borderRadius: 6,
                  color: 'var(--ink)', fontSize: 12, fontFamily: 'inherit',
                }}
              />
              {historyDate && (
                <button
                  onClick={() => setHistoryDate('')}
                  style={{ fontSize: 11, color: 'var(--ink-faint)', background: 'none', border: 'none', cursor: 'pointer' }}
                >
                  Clear
                </button>
              )}
            </div>

            {historyQ.isLoading ? (
              <div style={{ padding: '24px 16px', color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>Loading history…</div>
            ) : (() => {
              const histRows = (historyQ.data as Array<CandidateRow & { date?: string }> | undefined) ?? []
              const filtered = historyDate ? histRows.filter(r => (r.date ?? '').startsWith(historyDate)) : histRows

              if (!histRows.length) {
                return <div style={{ padding: '24px 16px', color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No historical candidates available</div>
              }
              if (!filtered.length) {
                return <div style={{ padding: '24px 16px', color: 'var(--ink-faint)', fontSize: 12, textAlign: 'center' }}>No candidates for selected date</div>
              }

              return (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--surface-rule)' }}>
                        {['Date', 'Strategy', 'Ticker', 'Entry', 'Target', 'Stop', 'Score', 'ML%', 'Gate'].map(h => (
                          <th key={h} style={{ padding: '8px 12px', fontWeight: 500, color: 'var(--ink-faint)', whiteSpace: 'nowrap',
                            textAlign: h === 'Date' || h === 'Strategy' || h === 'Ticker' || h === 'Gate' ? 'left' : 'right' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.slice(0, 200).map((row, i) => {
                        const gateStatus = row.gate_status ?? ''
                        const gateBg   = gateStatus === 'PASS' ? '#4ade8022' : '#fbbf2422'
                        const gateText = gateStatus === 'PASS' ? '#4ade80'   : '#fbbf24'
                        return (
                          <tr key={i} className="tr-hover" onClick={() => setSelectedCand(row)}
                              style={{ borderBottom: '1px solid var(--surface-rule)', cursor: 'pointer' }}>
                            <td style={{ padding: '7px 12px', color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>
                              {row.date ?? '—'}
                            </td>
                            <td style={{ padding: '7px 12px', color: 'var(--ink-faint)' }}>{row._stratLabel || row.account || ''}</td>
                            <td style={{ padding: '7px 12px', fontWeight: 700, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>{row.ticker}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>${Number(row.entry).toFixed(2)}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#4ade80', fontFamily: 'var(--font-mono)' }}>${Number(row.target).toFixed(2)}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#f87171', fontFamily: 'var(--font-mono)' }}>${Number(row.stop).toFixed(2)}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>{Math.round(Number(row.score))}</td>
                            <td style={{ padding: '7px 12px', textAlign: 'right', color: '#67e8f9', fontFamily: 'var(--font-mono)' }}>{fmtPct(Number(row.ml_probability))}</td>
                            <td style={{ padding: '7px 12px' }}>
                              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: gateBg, color: gateText }}>
                                {gateStatus || '—'}
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )
            })()}
          </div>
        )}
      </div>

      {/* Runner log */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-faint)', marginBottom: 8 }}>Runner Log</div>
        <pre id="paper-log" style={{
          fontSize: 11, color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)',
          maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', margin: 0,
          background: 'var(--canvas)', borderRadius: 4, padding: 10,
        }}>
          {(data?.log_lines ?? []).slice(-50).join('\n') || 'No log output.'}
        </pre>
      </div>

      {/* Portfolio drawer */}
      <PortfolioDrawer
        account={drawerAccount}
        open={!!drawerAccount}
        onClose={() => setDrawerAccount(null)}
      />

      {/* Candidate detail panel */}
      <CandidatePanel
        candidate={selectedCand}
        open={selectedCand !== null}
        onClose={() => setSelectedCand(null)}
        strategyColor={selectedCand ? (STRATEGY_COLORS[selectedCand._strategy ?? ''] ?? '#94a3b8') : undefined}
      />
    </div>
  )
}

// ── Equity chart panel (separate component to avoid re-render on drawer) ─
function EquityChartPanel({ accounts }: { accounts: PaperAccount[] }) {
  const { data } = useQuery({
    queryKey: ['paper', 'equity'],
    queryFn: getPaperEquity,
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  if (!data?.length) return null

  const byStrategy: Record<string, Array<{ x: string; y: number }>> = {}
  data.forEach(pt => {
    if (!byStrategy[pt.strategy]) byStrategy[pt.strategy] = []
    byStrategy[pt.strategy].push({ x: pt.t.slice(0, 16).replace('T', ' '), y: pt.v })
  })

  const series = accounts
    .filter(a => byStrategy[a.strategy]?.length > 1)
    .map(a => ({
      label: a.label,
      data:  byStrategy[a.strategy],
      color: STRATEGY_COLORS[a.strategy] ?? '#94a3b8',
    }))

  if (!series.length) return null

  return (
    <div id="paper-equity-chart-panel"
         style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 12 }}>Equity Curve</div>
      <canvas id="paper-equity-chart" style={{ display: 'none' }} />
      <EquityAreaChart
        series={series}
        height={200}
        yFormatter={v => '$' + Math.round(v).toLocaleString()}
      />
    </div>
  )
}

// ── Analytics panel ───────────────────────────────────────────────────────
function AnalyticsPanel({ accounts }: { accounts: PaperAccount[] }) {
  const analyticsQ = useQuery({
    queryKey: ['paper', 'analytics'],
    queryFn: getPaperAnalytics,
    staleTime: 60_000,
  })

  const data = analyticsQ.data
  if (!data) return null

  const byStrategy = data.by_strategy as Record<string, {
    win_rate?: number
    trades?: number
    total_pnl?: number
    max_drawdown_pct?: number
    sharpe?: number | null
    profit_factor?: number | null
  }>

  const metrics: StrategyMetric[] = accounts
    .map(a => {
      const s = byStrategy?.[a.strategy] ?? {}
      return {
        strategy: a.strategy,
        label: a.label,
        win_rate: s.win_rate ?? 0,
        trades: s.trades ?? 0,
        total_pnl: s.total_pnl ?? 0,
        max_drawdown_pct: s.max_drawdown_pct ?? 0,
        sharpe: s.sharpe ?? null,
        profit_factor: s.profit_factor ?? null,
      }
    })
    .filter(m => m.trades > 0)

  if (!metrics.length) return null

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--surface-rule)', borderRadius: 8, padding: 16 }}>
      <WinLossBar metrics={metrics} />
    </div>
  )
}
