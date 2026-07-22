import React from 'react'

interface SpinnerProps {
  /** Diameter in px. Defaults to 16 (inline-with-text size). */
  size?: number
  /** Stroke color. Defaults to currentColor so it inherits the parent's text color. */
  color?: string
  /** Accessible label for screen readers. Hidden visually. */
  label?: string
  className?: string
  style?: React.CSSProperties
}

/**
 * Indeterminate loading spinner. Respects `prefers-reduced-motion` (the keyframe
 * is disabled via CSS there — see tokens.css `.ui-spinner`). Uses `currentColor`
 * so it tints to whatever context it sits in (button text, muted panel, etc.).
 *
 * a11y: `role="status"` + visually-hidden label so assistive tech announces
 * "Loading" without duplicating a visible label.
 */
export function Spinner({ size = 16, color = 'currentColor', label = 'Loading', className = '', style }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={`ui-spinner ${className}`}
      style={{
        width: size,
        height: size,
        borderWidth: Math.max(1.5, size / 8),
        borderColor: color,
        borderTopColor: 'transparent',
        ...style,
      }}
    >
      <span className="ui-visually-hidden">{label}</span>
    </span>
  )
}
