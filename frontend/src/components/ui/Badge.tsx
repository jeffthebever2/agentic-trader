import React from 'react'

type Variant = 'default' | 'success' | 'danger' | 'warning' | 'info'

interface BadgeProps {
  variant?: Variant
  children: React.ReactNode
  className?: string
}

const variantClass: Record<Variant, string> = {
  default: 'badge-pending',
  success: 'badge-success',
  danger:  'badge-danger',
  warning: 'badge-warning',
  info:    'bg-blue-900/30 text-blue-400 border border-blue-800/40',
}

export function Badge({ variant = 'default', children, className = '' }: BadgeProps) {
  return (
    <span className={`badge ${variantClass[variant]} ${className}`}>
      {children}
    </span>
  )
}
