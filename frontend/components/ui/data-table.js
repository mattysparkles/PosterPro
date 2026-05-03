export default function DataTable({ columns, rows, emptyState, rowKey = (row, index) => index, selectedRows = [], onToggleRow }) {
  return (
    <div className="overflow-x-auto rounded-[16px] border border-[#e5e7eb] bg-white">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-[#f8fafc]">
          <tr>
            {onToggleRow ? <th className="px-4 py-3" /> : null}
            {columns.map((column) => (
              <th key={column.key} className="px-4 py-3 font-semibold text-[#667085]">
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
                <tr key={key} className="border-t border-[#e5e7eb]">
                  {onToggleRow ? (
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => onToggleRow(key)}
                        className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                      />
                    </td>
                  ) : null}
                  {columns.map((column) => (
                    <td key={column.key} className="px-4 py-3 align-top text-[#111827]">
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
  );
}
