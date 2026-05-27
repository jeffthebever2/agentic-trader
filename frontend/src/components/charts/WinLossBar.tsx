import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

export interface StrategyMetric {
  strategy: string
  label: string
  win_rate: number
  trades: number
  total_pnl: number
  max_drawdown_pct: number
  sharpe: number | null
  profit_factor: number | null
}

interface WinLossBarProps {
  metrics: StrategyMetric[]
}

function fmt$(n: number) {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

export function WinLossBar({ metrics }: WinLossBarProps) {
  const data = metrics.map(m => ({
    ...m,
    winPct: +(m.win_rate * 100).toFixed(1),
    lossPct: +(100 - m.win_rate * 100).toFixed(1),
  }))

  return (
    <div style={{ width: '100%' }}>
      <div style={{
        fontSize: 13,
        fontWeight: 600,
        color: 'var(--ink)',
        marginBottom: 10,
      }}>
        Win Rate by Strategy
      </div>
      <ResponsiveContainer width="100%" height={metrics.length * 44 + 40}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
        >
          <XAxis
            type="number"
            domain={[0, 100]}
            tickFormatter={v => v + '%'}
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={110}
            tick={{ fontSize: 11, fill: 'var(--ink-muted)' }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--surface-rule)',
              fontSize: 12,
              borderRadius: 8,
            }}
            formatter={(v, name, props) => {
              const m = props.payload as StrategyMetric & { winPct: number; lossPct: number }
              if (name === 'winPct') {
                return [
                  [
                    `Win Rate: ${m.winPct.toFixed(1)}%`,
                    `Trades: ${m.trades}`,
                    `P&L: ${fmt$(m.total_pnl)}`,
                    m.sharpe != null ? `Sharpe: ${m.sharpe.toFixed(2)}` : null,
                  ]
                    .filter(Boolean)
                    .join(' | '),
                  'Stats',
                ]
              }
              return [v + '%', name === 'winPct' ? 'Win' : 'Loss']
            }}
          />
          <Bar dataKey="winPct" stackId="a" fill="#4ade80" radius={[0, 0, 0, 0]} />
          <Bar dataKey="lossPct" stackId="a" fill="#f8717166" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
