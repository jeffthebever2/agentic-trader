/**
 * UI primitives barrel — the single import surface for the design system.
 *
 *   import { Button, Card, Badge, AsyncBoundary, EmptyState } from '@/components/ui'
 *
 * Prefer these over hand-rolled inline styles. Every primitive reads from
 * tokens.css (color, radius, type, motion) and is dark-mode + reduced-motion
 * aware. If you find yourself writing a local `const card = {...}` style object,
 * reach for one of these instead — or extend one here.
 */
export { Button } from './Button'
export type { ButtonProps, ButtonVariant, ButtonSize } from './Button'
export { Spinner } from './Spinner'
export { EmptyState } from './EmptyState'
export type { EmptyStateProps } from './EmptyState'
export { AsyncBoundary } from './AsyncBoundary'
export type { AsyncBoundaryProps } from './AsyncBoundary'

export { Card } from './Card'
export { Badge } from './Badge'
export { Tabs } from './Tabs'
export { Modal } from './Modal'
export { Drawer } from './Drawer'
export { DataTable } from './DataTable'
export { AnimatedNumber } from './AnimatedNumber'
export { Skeleton, SkeletonText, SkeletonCard, SkeletonTable } from './Skeleton'
