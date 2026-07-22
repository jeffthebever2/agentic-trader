import React, { useEffect, useRef, useState } from 'react'
import { gsap } from 'gsap'

/**
 * AnimatedNumber — count-up primitive.
 *
 * The one thing CSS can't do: tween a numeric *value*. GSAP rolls the number
 * from its previous value to the new one and reuses the dormant `.val-up` /
 * `.val-down` flash keyframes (tokens.css) to signal direction. Everything
 * decorative (entrance, page-swap) stays in CSS — this is reserved for hero
 * figures where watching the number change *is* the job (equity, P&L, returns).
 *
 * Restraint rules baked in:
 *  - No tween when the value is unchanged (kills poll-jitter).
 *  - `prefers-reduced-motion` snaps instantly (GSAP bypasses the global CSS
 *    reduced-motion block, so it must be checked here in JS).
 *  - Non-finite values render straight through the formatter, no tween.
 */

const prefersReducedMotion = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

const defaultFormat = (n: number): string =>
  Math.round(n).toLocaleString('en-US')

export interface AnimatedNumberProps {
  value: number
  /** Formats the live tween value into display text (currency, %, plain…). */
  format?: (n: number) => string
  /** Tween length in seconds. Kept short — hero numbers, not marketing. */
  duration?: number
  /** Green/red background pulse on change via the shared flash keyframes. */
  flash?: boolean
  /** Count up from `mountFrom` on first paint (a one-time, tasteful reveal). */
  animateOnMount?: boolean
  mountFrom?: number
  className?: string
  style?: React.CSSProperties
  /** Accessible label announced to screen readers instead of the rolling digits. */
  ariaLabel?: string
}

export function AnimatedNumber({
  value,
  format = defaultFormat,
  duration = 0.7,
  flash = true,
  animateOnMount = true,
  mountFrom = 0,
  className = '',
  style,
  ariaLabel,
}: AnimatedNumberProps) {
  const elRef = useRef<HTMLSpanElement>(null)
  const tweenRef = useRef<gsap.core.Tween | null>(null)
  const prevRef = useRef<number>(animateOnMount ? mountFrom : value)
  const mountedRef = useRef(false)
  // Computed once from props (no ref read during render). After mount the effect
  // owns textContent; keeping the JSX child constant stops React from clobbering
  // the GSAP-driven digits on re-render.
  const [initialText] = useState(() => format(animateOnMount ? mountFrom : value))

  useEffect(() => {
    const el = elRef.current
    if (!el) return

    const from = prevRef.current
    const finite = Number.isFinite(value)
    const firstRun = !mountedRef.current
    mountedRef.current = true

    tweenRef.current?.kill()

    const settle = () => {
      el.textContent = format(value)
      prevRef.current = value
    }

    // Nothing to roll: identical to the last value (a poll that returned the
    // same number, or a mount with `animateOnMount={false}`). Set text, done.
    if (from === value || !finite || prefersReducedMotion() || duration <= 0) {
      settle()
      return
    }

    // Direction flash — remove + reflow + re-add so the keyframe re-fires even
    // when the direction repeats.
    if (flash && !firstRun && value !== from) {
      el.classList.remove('val-up', 'val-down')
      void el.offsetWidth
      el.classList.add(value > from ? 'val-up' : 'val-down')
    }

    const proxy = { v: from }
    tweenRef.current = gsap.to(proxy, {
      v: value,
      duration,
      ease: 'power3.out',
      onUpdate: () => {
        el.textContent = format(proxy.v)
      },
      onComplete: settle,
    })

    return () => {
      tweenRef.current?.kill()
    }
  }, [value, duration, flash, format])

  return (
    <span
      ref={elRef}
      className={className}
      style={style}
      aria-label={ariaLabel}
    >
      {initialText}
    </span>
  )
}
