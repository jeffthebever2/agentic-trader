// Skeleton shimmer components
// Animation CSS is injected once via a module-level flag

let styleInjected = false

function ensureStyle() {
  if (styleInjected) return
  styleInjected = true
  // CSS is handled via tokens.css .skeleton-pulse class
}

interface SkeletonProps {
  width?: string | number
  height?: string | number
  borderRadius?: string | number
  className?: string
}

export function Skeleton({ width, height = 16, borderRadius = 4, className }: SkeletonProps) {
  ensureStyle()
  return (
    <div
      className={`skeleton-pulse${className ? ` ${className}` : ''}`}
      style={{
        width: width ?? '100%',
        height,
        borderRadius,
        display: 'block',
      }}
    />
  )
}

interface SkeletonTextProps {
  lines?: number
}

export function SkeletonText({ lines = 3 }: SkeletonTextProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={14}
          width={i === lines - 1 && lines > 1 ? '66%' : '100%'}
        />
      ))}
    </div>
  )
}

export function SkeletonCard() {
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--surface-rule)',
        borderRadius: 8,
        padding: '14px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <Skeleton height={11} width="55%" />
      <Skeleton height={28} width="70%" borderRadius={4} />
    </div>
  )
}

interface SkeletonTableProps {
  rows?: number
  cols?: number
}

export function SkeletonTable({ rows = 5, cols = 4 }: SkeletonTableProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Header row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 12,
          padding: '10px 16px',
          borderBottom: '1px solid var(--surface-rule)',
          background: 'var(--canvas)',
        }}
      >
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} height={11} width="60%" />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div
          key={r}
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${cols}, 1fr)`,
            gap: 12,
            padding: '10px 16px',
            borderBottom: '1px solid var(--surface-rule)',
          }}
        >
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} height={13} width={c === 0 ? '50%' : '80%'} />
          ))}
        </div>
      ))}
    </div>
  )
}
