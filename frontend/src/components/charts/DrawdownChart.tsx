import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts'

interface DrawdownChartProps {
  equityData: Array<{ x: string; y: number }>
  startingCash: number
  height?: number
}

export function DrawdownChart({ equityData, startingCash, height = 120 }: DrawdownChartProps) {
  let peak = startingCash
  const drawdownData = equityData.map(pt => {
    peak = Math.max(peak, pt.y)
    const dd = -((peak - pt.y) / startingCash * 100)
    return { x: pt.x, dd }
  })

  return (
    <div style={{ width: '100%' }}>
      <div style={{
        fontSize: 10,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.08em',
        color: 'var(--ink-faint)',
        marginBottom: 6,
      }}>
        Max Drawdown
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={drawdownData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.06)" />
          <XAxis
            dataKey="x"
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tickFormatter={v => (v as number).toFixed(1) + '%'}
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
            width={50}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--surface-rule)',
              fontSize: 12,
              borderRadius: 8,
            }}
            formatter={(v) => [(v as number).toFixed(2) + '%', 'Drawdown']}
          />
          <ReferenceLine y={0} stroke="var(--surface-rule)" />
          <Area
            type="monotone"
            dataKey="dd"
            stroke="#f87171"
            fill="#f87171"
            fillOpacity={0.25}
            strokeWidth={1.5}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
