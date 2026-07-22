import React from 'react'

export interface EmptyStateProps {
  /** Decorative glyph/emoji or icon node. aria-hidden. */
  icon?: React.ReactNode
  /** Short headline — what's empty and why. */
  title: string
  /** Optional supporting sentence guiding the next action. */
  description?: React.ReactNode
  /** Optional CTA (usually a <Button>). */
  action?: React.ReactNode
  /** Compact variant for small panels/cards. */
  compact?: boolean
  className?: string
}

/**
 * The one empty-state primitive — replaces the ad-hoc "No X yet" <p> blocks
 * scattered across pages. Centered, responsive, and semantically a `status`
 * region so screen readers announce the empty condition rather than silence.
 *
 * Use for: zero search results, an unconfigured feature, a drained queue.
 * Do NOT use for errors (use AsyncBoundary's error slot) — empty ≠ failed.
 */
export function EmptyState({ icon, title, description, action, compact = false, className = '' }: EmptyStateProps) {
  return (
    <div
      role="status"
      className={`ui-empty${compact ? ' ui-empty-compact' : ''} ${className}`}
    >
      {icon != null && <div className="ui-empty-icon" aria-hidden>{icon}</div>}
      <div className="ui-empty-title">{title}</div>
      {description != null && <div className="ui-empty-desc">{description}</div>}
      {action != null && <div className="ui-empty-action">{action}</div>}
    </div>
  )
}
