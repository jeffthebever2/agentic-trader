import type { ReactNode } from 'react'

interface MetricCardProps {
  title: string
  value: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  accent?: 'green' | 'yellow' | 'red' | 'blue' | 'gray'
}

const accentRing: Record<string, string> = {
  green: 'ring-green-500/30',
  yellow: 'ring-yellow-500/30',
  red: 'ring-red-500/30',
  blue: 'ring-blue-500/30',
  gray: 'ring-gray-600/30',
}

const accentDot: Record<string, string> = {
  green: 'bg-green-400',
  yellow: 'bg-yellow-400',
  red: 'bg-red-400',
  blue: 'bg-blue-400',
  gray: 'bg-gray-400',
}

export function MetricCard({ title, value, sub, icon, accent = 'gray' }: MetricCardProps) {
  return (
    <div className={`rounded-xl bg-gray-900 p-5 ring-1 ${accentRing[accent]} flex flex-col gap-2`}>
      <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
        {icon}
        <span>{title}</span>
        <span className={`ml-auto h-2 w-2 rounded-full ${accentDot[accent]}`} />
      </div>
      <div className="text-2xl font-bold text-white leading-tight">{value}</div>
      {sub && <div className="text-xs text-gray-500">{sub}</div>}
    </div>
  )
}
