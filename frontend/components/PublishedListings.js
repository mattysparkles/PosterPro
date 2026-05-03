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
    <Card className="border-slate-200/80 bg-white/95 shadow-[0_18px_48px_rgba(15,23,42,0.06)]">
      <CardTitle className="text-xl tracking-tight">{title}</CardTitle>
      <CardDescription className="mt-2 leading-6 text-slate-600">
        Live marketplace records and recent publish activity summarized in a cleaner review surface.
      </CardDescription>

      {!posted.length ? (
        <div className="mt-6 rounded-[24px] border border-dashed border-slate-300 bg-slate-50 p-5">
          <p className="text-sm leading-6 text-slate-600">{emptyMessage}</p>
        </div>
      ) : (
        <div className="mt-6 grid gap-3 xl:grid-cols-2">
          {posted.map((listing) => {
            const ebayUrl =
              listing.marketplace_data?.ebay_url ||
              (listing.ebay_listing_id ? `https://www.ebay.com/itm/${listing.ebay_listing_id}` : '');
            const crosspostStatuses = (statusMap[listing.id] || []).filter((row) => row.marketplace !== 'ebay');

            return (
              <article key={listing.id} className="rounded-[24px] border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-950">{listing.title || `Listing #${listing.id}`}</p>
                    <p className="mt-1 text-sm text-slate-500">eBay ID: {listing.ebay_listing_id || 'Pending'}</p>
                  </div>
                  <StatusPill status={listing.ebay_publish_status || 'UNKNOWN'} />
                </div>

                {ebayUrl ? (
                  <a
                    href={ebayUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-sky-800"
                    title="Open this listing on eBay in a new tab."
                  >
                    Open live listing
                    <ExternalLink size={14} />
                  </a>
                ) : null}

                {!!crosspostStatuses.length && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {crosspostStatuses.map((row) => (
                      <StatusPill key={`${listing.id}-${row.marketplace}`} status={`${row.marketplace}: ${row.status}`} />
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </Card>
  );
}
