import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'

interface AllocationSlice {
  name: string
  value: number
  color: string
}

interface AllocationPieProps {
  slices: AllocationSlice[]
  height?: number
  title?: string
}

export function AllocationPie({ slices, height = 200, title }: AllocationPieProps) {
  const total = slices.reduce((s, sl) => s + sl.value, 0)

  return (
    <div style={{ width: '100%', position: 'relative' }}>
      {title && (
        <div style={{
          fontSize: 13,
          fontWeight: 600,
          color: 'var(--ink)',
          marginBottom: 8,
        }}>
          {title}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            innerRadius="55%"
            outerRadius="80%"
            paddingAngle={2}
            startAngle={90}
            endAngle={-270}
          >
            {slices.map((slice, i) => (
              <Cell key={i} fill={slice.color} />
            ))}
          </Pie>
          <Tooltip
            formatter={(v, name) => [
              typeof v === 'number' ? v.toFixed(1) + '%' : v,
              name,
            ]}
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--surface-rule)',
              fontSize: 12,
              borderRadius: 8,
            }}
          />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
        </PieChart>
      </ResponsiveContainer>
      {/* Center label */}
      <div style={{
        position: 'absolute',
        top: title ? 'calc(50% + 14px)' : '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        textAlign: 'center',
        pointerEvents: 'none',
      }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
          {total.toFixed(0)}
        </div>
        <div style={{ fontSize: 10, color: 'var(--ink-faint)' }}>total</div>
      </div>
    </div>
  )
}
