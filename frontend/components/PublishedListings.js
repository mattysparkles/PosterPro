export default function PublishedListings({ listings }) {
  const posted = listings.filter((listing) => listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id);

  return (
    <section className="card">
      <h2>Published eBay Listings</h2>
      {!posted.length && <p>No published listings yet.</p>}
      {posted.map((listing) => {
        const ebayUrl = listing.marketplace_data?.ebay_url || (listing.ebay_listing_id ? `https://www.ebay.com/itm/${listing.ebay_listing_id}` : '');
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
          </article>
        );
      })}
    </section>
  );
}
