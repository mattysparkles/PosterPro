export default function MarketplaceStatusPanel({ statuses }) {
  if (!statuses?.length) return <p className="text-sm text-[#667085]">No marketplace attempts yet.</p>;

  return (
    <div className="mt-4 overflow-x-auto rounded-[20px] border border-[#e5e7eb]">
      <table className="min-w-full text-sm">
        <thead className="bg-[#f8fafc] text-left text-xs uppercase text-[#667085]">
          <tr>
            <th className="px-3 py-2">Marketplace</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Listing</th>
            <th className="px-3 py-2">Error</th>
          </tr>
        </thead>
        <tbody>
          {statuses.map((row) => (
            <tr key={`${row.marketplace}-${row.marketplace_listing_id || 'pending'}`} className="border-t border-[#e5e7eb]">
              <td className="px-3 py-2 text-[#111827]">{row.marketplace}</td>
              <td className="px-3 py-2 text-[#667085]">{row.status}</td>
              <td className="px-3 py-2">
                {row.marketplace_listing_id ? (
                  <a href={row.raw_response?.ebay_url || '#'} target="_blank" rel="noreferrer" className="font-semibold text-[#2563eb] underline">
                    {row.marketplace_listing_id}
                  </a>
                ) : (
                  '-'
                )}
              </td>
              <td className="px-3 py-2 text-[#667085]">{row.raw_response?.error || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
