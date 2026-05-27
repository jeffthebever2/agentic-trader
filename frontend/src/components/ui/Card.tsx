import React from 'react'

interface CardProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
  style?: React.CSSProperties
}

export function Card({ children, className = '', onClick, style }: CardProps) {
  return (
    <div
      className={`card ${className}`}
      onClick={onClick}
      style={style}
    >
      {children}
    </div>
  )
}
