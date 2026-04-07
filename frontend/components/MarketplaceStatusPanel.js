export default function MarketplaceStatusPanel({ statuses }) {
  if (!statuses?.length) return <p>No marketplace attempts yet.</p>;

  return (
    <table className="status-table">
      <thead>
        <tr>
          <th>Marketplace</th>
          <th>Status</th>
          <th>Listing</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {statuses.map((row) => (
          <tr key={`${row.marketplace}-${row.marketplace_listing_id || 'pending'}`}>
            <td>{row.marketplace}</td>
            <td>{row.status}</td>
            <td>
              {row.marketplace_listing_id ? (
                <a href={row.raw_response?.ebay_url || '#'} target="_blank" rel="noreferrer">
                  {row.marketplace_listing_id}
                </a>
              ) : (
                '-'
              )}
            </td>
            <td>{row.raw_response?.error || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
