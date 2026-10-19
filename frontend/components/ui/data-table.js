import { cn } from '../../lib/utils';

export default function DataTable({
  columns,
  rows,
  emptyState,
  className,
  rowKey = (row, index) => index,
  selectedRows = [],
  onToggleRow,
  onToggleAll,
  allSelected = false,
  getRowClassName,
  onRowClick,
  stickyHeader = false,
  density = 'default',
}) {
  const headerClass = stickyHeader ? 'sticky top-0 z-10 bg-[#f9fafb]' : 'bg-[#f9fafb]';
  const rowPadding = density === 'compact' ? 'px-4 py-2.5' : 'px-4 py-3.5';

  return (
    <div className={cn('overflow-hidden rounded-2xl border border-[#e4e7ec] bg-white', className)}>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className={headerClass}>
            <tr>
              {onToggleRow ? (
                <th className="w-12 px-4 py-3">
                  <input
                    aria-label="Select all rows"
                    type="checkbox"
                    checked={allSelected}
                    onChange={onToggleAll}
                    className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                  />
                </th>
              ) : null}
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={cn('px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]', column.headerClassName)}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row, index) => {
                const key = rowKey(row, index);
                const selected = selectedRows.includes(key);
                return (
                  <tr
                    key={key}
                    className={cn(
                      'border-t border-[#f2f4f7] transition hover:bg-[#f9fafb]',
                      selected ? 'bg-[#f5f8ff]' : '',
                      onRowClick ? 'cursor-pointer' : '',
                      getRowClassName?.(row, selected),
                    )}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                  >
                    {onToggleRow ? (
                      <td
                        className={rowPadding}
                        onClick={(event) => {
                          event.stopPropagation();
                        }}
                      >
                        <input
                          aria-label={`Select row ${index + 1}`}
                          type="checkbox"
                          checked={selected}
                          onChange={() => onToggleRow(key)}
                          className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                        />
                      </td>
                    ) : null}
                    {columns.map((column) => (
                      <td key={column.key} className={cn(rowPadding, 'align-middle text-[#101828]', column.cellClassName)}>
                        {column.render ? column.render(row) : row[column.key]}
                      </td>
                    ))}
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={columns.length + (onToggleRow ? 1 : 0)} className="px-4 py-14 text-center text-sm text-[#667085]">
                  {emptyState}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
