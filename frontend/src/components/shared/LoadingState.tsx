export function LoadingState({ message = 'Loading…' }: { message?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 48, color: 'var(--ink-faint)', fontSize: 13 }}>
      {message}
    </div>
  )
}

export function ErrorState({ message = 'Failed to load data.' }: { message?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 48, color: 'var(--danger)', fontSize: 13 }}>
      {message}
    </div>
  )
}

export function EmptyState({ message = 'No data.' }: { message?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 48, color: 'var(--ink-faint)', fontSize: 13 }}>
      {message}
    </div>
  )
}
