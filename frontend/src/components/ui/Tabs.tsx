
interface Tab {
  id: string
  label: string
  adminOnly?: boolean
}

interface TabsProps {
  tabs: Tab[]
  active: string
  onChange: (id: string) => void
  isAdmin?: boolean
  className?: string
}

export function Tabs({ tabs, active, onChange, isAdmin = false, className = '' }: TabsProps) {
  const visible = tabs.filter(t => !t.adminOnly || isAdmin)
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }} className={className}>
      {visible.map(tab => (
        <button
          key={tab.id}
          className={`report-tab${active === tab.id ? ' active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
