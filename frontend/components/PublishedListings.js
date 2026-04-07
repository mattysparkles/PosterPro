export default function PublishedListings({
  listings,
  title = 'Published eBay Listings',
  emptyMessage = 'No published listings yet.',
  postedOnly = true,
}) {
  const posted = postedOnly
    ? listings.filter((listing) => listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id)
    : listings;

  return (
    <section className="card">
      <h2>{title}</h2>
      {!posted.length && <p>{emptyMessage}</p>}
      {posted.map((listing) => {
        const ebayUrl = listing.marketplace_data?.ebay_url || (listing.ebay_listing_id ? `https://www.ebay.com/itm/${listing.ebay_listing_id}` : '');
        const autoRelistHistory = listing.marketplace_data?.auto_relist_history || [];
        return (
          <article key={listing.id} className="listing-item">
            <strong>{listing.title || `Listing #${listing.id}`}</strong>
            <div>eBay Listing ID: {listing.ebay_listing_id}</div>
            <div>Status: {listing.ebay_publish_status || 'UNKNOWN'}</div>
            <div>Start Price: {listing.start_price ? `$${listing.start_price.toFixed(2)}` : 'N/A'}</div>
            <div>Buy It Now: {listing.buy_it_now_price ? `$${listing.buy_it_now_price.toFixed(2)}` : 'N/A'}</div>
            <div>Min Offer: {listing.min_acceptable_offer ? `$${listing.min_acceptable_offer.toFixed(2)}` : 'N/A'}</div>
            {ebayUrl && (
              <a href={ebayUrl} target="_blank" rel="noreferrer">
                Open on eBay
              </a>
            )}
            {autoRelistHistory.length > 0 && (
              <div>
                <strong>Auto-Relisted</strong>
                <ul>
                  {autoRelistHistory.map((event, index) => (
                    <li key={`${listing.id}-${event.timestamp || index}`}>
                      {event.timestamp || 'Unknown time'} → {event.new_listing_id || 'N/A'}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
