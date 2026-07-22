import React, { forwardRef } from 'react'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a spinner, sets aria-busy, and blocks clicks. Keeps width stable. */
  loading?: boolean
  /** Optional text announced/shown while loading (defaults to keeping children). */
  loadingText?: string
  /** Stretch to the container width. */
  fullWidth?: boolean
  /** Icon rendered before the label (decorative — aria-hidden). */
  iconLeft?: React.ReactNode
  /** Icon rendered after the label (decorative — aria-hidden). */
  iconRight?: React.ReactNode
  children?: React.ReactNode
}

const variantClass: Record<ButtonVariant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  danger: 'btn-danger',
  ghost: 'btn-ghost',
}

/**
 * The single button primitive. Wraps the existing `.btn-*` token classes and
 * adds the states every real app needs: loading (spinner + aria-busy + click
 * guard), disabled, sizes, icons, full-width, and ref forwarding.
 *
 * Accessibility:
 *  - `type="button"` by default so a button inside a form never submits by
 *    accident (the #1 real-world button bug).
 *  - `aria-busy` while loading; `aria-disabled` mirrors the disabled state.
 *  - When loading, the label stays mounted (width doesn't collapse) and the
 *    spinner inherits text color via currentColor.
 *  - Icons are `aria-hidden` — the accessible name comes from the text. If a
 *    button is icon-only, pass `aria-label` via ...rest.
 *  - Focus ring is the global `:focus-visible` token — never removed.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    loadingText,
    fullWidth = false,
    iconLeft,
    iconRight,
    disabled,
    className = '',
    children,
    onClick,
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading
  const cls = [
    'ui-btn',
    variantClass[variant],
    `ui-btn-${size}`,
    fullWidth ? 'ui-btn-block' : '',
    loading ? 'is-loading' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      ref={ref}
      type={rest.type ?? 'button'}
      className={cls}
      disabled={isDisabled}
      aria-disabled={isDisabled || undefined}
      aria-busy={loading || undefined}
      onClick={loading ? undefined : onClick}
      {...rest}
    >
      {loading && <Spinner size={size === 'lg' ? 18 : size === 'sm' ? 13 : 15} />}
      {!loading && iconLeft ? <span className="ui-btn-icon" aria-hidden>{iconLeft}</span> : null}
      {(loading && loadingText) ? loadingText : children}
      {!loading && iconRight ? <span className="ui-btn-icon" aria-hidden>{iconRight}</span> : null}
    </button>
  )
})
