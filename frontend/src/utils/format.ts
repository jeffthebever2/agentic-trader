/** Shared formatting and color helpers — import from here, not per-page. */

export function fmtDollar(n: number, decimals = 0): string {
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })
}

export function fmtPnl(n: number | null | undefined): string {
  if (n == null) return '—'
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function fmtNum(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

export function fmtPct(n: number | null | undefined, decimals = 1): string {
  if (n == null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

export function pnlColor(n: number | null | undefined): string {
  if (n == null) return 'var(--ink)'
  return n >= 0 ? '#4ade80' : '#f87171'
}

export function winRateColor(wr: number | null): string {
  if (wr == null) return '#6b7280'
  if (wr >= 0.65) return '#4ade80'
  if (wr >= 0.5) return '#facc15'
  return '#f87171'
}

export function timeAgo(published: string): string {
  try {
    const diff = Date.now() - new Date(published).getTime()
    const h = Math.floor(diff / 3_600_000)
    const m = Math.floor(diff / 60_000)
    if (h >= 24) return `${Math.floor(h / 24)}d ago`
    if (h >= 1) return `${h}h ago`
    return `${m}m ago`
  } catch { return '' }
}

export function fmtVolume(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}
