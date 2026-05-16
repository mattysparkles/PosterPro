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
}) {
  return (
    <div className={cn('overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-white', className)}>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[#f9fafb]">
            <tr>
              {onToggleRow ? (
                <th className="w-12 px-4 py-3">
                  <input
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
                  className={cn(
                    'px-4 py-3 text-[12px] font-semibold uppercase tracking-[0.08em] text-[#667085]',
                    column.headerClassName,
                  )}
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
                      'h-14 border-t border-[#e5e7eb] hover:bg-[#f9fafb]',
                      onRowClick ? 'cursor-pointer' : '',
                      getRowClassName?.(row, selected),
                    )}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                  >
                    {onToggleRow ? (
                      <td
                        className="px-4 py-3"
                        onClick={(event) => {
                          event.stopPropagation();
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => onToggleRow(key)}
                          className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                        />
                      </td>
                    ) : null}
                    {columns.map((column) => (
                      <td key={column.key} className={cn('px-4 py-3 align-middle text-[#101828]', column.cellClassName)}>
                        {column.render ? column.render(row) : row[column.key]}
                      </td>
                    ))}
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={columns.length + (onToggleRow ? 1 : 0)} className="px-4 py-12 text-center text-sm text-[#667085]">
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
