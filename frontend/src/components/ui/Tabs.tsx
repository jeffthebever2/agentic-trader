
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
    <div className={`flex gap-1 flex-wrap ${className}`}>
      {visible.map(tab => (
        <button
          key={tab.id}
          className="report-tab"
          style={{
            paddingBottom: 12,
            color: active === tab.id ? 'var(--ink)' : 'var(--ink-faint)',
            background: 'none',
            border: 'none',
            borderBottom: active === tab.id ? '2px solid var(--accent)' : '2px solid transparent',
            fontWeight: active === tab.id ? 600 : 400,
            fontSize: 13,
            cursor: 'pointer',
            padding: '4px 10px 10px',
            transition: 'color .15s, border-color .15s',
          }}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
