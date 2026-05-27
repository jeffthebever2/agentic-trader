import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

interface FeatureImportanceBarProps {
  features: Array<{ feature: string; importance: number }>
  topN?: number
}

export function FeatureImportanceBar({ features, topN = 15 }: FeatureImportanceBarProps) {
  const sorted = [...features]
    .sort((a, b) => b.importance - a.importance)
    .slice(0, topN)

  return (
    <div style={{ width: '100%' }}>
      <ResponsiveContainer width="100%" height={topN * 28 + 40}>
        <BarChart
          data={sorted}
          layout="vertical"
          margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
        >
          <XAxis
            type="number"
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={v => (v as number).toFixed(3)}
          />
          <YAxis
            type="category"
            dataKey="feature"
            width={140}
            tick={{ fontSize: 10, fill: 'var(--ink-faint)' }}
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
            formatter={v => [(v as number).toFixed(4), 'Importance']}
          />
          <Bar
            dataKey="importance"
            fill="var(--accent)"
            radius={[0, 4, 4, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
