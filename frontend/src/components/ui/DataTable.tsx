import { useState } from 'react'

export interface ColDef<T> {
  key: string
  label: string
  sortable?: boolean
  align?: 'left' | 'right' | 'center'
  width?: string | number
  render?: (value: unknown, row: T) => React.ReactNode
}

export interface DataTableProps<T extends Record<string, unknown>> {
  data: T[]
  columns: ColDef<T>[]
  defaultSortKey?: string
  defaultSortDir?: 'asc' | 'desc'
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
  emptyMessage?: string
  maxHeight?: string | number
}

const tblHead = (align: 'left' | 'right' | 'center' = 'left', sortable?: boolean): React.CSSProperties => ({
  fontSize: 11,
  fontWeight: 700,
  color: 'var(--ink-faint)',
  textTransform: 'uppercase',
  letterSpacing: '.04em',
  padding: '8px 12px',
  borderBottom: '1px solid var(--surface-rule)',
  textAlign: align,
  whiteSpace: 'nowrap',
  background: 'var(--canvas)',
  position: 'sticky',
  top: 0,
  zIndex: 1,
  cursor: sortable ? 'pointer' : 'default',
  userSelect: 'none',
})

const tblCell = (align: 'left' | 'right' | 'center' = 'left'): React.CSSProperties => ({
  fontSize: 12,
  color: 'var(--ink)',
  padding: '8px 12px',
  borderBottom: '1px solid var(--surface-rule)',
  fontFamily: 'var(--font-mono)',
  whiteSpace: 'nowrap',
  textAlign: align,
})

function sortData<T extends Record<string, unknown>>(
  data: T[],
  key: string,
  dir: 'asc' | 'desc'
): T[] {
  return [...data].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    const cmp = (typeof av === 'number' && typeof bv === 'number')
      ? av - bv
      : String(av ?? '').toLowerCase().localeCompare(String(bv ?? '').toLowerCase())
    return dir === 'asc' ? cmp : -cmp
  })
}

export function DataTable<T extends Record<string, unknown>>({
  data,
  columns,
  defaultSortKey,
  defaultSortDir = 'asc',
  rowKey,
  onRowClick,
  emptyMessage = 'No data.',
  maxHeight,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string>(defaultSortKey ?? '')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(defaultSortDir)
  const [hoveredKey, setHoveredKey] = useState<string | number | null>(null)

  function handleHeaderClick(col: ColDef<T>) {
    if (!col.sortable) return
    if (sortKey === col.key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(col.key)
      setSortDir('asc')
    }
  }

  const sorted = sortKey ? sortData(data, sortKey, sortDir) : data

  const tableEl = (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr>
          {columns.map(col => (
            <th
              key={col.key}
              style={{
                ...tblHead(col.align, col.sortable),
                width: col.width,
              }}
              onClick={() => handleHeaderClick(col)}
            >
              {col.label}
              {col.sortable && sortKey === col.key && (
                <span style={{ marginLeft: 4 }}>{sortDir === 'asc' ? '▲' : '▼'}</span>
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.length === 0 ? (
          <tr>
            <td
              colSpan={columns.length}
              style={{ padding: '24px 12px', textAlign: 'center', fontSize: 13, color: 'var(--ink-faint)' }}
            >
              {emptyMessage}
            </td>
          </tr>
        ) : (
          sorted.map(row => {
            const rk = rowKey(row)
            return (
              <tr
                key={rk}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={{
                  background: hoveredKey === rk ? 'var(--canvas)' : 'transparent',
                  cursor: onRowClick ? 'pointer' : 'default',
                  transition: 'background .1s',
                }}
                onMouseEnter={() => setHoveredKey(rk)}
                onMouseLeave={() => setHoveredKey(null)}
              >
                {columns.map(col => {
                  const value = row[col.key]
                  return (
                    <td key={col.key} style={{ ...tblCell(col.align), width: col.width }}>
                      {col.render ? col.render(value, row) : (value as React.ReactNode)}
                    </td>
                  )
                })}
              </tr>
            )
          })
        )}
      </tbody>
    </table>
  )

  if (maxHeight) {
    return (
      <div style={{ maxHeight, overflowY: 'auto', position: 'relative' }}>
        {tableEl}
      </div>
    )
  }

  return tableEl
}
