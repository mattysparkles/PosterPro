import Button from '../../ui/button';
import MetricCard from '../../ui/metric-card';
import SectionPanel from '../../ui/section-panel';

export default function ListingsRepairQueuePanel({
  repairQueueImageStatusFilter,
  setRepairQueueImageStatusFilter,
  refreshRepairQueue,
  repairQueueLoading,
  exportRepairQueueCsv,
  repairQueueReport,
  setSelectedListingId,
  runSingleListingPreflight,
  applyRepairAction,
  uploadActualPhotosToListing,
  approvePendingPhotos,
}) {
  return (
    <SectionPanel
      title="eBay launch repair queue"
      description="Unpublished eBay drafts under the launch threshold, grouped by actionable blockers so you can create the first safe live queue."
      action={
        <div className="flex flex-wrap gap-2">
          <select
            value={repairQueueImageStatusFilter}
            onChange={(event) => setRepairQueueImageStatusFilter(event.target.value)}
            className="h-9 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
          >
            <option value="all">All image states</option>
            <option value="no_images">No images</option>
            <option value="reference_only">Reference only</option>
            <option value="actual_pending_review">Actual pending review</option>
            <option value="actual_approved">Actual approved</option>
            <option value="actual_file_invalid">Actual file invalid</option>
          </select>
          <Button variant="outline" size="sm" onClick={refreshRepairQueue} disabled={repairQueueLoading}>
            {repairQueueLoading ? 'Refreshing…' : 'Refresh repair queue'}
          </Button>
          <Button variant="outline" size="sm" onClick={exportRepairQueueCsv} disabled={repairQueueLoading}>
            Export repair CSV
          </Button>
        </div>
      }
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Repair rows" value={repairQueueReport?.summary?.included || 0} detail="Unpublished eBay drafts with actionable blockers under the current launch threshold." />
        <MetricCard label="No images" value={repairQueueReport?.summary?.no_images || 0} detail="Drafts with no saved image metadata at all." />
        <MetricCard label="Missing actual photos" value={repairQueueReport?.summary?.missing_actual_photos || 0} detail="Drafts that still need operator-approved actual item photos." />
        <MetricCard label="Reference only" value={repairQueueReport?.summary?.reference_only_images || 0} detail="Drafts with source images but no actual approved item photos." />
        <MetricCard label="Actual pending" value={repairQueueReport?.summary?.actual_pending_review || 0} detail="Drafts with uploaded actual photos still waiting for operator approval." />
        <MetricCard label="Actual approved" value={repairQueueReport?.summary?.actual_approved || 0} detail="Drafts with approved actual photos already attached." />
        <MetricCard label="Image URL invalid" value={repairQueueReport?.summary?.invalid_image_url || 0} detail="Drafts whose actual item image files or publish URLs still fail validation." />
        <MetricCard label="Missing category" value={repairQueueReport?.summary?.missing_category || 0} detail="Drafts that still need an eBay category suggestion or operator category review." />
        <MetricCard label="Missing aspects" value={repairQueueReport?.summary?.missing_required_aspects || 0} detail="Drafts missing required eBay item specifics for their current category." />
        <MetricCard label="Most common blocker" value={repairQueueReport?.summary?.most_common_blocker || 'None'} detail="Top issue in the current repair queue." />
        <MetricCard label="Ready for image preflight" value={repairQueueReport?.summary?.ready_for_image_preflight || 0} detail="Drafts whose actual photos are approved and can move to full eBay preflight." />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {(repairQueueReport?.items || []).slice(0, 8).map((item) => (
          <div key={`repair-queue-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
            <p className="mt-1 text-xs text-[#667085]">#{item.listing_id} · ${Number(item.price || 0).toFixed(2)} · {String(item.current_preflight_status || 'unknown').replaceAll('_', ' ')}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {(item.blocker_codes || []).slice(0, 4).map((code) => (
                <span key={`${item.listing_id}-${code}`} className="pp-chip">{code}</span>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-[#475467]">
              <span className="pp-chip">Image status: {String(item.image_status || 'unknown').replaceAll('_', ' ')}</span>
              <span className="pp-chip">Approved actual: {Number(item.photo_counts?.actual_approved_images || 0)}</span>
              <span className="pp-chip">Pending actual: {Number(item.photo_counts?.actual_pending_images || 0)}</span>
              <span className="pp-chip">Reference: {Number(item.photo_counts?.reference_source_images || 0)}</span>
            </div>
            {item.suggested_category?.label ? (
              <p className="mt-2 text-xs text-[#475467]">Suggested category: {item.suggested_category.label}</p>
            ) : null}
            <p className="mt-2 text-xs text-[#475467]">Recommended repair: {item.recommended_next_repair_action || 'Review blockers'}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => setSelectedListingId(item.listing_id)}>
                Open editor
              </Button>
              <Button variant="outline" size="sm" onClick={() => runSingleListingPreflight(item.listing_id)}>
                Run preflight
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => applyRepairAction(item.listing_id, 'category')}
                disabled={!item.suggested_category?.can_apply}
              >
                Apply suggested category
              </Button>
              <label className="inline-flex cursor-pointer items-center rounded-[10px] border border-[#d0d5dd] bg-white px-3 py-2 text-sm font-medium text-[#101828]">
                Upload actual photos
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  multiple
                  className="hidden"
                  onChange={async (event) => {
                    const files = Array.from(event.target.files || []);
                    if (files.length) {
                      await uploadActualPhotosToListing(item.listing_id, files);
                    }
                    event.target.value = '';
                  }}
                />
              </label>
              {Number(item.photo_counts?.actual_pending_images || 0) > 0 ? (
                <Button variant="outline" size="sm" onClick={() => approvePendingPhotos(item.listing_id)}>
                  Approve actual photos
                </Button>
              ) : null}
              <Button variant="outline" size="sm" onClick={() => applyRepairAction(item.listing_id, 'images')}>
                Validate images
              </Button>
            </div>
          </div>
        ))}
        {!(repairQueueReport?.items || []).length ? (
          <div className="rounded-[12px] border border-dashed border-[#d0d5dd] bg-white p-4 text-sm text-[#667085]">
            Refresh the repair queue to find unpublished eBay drafts that are closest to launch-ready.
          </div>
        ) : null}
      </div>
    </SectionPanel>
  );
}
