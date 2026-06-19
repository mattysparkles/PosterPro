import Button from '../../ui/button';
import ListingsMarketplacePreflightBadges from '../workspace/ListingsMarketplacePreflightBadges';

export default function ListingsTitleCell({
  listing,
  getListingTitle,
  getReadinessSummary,
  getListingImageCount,
  isAmazonVineSource,
  getListingBucket,
  publishSingleListing,
  publishing,
  approveListing,
  deleteListing,
  confirmDeleteListings,
  archiveListing,
  isArchivedListing,
  router,
  getMarketplacePreflightSummary,
  getMarketplacePreflightStatus,
  getMarketplacePreflightTone,
  getMarketplaceTopBlocker,
  getMarketplacePreflightAgeLabel,
}) {
  return (
    <div>
      <p className="truncate font-medium text-[#101828]">{getListingTitle(listing)}</p>
      {getReadinessSummary(listing).blockers?.length ? (
        <p className="mt-1 text-xs text-[#b42318]">{getReadinessSummary(listing).blockers[0]}</p>
      ) : null}
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <p className="text-xs text-[#667085]">#{listing.id}</p>
        {listing.sku ? <span className="pp-chip">SKU {listing.sku}</span> : null}
        {isAmazonVineSource(listing) ? <span className="pp-chip">Vine</span> : null}
        <span className="pp-chip">{getListingImageCount(listing)} image{getListingImageCount(listing) === 1 ? '' : 's'}</span>
        {listing?.quality_summary?.score != null ? <span className="pp-chip">Quality {listing.quality_summary.score}</span> : null}
        {listing?.marketplace_data?.pricing_analysis?.price_confidence ? <span className="pp-chip">{Math.round(listing.marketplace_data.pricing_analysis.price_confidence * 100)}% pricing</span> : null}
        {listing.needs_review ? <span className="pp-chip">Needs Review</span> : null}
        {listing.restricted_review_required ? <span className="pp-chip">Restricted Review</span> : null}
        {listing.custom_labels?.includes('needs_photos') ? <span className="pp-chip">Image Missing</span> : null}
      </div>
      <ListingsMarketplacePreflightBadges
        listing={listing}
        getMarketplacePreflightSummary={getMarketplacePreflightSummary}
        getMarketplacePreflightStatus={getMarketplacePreflightStatus}
        getMarketplacePreflightTone={getMarketplacePreflightTone}
        getMarketplaceTopBlocker={getMarketplaceTopBlocker}
        getMarketplacePreflightAgeLabel={getMarketplacePreflightAgeLabel}
      />
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Button variant="outline" size="sm" onClick={(event) => { event.stopPropagation(); router.push(`/listings/${listing.id}?mode=preview`); }}>
          Preview/Edit
        </Button>
        <Button variant="outline" size="sm" onClick={(event) => { event.stopPropagation(); router.push(`/listings/${listing.id}`); }}>
          Full page
        </Button>
        {(getListingBucket(listing) === 'ready' || getListingBucket(listing) === 'drafts' || listing.status === 'draft') ? (
          <Button size="sm" onClick={(event) => { publishSingleListing(listing, event); }} disabled={publishing[listing.id]}>
            {publishing[listing.id] ? 'Publishing…' : 'Publish'}
          </Button>
        ) : null}
        {getListingBucket(listing) === 'review' || listing.status === 'draft' ? (
          <Button variant="outline" size="sm" onClick={async (event) => { event.stopPropagation(); await approveListing(listing.id); }}>
            Approve
          </Button>
        ) : null}
        <Button variant="danger" size="sm" onClick={async (event) => { event.stopPropagation(); if (!confirmDeleteListings(1)) return; await deleteListing(listing.id); }}>
          Delete
        </Button>
        {isAmazonVineSource(listing) && !isArchivedListing(listing) ? (
          <Button variant="outline" size="sm" onClick={async (event) => { event.stopPropagation(); await archiveListing(listing.id); }}>
            Archive
          </Button>
        ) : null}
      </div>
    </div>
  );
}
