import { ExternalLink } from 'lucide-react';

import StatusPill from './StatusPill';
import { Card, CardDescription, CardTitle } from './ui/card';

export default function PublishedListings({
  listings,
  statusMap = {},
  title = 'Published Listings',
  emptyMessage = 'No published listings yet.',
  postedOnly = true,
}) {
  const posted = postedOnly
    ? listings.filter((listing) => listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id)
    : listings;

  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <CardDescription className="mb-4">Listings that are live or recently published across channels.</CardDescription>
      {!posted.length && <p className="text-sm text-muted-foreground">{emptyMessage}</p>}
      <div className="grid gap-3 lg:grid-cols-2">
        {posted.map((listing) => {
          const ebayUrl = listing.marketplace_data?.ebay_url || (listing.ebay_listing_id ? `https://www.ebay.com/itm/${listing.ebay_listing_id}` : '');
          const crosspostStatuses = (statusMap[listing.id] || []).filter((row) => row.marketplace !== 'ebay');
          return (
            <article key={listing.id} className="rounded-2xl border border-border/70 bg-background p-4">
              <div className="mb-2 flex items-start justify-between gap-3">
                <strong>{listing.title || `Listing #${listing.id}`}</strong>
                <StatusPill status={listing.ebay_publish_status || 'UNKNOWN'} />
              </div>
              <p className="text-sm text-muted-foreground">eBay ID: {listing.ebay_listing_id || 'Pending'}</p>
              {ebayUrl && (
                <a href={ebayUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-sm text-primary underline" title="Open this listing on eBay in a new tab.">
                  Open on eBay <ExternalLink size={14} />
                </a>
              )}
              {!!crosspostStatuses.length && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {crosspostStatuses.map((row) => (
                    <StatusPill key={`${listing.id}-${row.marketplace}`} status={`${row.marketplace}: ${row.status}`} />
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </Card>
  );
}
