import { useState, useRef, useMemo, useEffect } from 'react'
import { Download, Search } from 'lucide-react'
import { downloadCsv } from '../../lib/csv'

const ROW_HEIGHT = 38

export default function VirtualTable({ rows, columns, defaultSort, onRowClick, renderCellValue }) {
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState(defaultSort || columns[0])
  const [sortDesc, setSortDesc] = useState(true)
  const containerRef = useRef(null)
  const [scrollTop, setScrollTop] = useState(0)

  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const query = search.toLowerCase()
    return rows.filter(row =>
      columns.some(col => {
        const val = row[col]
        const str = val === null || val === undefined ? '' : String(val).toLowerCase()
        return str.includes(query)
      })
    )
  }, [rows, search, columns])

  const sorted = useMemo(() => {
    if (!sortBy || !filtered) return filtered
    const copy = [...filtered]
    copy.sort((a, b) => {
      const aVal = a[sortBy]
      const bVal = b[sortBy]
      let cmp = 0

      if (aVal === null || aVal === undefined) cmp = 1
      else if (bVal === null || bVal === undefined) cmp = -1
      else if (typeof aVal === 'number' && typeof bVal === 'number') cmp = aVal - bVal
      else cmp = String(aVal).localeCompare(String(bVal))

      return sortDesc ? -cmp : cmp
    })
    return copy
  }, [filtered, sortBy, sortDesc])

  const visibleStart = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 5)
  const visibleEnd = Math.min(sorted.length, visibleStart + Math.ceil(window.innerHeight / ROW_HEIGHT) + 10)
  const visible = sorted.slice(visibleStart, visibleEnd)

  const handleScroll = e => {
    setScrollTop(e.currentTarget.scrollTop)
  }

  const handleSort = col => {
    if (sortBy === col) {
      setSortDesc(!sortDesc)
    } else {
      setSortBy(col)
      setSortDesc(true)
    }
  }

  const handleDownload = () => {
    downloadCsv('ontology-table.csv', sorted, columns)
  }

  return (
    <div className="virtual-table-container">
      <div className="virtual-table-toolbar">
        <label className="virtual-table-search">
          <Search size={15} />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search..."
          />
        </label>
        <button type="button" className="virtual-table-export" onClick={handleDownload}>
          <Download size={14} />Export CSV
        </button>
        <span className="virtual-table-count">{sorted.length} rows</span>
      </div>

      <div className="virtual-table-wrapper" ref={containerRef} onScroll={handleScroll}>
        <table className="virtual-table">
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col} onClick={() => handleSort(col)}>
                  {col}
                  {sortBy === col && <span className="sort-indicator">{sortDesc ? ' ▼' : ' ▲'}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr style={{ height: visibleStart * ROW_HEIGHT }} />
            {visible.map((row, idx) => (
              <tr
                key={visibleStart + idx}
                onClick={() => onRowClick?.(row)}
                style={{ cursor: onRowClick ? 'pointer' : 'default' }}
              >
                {columns.map(col => (
                  <td key={col}>
                    {renderCellValue ? renderCellValue(row, col) : row[col] === null ? '—' : String(row[col])}
                  </td>
                ))}
              </tr>
            ))}
            <tr style={{ height: Math.max(0, (sorted.length - visibleEnd) * ROW_HEIGHT) }} />
          </tbody>
        </table>
      </div>
    </div>
  )
}
