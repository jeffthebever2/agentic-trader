export function LoadingState({ message = 'Loading…' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center p-12"
         style={{ color: 'var(--ink-faint)', fontSize: 13 }}>
      {message}
    </div>
  )
}

export function ErrorState({ message = 'Failed to load data.' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center p-12"
         style={{ color: 'var(--danger)', fontSize: 13 }}>
      {message}
    </div>
  )
}

export function EmptyState({ message = 'No data.' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center p-12"
         style={{ color: 'var(--ink-faint)', fontSize: 13 }}>
      {message}
    </div>
  )
}
