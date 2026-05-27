import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'

interface EquitySeries {
  label: string
  color: string
  data: Array<{ x: string; y: number }>
}

interface EquityAreaChartProps {
  series: EquitySeries[]
  height?: number
  yFormatter?: (v: number) => string
}

export function EquityAreaChart({ series, height = 220, yFormatter }: EquityAreaChartProps) {
  // Combine all series into unified time array (outer join by x)
  const allKeys = new Set<string>()
  series.forEach(s => s.data.forEach(pt => allKeys.add(pt.x)))
  const sortedKeys = Array.from(allKeys).sort()

  const combined = sortedKeys.map(x => {
    const row: Record<string, string | number> = { x }
    series.forEach(s => {
      const pt = s.data.find(d => d.x === x)
      if (pt != null) row[s.label] = pt.y
    })
    return row
  })

  return (
    <div style={{ width: '100%' }}>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={combined} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.06)" />
          <XAxis
            dataKey="x"
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tickFormatter={yFormatter}
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
            width={65}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--surface-rule)',
              fontSize: 12,
              borderRadius: 8,
            }}
            labelStyle={{ color: 'var(--ink-faint)' }}
            formatter={(v, name) => [
              yFormatter ? yFormatter(v as number) : v,
              name,
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map(s => (
            <Area
              key={s.label}
              type="monotone"
              dataKey={s.label}
              stroke={s.color}
              fill={s.color}
              fillOpacity={0.12}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
