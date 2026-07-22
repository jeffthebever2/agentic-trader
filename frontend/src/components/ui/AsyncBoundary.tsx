import React from 'react'
import { SkeletonText } from './Skeleton'
import { EmptyState } from './EmptyState'
import { Button } from './Button'

export interface AsyncBoundaryProps<T> {
  /** The resolved data (undefined until first load). */
  data: T | undefined
  /** True during the initial fetch (react-query `isLoading`/`isPending`). */
  isLoading: boolean
  /** True when the fetch failed. */
  isError?: boolean
  /** The thrown error, used to derive a message. */
  error?: unknown
  /** Retry handler (react-query `refetch`). Enables the error-state retry button. */
  onRetry?: () => void
  /**
   * Decides whether resolved data counts as "empty". Default: null/undefined,
   * empty array, or empty object. Override for domain-specific emptiness.
   */
  isEmpty?: (data: T) => boolean
  /** Custom loading UI. Defaults to a 3-line skeleton. */
  loading?: React.ReactNode
  /** Custom empty UI. Defaults to a generic EmptyState. */
  empty?: React.ReactNode
  /** Title shown in the default empty state. */
  emptyTitle?: string
  /** Custom error renderer. Defaults to a message + retry button. */
  errorFallback?: (error: unknown, retry?: () => void) => React.ReactNode
  /** Render-prop: receives the non-empty data. */
  children: (data: T) => React.ReactNode
}

function defaultIsEmpty(data: unknown): boolean {
  if (data == null) return true
  if (Array.isArray(data)) return data.length === 0
  if (typeof data === 'object') return Object.keys(data as object).length === 0
  return false
}

function errorMessage(error: unknown): string {
  if (!error) return 'Something went wrong.'
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  // axios-style { response: { data: { detail } } }
  const anyErr = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyErr?.response?.data?.detail || anyErr?.message || 'Something went wrong.'
}

/**
 * One boundary for the four states every async view has: loading, error, empty,
 * and data. Replaces the copy-pasted `if (isLoading) return <Spinner/>; if
 * (!data.length) return <p>None</p>` blocks across the app with a single,
 * accessible, consistent contract.
 *
 * State precedence (handles background refetch gracefully):
 *   1. no data yet + loading      → loading skeleton
 *   2. no data yet + error        → error + retry
 *   3. data present but empty     → empty state
 *   4. otherwise                  → children(data)   ← stale data stays visible
 *                                    during a background refetch instead of
 *                                    flashing a skeleton.
 *
 * Usage:
 *   const q = useQuery({ queryKey: ['x'], queryFn })
 *   <AsyncBoundary data={q.data} isLoading={q.isLoading} isError={q.isError}
 *                  error={q.error} onRetry={q.refetch} emptyTitle="No items yet">
 *     {(items) => <List items={items} />}
 *   </AsyncBoundary>
 */
export function AsyncBoundary<T>({
  data,
  isLoading,
  isError = false,
  error,
  onRetry,
  isEmpty = defaultIsEmpty,
  loading,
  empty,
  emptyTitle = 'Nothing here yet',
  errorFallback,
  children,
}: AsyncBoundaryProps<T>) {
  const hasData = data !== undefined && data !== null

  if (!hasData && isLoading) {
    return <>{loading ?? <SkeletonText lines={3} />}</>
  }

  if (!hasData && isError) {
    if (errorFallback) return <>{errorFallback(error, onRetry)}</>
    return (
      <EmptyState
        icon="⚠️"
        title="Couldn't load this"
        description={errorMessage(error)}
        action={onRetry ? <Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button> : undefined}
      />
    )
  }

  if (hasData && isEmpty(data as T)) {
    return <>{empty ?? <EmptyState icon="∅" title={emptyTitle} />}</>
  }

  if (hasData) {
    return <>{children(data as T)}</>
  }

  // No data, not loading, not error (e.g. a disabled/idle query) — render nothing.
  return null
}
