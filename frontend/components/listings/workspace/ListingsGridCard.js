import Button from '../../ui/button';
import StatusPill from '../../ui/status-pill';
import ListingsMarketplacePreflightBadges from './ListingsMarketplacePreflightBadges';
import ListingsThumbnail from './ListingsThumbnail';

export default function ListingsGridCard({
  listing,
  selected,
  setSelectedListingId,
  toggleRow,
  getListingThumbnail,
  getListingTitle,
  getReadinessSummary,
  getListingImageCount,
  getMarketplacePreflightSummary,
  getMarketplacePreflightStatus,
  getMarketplacePreflightTone,
  getMarketplaceTopBlocker,
  getMarketplacePreflightAgeLabel,
  getListingBucket,
  getListingMarketplaces,
  enabledPlatforms,
  formatMarketplace,
  workflowPreferences,
  publishSingleListing,
  publishing,
  approveListing,
  isAmazonVineSource,
  isArchivedListing,
  archiveListing,
  deleteListing,
  confirmDeleteListings,
  router,
  errors,
  getListingFailureMessage,
  getListingPrice,
}) {
  const bucket = getListingBucket(listing);
  const explicitMarketplaces = getListingMarketplaces(listing, enabledPlatforms, { allowFallback: false });
  const canPublish = workflowPreferences.review_before_publish ? bucket === 'ready' : bucket === 'drafts' || bucket === 'ready';

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => setSelectedListingId(listing.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          setSelectedListingId(listing.id);
        }
      }}
      className={`rounded-[16px] border bg-white p-4 text-left transition hover:border-[#bfd2ff] hover:bg-[#f8fbff] ${selected ? 'border-[#bfd2ff] ring-2 ring-[#dbe7ff]' : 'border-[#e5e7eb]'}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <ListingsThumbnail src={getListingThumbnail(listing)} alt={getListingTitle(listing)} size="lg" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[#101828]">{getListingTitle(listing)}</p>
            {getReadinessSummary(listing).blockers?.length ? <p className="mt-1 text-xs text-[#b42318]">{getReadinessSummary(listing).blockers[0]}</p> : null}
            <p className="mt-1 text-xs text-[#667085]">#{listing.id}{listing.sku ? ` · SKU ${listing.sku}` : ''}</p>
            <p className="mt-1 text-xs text-[#667085]">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</p>
            <ListingsMarketplacePreflightBadges
              listing={listing}
              getMarketplacePreflightSummary={getMarketplacePreflightSummary}
              getMarketplacePreflightStatus={getMarketplacePreflightStatus}
              getMarketplacePreflightTone={getMarketplacePreflightTone}
              getMarketplaceTopBlocker={getMarketplaceTopBlocker}
              getMarketplacePreflightAgeLabel={getMarketplacePreflightAgeLabel}
            />
          </div>
        </div>
        <input
          type="checkbox"
          checked={selected}
          onChange={(event) => {
            event.stopPropagation();
            toggleRow(listing.id);
          }}
          onClick={(event) => event.stopPropagation()}
          className="mt-1 h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb]"
        />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <StatusPill status={bucket} label={bucket.charAt(0).toUpperCase() + bucket.slice(1)} />
        {listing?.quality_summary?.status ? <span className="pp-chip">{String(listing.quality_summary.status).replaceAll('_', ' ')}</span> : null}
        {(explicitMarketplaces.length ? explicitMarketplaces : ['unassigned']).map((marketplace) => (
          <span key={`${listing.id}-${marketplace}`} className="pp-chip">{formatMarketplace(marketplace)}</span>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Price</p>
          <p className="mt-1 text-sm font-semibold text-[#101828]">${getListingPrice(listing)}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Qty</p>
          <p className="mt-1 text-sm font-semibold text-[#101828]">{listing.quantity ?? 1}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#667085]">Updated</p>
          <p className="mt-1 text-sm font-semibold text-[#101828]">{new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(listing.updated_at || listing.created_at || Date.now()))}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {(bucket === 'review' || listing.status === 'draft') ? (
          <Button variant="outline" size="sm" onClick={async (event) => { event.stopPropagation(); await approveListing(listing.id); }}>
            Approve
          </Button>
        ) : null}
        {isAmazonVineSource(listing) && !isArchivedListing(listing) ? (
          <Button variant="outline" size="sm" onClick={async (event) => { event.stopPropagation(); await archiveListing(listing.id); }}>
            Archive
          </Button>
        ) : null}
        <Button variant="danger" size="sm" onClick={async (event) => { event.stopPropagation(); if (!confirmDeleteListings(1)) return; await deleteListing(listing.id); }}>
          Delete
        </Button>
        <Button variant="outline" size="sm" onClick={(event) => { event.stopPropagation(); router.push(`/listings/${listing.id}?mode=preview`); }}>
          Preview/Edit
        </Button>
        <Button variant="outline" size="sm" onClick={(event) => { event.stopPropagation(); router.push(`/listings/${listing.id}`); }}>
          Full page
        </Button>
        {canPublish ? (
          <Button size="sm" onClick={async (event) => { publishSingleListing(listing, event); }} disabled={publishing[listing.id]}>
            {publishing[listing.id] ? 'Publishing…' : 'Publish'}
          </Button>
        ) : null}
        {bucket === 'failed' ? <p className="mt-1 text-xs text-[#b42318]">{getListingFailureMessage(listing) || errors[listing.id] || 'Publish failed'}</p> : null}
      </div>
    </div>
  );
}
