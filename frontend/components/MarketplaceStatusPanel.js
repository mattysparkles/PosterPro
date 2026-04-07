export default function MarketplaceStatusPanel({ statuses }) {
  if (!statuses?.length) return <p className="text-sm text-muted-foreground">No marketplace attempts yet.</p>;

  return (
    <div className="mt-4 overflow-x-auto rounded-2xl border border-border/70">
      <table className="min-w-full text-sm">
        <thead className="bg-muted/60 text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Marketplace</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Listing</th>
            <th className="px-3 py-2">Error</th>
          </tr>
        </thead>
        <tbody>
          {statuses.map((row) => (
            <tr key={`${row.marketplace}-${row.marketplace_listing_id || 'pending'}`} className="border-t border-border/70">
              <td className="px-3 py-2">{row.marketplace}</td>
              <td className="px-3 py-2">{row.status}</td>
              <td className="px-3 py-2">
                {row.marketplace_listing_id ? (
                  <a href={row.raw_response?.ebay_url || '#'} target="_blank" rel="noreferrer" className="text-primary underline">
                    {row.marketplace_listing_id}
                  </a>
                ) : (
                  '-'
                )}
              </td>
              <td className="px-3 py-2">{row.raw_response?.error || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
